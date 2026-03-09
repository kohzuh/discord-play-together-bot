import os
import math
import asyncio
from dataclasses import dataclass
from typing import Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN in .env")

if not STEAM_API_KEY:
    raise RuntimeError("Missing STEAM_API_KEY in .env")

FRIENDS_PER_PAGE = 25
MAX_COMPARE_FRIENDS = 5

MULTIPLAYER_CATEGORY_NAMES = {
    "multi-player",
    "multiplayer",
    "co-op",
    "online co-op",
    "online pvp",
    "pvp",
    "shared/split screen",
    "shared/split screen co-op",
    "shared/split screen pvp",
    "lan pvp",
    "lan co-op",
    "cross-platform multiplayer",
    "mmo",
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@dataclass
class FriendEntry:
    steamid: str
    personaname: str
    profileurl: str = ""


async def steam_get_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict,
    *,
    allow_401: bool = False,
) -> tuple[int, Optional[dict], Optional[str]]:
    try:
        async with session.get(url, params=params, timeout=20) as resp:
            status = resp.status

            if status == 401 and allow_401:
                return status, None, None

            if status != 200:
                text = await resp.text()
                return status, None, text[:500]

            data = await resp.json()
            return status, data, None

    except Exception as e:
        return 0, None, str(e)


async def get_friend_list(session: aiohttp.ClientSession, steamid: str) -> tuple[str, list[str], Optional[str]]:
    """
    Returns:
      ("ok", [steamid, ...], None)
      ("private", [], None)
      ("forbidden", [], error_text)
      ("error:<status>", [], error_text)
    """
    url = "https://api.steampowered.com/ISteamUser/GetFriendList/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": steamid,
        "relationship": "friend",
    }

    status, data, err = await steam_get_json(session, url, params, allow_401=True)

    if status == 401:
        return "private", [], None

    if status == 403:
        return "forbidden", [], err

    if status != 200 or not data:
        return f"error:{status}", [], err

    friends = data.get("friendslist", {}).get("friends", [])
    friend_ids = [f.get("steamid") for f in friends if f.get("steamid")]
    return "ok", friend_ids, None


async def get_player_summaries(session: aiohttp.ClientSession, steamids: list[str]) -> list[FriendEntry]:
    results: list[FriendEntry] = []
    if not steamids:
        return results

    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"

    for i in range(0, len(steamids), 100):
        chunk = steamids[i:i + 100]
        params = {
            "key": STEAM_API_KEY,
            "steamids": ",".join(chunk),
        }

        status, data, err = await steam_get_json(session, url, params)
        if status != 200 or not data:
            print(f"GetPlayerSummaries failed: status={status}, err={err}")
            continue

        players = data.get("response", {}).get("players", [])
        for p in players:
            results.append(
                FriendEntry(
                    steamid=p.get("steamid", ""),
                    personaname=p.get("personaname", p.get("steamid", "Unknown")),
                    profileurl=p.get("profileurl", ""),
                )
            )

    results.sort(key=lambda x: x.personaname.casefold())
    return results


async def get_owned_games(session: aiohttp.ClientSession, steamid: str) -> Optional[dict[int, str]]:
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": steamid,
        "include_appinfo": "true",
        "include_played_free_games": "true",
    }

    status, data, err = await steam_get_json(session, url, params)
    if status != 200 or not data:
        print(f"GetOwnedGames failed for {steamid}: status={status}, err={err}")
        return None

    games = data.get("response", {}).get("games", [])
    if not games:
        return {}

    out: dict[int, str] = {}
    for game in games:
        appid = game.get("appid")
        if appid is None:
            continue
        out[int(appid)] = game.get("name", f"App {appid}")
    return out


async def get_store_appdetails(session: aiohttp.ClientSession, appid: int) -> Optional[dict]:
    url = "https://store.steampowered.com/api/appdetails"
    params = {
        "appids": str(appid),
        "cc": "ca",
        "l": "en",
    }

    try:
        async with session.get(url, params=params, timeout=20) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"Store appdetails failed for {appid}: status={resp.status}, err={text[:300]}")
                return None
            payload = await resp.json()
    except Exception as e:
        print(f"Store metadata request failed for {appid}: {e}")
        return None

    block = payload.get(str(appid), {})
    if not block.get("success"):
        return None
    return block.get("data")


async def is_multiplayer_game(session: aiohttp.ClientSession, appid: int) -> bool:
    data = await get_store_appdetails(session, appid)
    if not data:
        return False

    categories = data.get("categories", [])
    category_names = {
        str(cat.get("description", "")).strip().casefold()
        for cat in categories
        if cat.get("description")
    }

    return any(name in category_names for name in MULTIPLAYER_CATEGORY_NAMES)


async def get_multiplayer_common_games(
    session: aiohttp.ClientSession,
    steamids: list[str],
) -> tuple[list[str], list[str]]:
    libraries: list[dict[int, str]] = []
    unavailable_users: list[str] = []

    for sid in steamids:
        games = await get_owned_games(session, sid)
        if games is None or len(games) == 0:
            unavailable_users.append(sid)
            continue
        libraries.append(games)

    if len(libraries) < 2:
        return [], unavailable_users

    common_appids = set(libraries[0].keys())
    for lib in libraries[1:]:
        common_appids &= set(lib.keys())

    if not common_appids:
        return [], unavailable_users

    common_games = sorted(
        [(appid, libraries[0][appid]) for appid in common_appids],
        key=lambda x: x[1].casefold()
    )

    multiplayer_names: list[str] = []
    sem = asyncio.Semaphore(8)

    async def check_one(appid: int, name: str):
        async with sem:
            if await is_multiplayer_game(session, appid):
                multiplayer_names.append(name)

    await asyncio.gather(*(check_one(appid, name) for appid, name in common_games))
    multiplayer_names.sort(key=str.casefold)
    return multiplayer_names, unavailable_users


def chunk_text_lines(lines: list[str], max_chars: int = 1800) -> list[str]:
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_chars:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    return chunks


class SteamIdModal(discord.ui.Modal, title="Enter your SteamID64"):
    steamid = discord.ui.TextInput(
        label="SteamID64",
        placeholder="Example: 7656119...",
        min_length=17,
        max_length=20,
        required=True,
    )

    def __init__(self):
        super().__init__(timeout=300)

    async def on_submit(self, interaction: discord.Interaction):
        steamid_value = str(self.steamid).strip()

        if not steamid_value.isdigit():
            await interaction.response.send_message(
                "That does not look like a numeric SteamID64.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        async with aiohttp.ClientSession() as session:
            owner_summary = await get_player_summaries(session, [steamid_value])
            owner_name = owner_summary[0].personaname if owner_summary else steamid_value

            status, friend_ids, err = await get_friend_list(session, steamid_value)

            if status == "private":
                await interaction.followup.send(
                    "That user's Steam friends list is private.",
                    ephemeral=True,
                )
                return

            if status == "forbidden":
                await interaction.followup.send(
                    "Steam rejected the request with 403. This usually means the API key is invalid or not accepted.",
                    ephemeral=True,
                )
                return

            if status != "ok":
                await interaction.followup.send(
                    f"I couldn't fetch the friends list from Steam.\nStatus: `{status}`\nError: `{err or 'None'}`",
                    ephemeral=True,
                )
                return

            if not friend_ids:
                await interaction.followup.send(
                    "No friends were returned for that SteamID.",
                    ephemeral=True,
                )
                return

            friends = await get_player_summaries(session, friend_ids)

        if not friends:
            await interaction.followup.send(
                "I found friend IDs, but couldn't resolve any display names.",
                ephemeral=True,
            )
            return

        view = FriendPickerView(
            requester_id=interaction.user.id,
            owner_steamid=steamid_value,
            owner_name=owner_name,
            friends=friends,
        )
        message = await interaction.followup.send(
            content=view.render_header(),
            view=view,
            ephemeral=True,
        )
        view.message = message


class FriendSelect(discord.ui.Select):
    def __init__(self, parent_view: "FriendPickerView"):
        self.parent_view = parent_view
        options = parent_view.get_page_options()

        super().__init__(
            placeholder="Choose friends to compare with",
            min_values=0,
            max_values=min(MAX_COMPARE_FRIENDS, len(options)) if options else 1,
            options=options or [
                discord.SelectOption(label="No friends on this page", value="__none__", default=True)
            ],
            disabled=(len(options) == 0),
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        page_friends = self.parent_view.get_page_friends()
        page_ids = {f.steamid for f in page_friends}

        self.parent_view.selected_ids -= page_ids

        for value in self.values:
            if value != "__none__":
                self.parent_view.selected_ids.add(value)

        self.parent_view.refresh_components()
        await interaction.response.edit_message(
            content=self.parent_view.render_header(),
            view=self.parent_view,
        )


class FriendPickerView(discord.ui.View):
    def __init__(
        self,
        requester_id: int,
        owner_steamid: str,
        owner_name: str,
        friends: list[FriendEntry],
    ):
        super().__init__(timeout=600)
        self.requester_id = requester_id
        self.owner_steamid = owner_steamid
        self.owner_name = owner_name
        self.friends = friends
        self.page = 0
        self.selected_ids: set[str] = set()
        self.message: Optional[discord.Message] = None
        self.refresh_components()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self.friends) / FRIENDS_PER_PAGE))

    def get_page_friends(self) -> list[FriendEntry]:
        start = self.page * FRIENDS_PER_PAGE
        end = start + FRIENDS_PER_PAGE
        return self.friends[start:end]

    def get_page_options(self) -> list[discord.SelectOption]:
        options = []
        for friend in self.get_page_friends():
            options.append(
                discord.SelectOption(
                    label=friend.personaname[:100],
                    value=friend.steamid,
                    description=friend.steamid,
                    default=(friend.steamid in self.selected_ids),
                )
            )
        return options

    def render_header(self) -> str:
        selected_names = [
            f.personaname for f in self.friends if f.steamid in self.selected_ids
        ]

        lines = [
            f"Steam owner: **{self.owner_name}** (`{self.owner_steamid}`)",
            f"Friends found: **{len(self.friends)}**",
            f"Page **{self.page + 1}/{self.page_count}**",
            f"Selected friends: **{len(self.selected_ids)} / {MAX_COMPARE_FRIENDS}**",
        ]

        if selected_names:
            lines.append("Current selection:")
            lines.extend(f"- {name}" for name in selected_names[:10])

        lines.append("")
        lines.append("Pick up to 5 friends, then click **Compare multiplayer common games**.")
        return "\n".join(lines)

    def refresh_components(self):
        self.clear_items()
        self.add_item(FriendSelect(self))
        self.prev_button.disabled = (self.page == 0)
        self.next_button.disabled = (self.page >= self.page_count - 1)
        self.compare_button.disabled = (len(self.selected_ids) == 0 or len(self.selected_ids) > MAX_COMPARE_FRIENDS)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.compare_button)
        self.add_item(self.clear_button)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return
        if self.page > 0:
            self.page -= 1
        self.refresh_components()
        await interaction.response.edit_message(
            content=self.render_header(),
            view=self,
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return
        if self.page < self.page_count - 1:
            self.page += 1
        self.refresh_components()
        await interaction.response.edit_message(
            content=self.render_header(),
            view=self,
        )

    @discord.ui.button(label="Compare multiplayer common games", style=discord.ButtonStyle.primary)
    async def compare_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        if not self.selected_ids:
            await interaction.response.send_message("Select at least one friend first.", ephemeral=True)
            return

        if len(self.selected_ids) > MAX_COMPARE_FRIENDS:
            await interaction.response.send_message("Please select 5 or fewer friends.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        chosen = [f for f in self.friends if f.steamid in self.selected_ids]
        compare_ids = [self.owner_steamid] + [f.steamid for f in chosen]

        async with aiohttp.ClientSession() as session:
            multiplayer_games, unavailable_ids = await get_multiplayer_common_games(session, compare_ids)
            unavailable_names = []
            if unavailable_ids:
                summaries = await get_player_summaries(session, unavailable_ids)
                name_map = {s.steamid: s.personaname for s in summaries}
                unavailable_names = [name_map.get(sid, sid) for sid in unavailable_ids]

        lines = [
            f"Compared owner **{self.owner_name}** with:",
        ]
        lines.extend(f"- {f.personaname}" for f in chosen)

        if unavailable_names:
            lines.append("")
            lines.append("Libraries unavailable or empty/private:")
            lines.extend(f"- {name}" for name in unavailable_names)

        lines.append("")
        lines.append(f"Common multiplayer games: **{len(multiplayer_games)}**")

        if multiplayer_games:
            lines.extend(f"- {name}" for name in multiplayer_games[:100])
            if len(multiplayer_games) > 100:
                lines.append(f"...and {len(multiplayer_games) - 100} more.")
        else:
            lines.append("No common multiplayer games found among users with visible libraries.")

        for chunk in chunk_text_lines(lines):
            await interaction.followup.send(chunk, ephemeral=True)

    @discord.ui.button(label="Clear selection", style=discord.ButtonStyle.danger)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        self.selected_ids.clear()
        self.refresh_components()
        await interaction.response.edit_message(
            content=self.render_header(),
            view=self,
        )


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user} | synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Slash command sync failed: {e}")


@bot.tree.command(name="comparefriends", description="Choose Steam friends and find common multiplayer games.")
async def comparefriends(interaction: discord.Interaction):
    await interaction.response.send_modal(SteamIdModal())


bot.run(DISCORD_TOKEN)
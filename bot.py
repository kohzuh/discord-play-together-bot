import os
import math
import asyncio
import random
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from yarl import URL

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN in .env")

if not STEAM_API_KEY:
    raise RuntimeError("Missing STEAM_API_KEY in .env")

FRIENDS_PER_PAGE = 25
MAX_COMPARE_FRIENDS = 5
MAX_USER_SEARCH_RESULTS = 3
ROULETTE_UPVOTE = "👍"
ROULETTE_DOWNVOTE = "👎"

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
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)


@dataclass
class FriendEntry:
    steamid: str
    personaname: str
    profileurl: str = ""


@dataclass
class SearchUserEntry:
    steamid: str
    profile_label: str
    avatar: str = ""
    personaname: str = ""
    profileurl: str = ""


@dataclass
class GameChoice:
    appid: int
    name: str
    short_description: str = ""
    store_url: str = ""
    header_image: str = ""
    capsule_image: str = ""


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


async def search_community_users(session: aiohttp.ClientSession, username: str) -> list[SearchUserEntry]:
    try:
        async with session.get("https://steamcommunity.com/search/users/", timeout=20) as resp:
            resp.raise_for_status()

        cookies = session.cookie_jar.filter_cookies(URL("https://steamcommunity.com"))
        session_cookie = cookies.get("sessionid")
        if not session_cookie:
            print("Steam community search did not return a sessionid cookie.")
            return []

        params = {
            "text": username,
            "filter": "users",
            "sessionid": session_cookie.value,
            "steamid_user": "false",
            "page": 1,
        }

        async with session.get(
            "https://steamcommunity.com/search/SearchCommunityAjax",
            params=params,
            timeout=20,
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
    except Exception as e:
        print(f"Steam community search failed: {e}")
        return []

    html = payload.get("html", "")
    if not html:
        return []

    profile_links = re.findall(
        r'<a href="https://steamcommunity\.com/(id|profiles)/([^"/]+)[^"]*"[^>]*><img src="([^"]+)"',
        html,
    )
    miniprofiles = re.findall(r'data-miniprofile="(\d+)"', html)

    steam64_base = 76561197960265728
    results: list[SearchUserEntry] = []

    for i, (profile_type, value, avatar) in enumerate(profile_links[:MAX_USER_SEARCH_RESULTS]):
        if profile_type == "profiles":
            steamid = value
            profile_label = f"profiles/{value}"
        else:
            if i >= len(miniprofiles):
                continue
            steamid = str(steam64_base + int(miniprofiles[i]))
            profile_label = f"id/{value}"

        results.append(
            SearchUserEntry(
                steamid=steamid,
                profile_label=profile_label,
                avatar=avatar,
            )
        )

    if results:
        summaries = await get_player_summaries(session, [result.steamid for result in results if result.steamid.isdigit()])
        summary_map = {entry.steamid: entry for entry in summaries}
        for result in results:
            summary = summary_map.get(result.steamid)
            if summary:
                result.personaname = summary.personaname
                result.profileurl = summary.profileurl

    return results


async def resolve_user_input_to_steamid(
    session: aiohttp.ClientSession,
    user_input: str,
) -> tuple[str, Optional[str], list[SearchUserEntry], Optional[str]]:
    raw = user_input.strip()

    if raw.isdigit():
        if 17 <= len(raw) <= 20:
            return "direct", raw, [], None
        return "error", None, [], "That numeric input does not look like a SteamID64."

    matches = await search_community_users(session, raw)
    if not matches:
        return "error", None, [], "I couldn't find any Steam users matching that name."

    if len(matches) == 1:
        return "direct", matches[0].steamid, matches, None

    return "choose", None, matches, None


async def get_friend_list(session: aiohttp.ClientSession, steamid: str) -> tuple[str, list[str], Optional[str]]:
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


async def get_game_choice(session: aiohttp.ClientSession, appid: int, fallback_name: str) -> Optional[GameChoice]:
    data = await get_store_appdetails(session, appid)
    if not data:
        return None

    categories = data.get("categories", [])
    category_names = {
        str(cat.get("description", "")).strip().casefold()
        for cat in categories
        if cat.get("description")
    }
    if not any(name in category_names for name in MULTIPLAYER_CATEGORY_NAMES):
        return None

    return GameChoice(
        appid=appid,
        name=data.get("name", fallback_name),
        short_description=data.get("short_description", ""),
        store_url=data.get("website") or f"https://store.steampowered.com/app/{appid}/",
        header_image=data.get("header_image", ""),
        capsule_image=data.get("capsule_image", ""),
    )


async def get_multiplayer_common_games(
    session: aiohttp.ClientSession,
    steamids: list[str],
) -> tuple[list[str], list[str]]:
    choices, unavailable_users = await get_multiplayer_common_game_choices(session, steamids)
    names = sorted([choice.name for choice in choices], key=str.casefold)
    return names, unavailable_users


async def get_multiplayer_common_game_choices(
    session: aiohttp.ClientSession,
    steamids: list[str],
) -> tuple[list[GameChoice], list[str]]:
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

    multiplayer_choices: list[GameChoice] = []
    sem = asyncio.Semaphore(8)

    async def check_one(appid: int, name: str):
        async with sem:
            choice = await get_game_choice(session, appid, name)
            if choice:
                multiplayer_choices.append(choice)

    await asyncio.gather(*(check_one(appid, name) for appid, name in common_games))
    multiplayer_choices.sort(key=lambda x: x.name.casefold())
    return multiplayer_choices, unavailable_users


async def start_friend_picker(
    interaction: discord.Interaction,
    steamid_value: str,
    mode: str,
):
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
                "No friends were returned for that Steam account.",
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
        mode=mode,
        channel=interaction.channel,
        guild=interaction.guild,
    )
    message = await interaction.followup.send(
        content=view.render_header(),
        view=view,
        ephemeral=True,
    )
    view.message = message


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


def build_roulette_embed(game: GameChoice, participant_names: list[str], threshold: int, round_number: int, total_candidates: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"🎲 Roulette Pick: {game.name}",
        url=game.store_url,
        description=game.short_description or "No description was provided by Steam.",
    )
    embed.add_field(name="Store page", value=game.store_url, inline=False)
    embed.add_field(name="Players", value="\n".join(f"- {name}" for name in participant_names[:10]), inline=False)
    embed.add_field(name="Vote rule", value=f"More than half must agree: **{threshold}+** 👍 or 👎", inline=False)
    embed.set_footer(text=f"Round {round_number} of up to {total_candidates} shared multiplayer games")
    if game.header_image:
        embed.set_image(url=game.header_image)
    if game.capsule_image:
        embed.set_thumbnail(url=game.capsule_image)
    return embed


async def run_roulette_vote(
    channel: Optional[discord.abc.Messageable],
    owner_name: str,
    chosen_friends: list[FriendEntry],
    game_choices: list[GameChoice],
    participant_count: int,
):
    if channel is None:
        return

    threshold = participant_count // 2 + 1
    participant_names = [owner_name] + [friend.personaname for friend in chosen_friends]
    remaining_choices = list(game_choices)
    random.shuffle(remaining_choices)
    round_number = 0

    while remaining_choices:
        round_number += 1
        game = remaining_choices.pop(0)
        embed = build_roulette_embed(game, participant_names, threshold, round_number, len(game_choices))
        message = await channel.send(
            content="Steam roulette spun up a candidate. React below to keep it or reroll it.",
            embed=embed,
        )
        await message.add_reaction(ROULETTE_UPVOTE)
        await message.add_reaction(ROULETTE_DOWNVOTE)

        while True:
            try:
                reaction, user = await bot.wait_for(
                    "reaction_add",
                    timeout=1800,
                    check=lambda r, u: (
                        r.message.id == message.id
                        and not u.bot
                        and str(r.emoji) in {ROULETTE_UPVOTE, ROULETTE_DOWNVOTE}
                    ),
                )
            except asyncio.TimeoutError:
                await channel.send(
                    f"Roulette voting timed out on **{game.name}**. Run `/roulettefriends` again to restart."
                )
                return

            try:
                refreshed = await channel.fetch_message(message.id)
            except Exception:
                refreshed = message

            up_count = 0
            down_count = 0
            for reaction_obj in refreshed.reactions:
                emoji_text = str(reaction_obj.emoji)
                adjusted_count = max(0, reaction_obj.count - 1)
                if emoji_text == ROULETTE_UPVOTE:
                    up_count = adjusted_count
                elif emoji_text == ROULETTE_DOWNVOTE:
                    down_count = adjusted_count

            if up_count >= threshold:
                await channel.send(f"✅ The group locked in **{game.name}**. Have fun!")
                return

            if down_count >= threshold:
                try:
                    await message.delete()
                except Exception:
                    pass

                if remaining_choices:
                    break
                await channel.send("❌ The group voted down every shared multiplayer game I could find.")
                return


class SteamUserInputModal(discord.ui.Modal, title="Enter Steam username or SteamID64"):
    steam_input = discord.ui.TextInput(
        label="Steam username or SteamID64",
        placeholder="...",
        min_length=2,
        max_length=50,
        required=True,
    )

    def __init__(self, mode: str):
        super().__init__(timeout=300)
        self.mode = mode

    async def on_submit(self, interaction: discord.Interaction):
        user_input = str(self.steam_input).strip()
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with aiohttp.ClientSession() as session:
            mode, steamid_value, matches, error_message = await resolve_user_input_to_steamid(session, user_input)

        if mode == "error":
            await interaction.followup.send(error_message or "I couldn't resolve that Steam user.", ephemeral=True)
            return

        if mode == "direct" and steamid_value:
            await start_friend_picker(interaction, steamid_value, self.mode)
            return

        if mode == "choose":
            chooser = SearchResultPickerView(interaction.user.id, user_input, matches, self.mode)
            message = await interaction.followup.send(
                chooser.render_message(),
                embeds=chooser.build_embeds(),
                view=chooser,
                ephemeral=True,
            )
            chooser.message = message
            return

        await interaction.followup.send("Something went wrong while resolving that Steam account.", ephemeral=True)


class SearchResultSelect(discord.ui.Select):
    def __init__(self, parent_view: "SearchResultPickerView"):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label=(result.personaname or result.profile_label)[:100],
                value=result.steamid,
                description=result.steamid[:100],
            )
            for result in parent_view.matches
        ]

        super().__init__(
            placeholder="Pick the correct Steam account",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.requester_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return

        chosen_steamid = self.values[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        await start_friend_picker(interaction, chosen_steamid, self.parent_view.mode)


class SearchResultPickerView(discord.ui.View):
    def __init__(self, requester_id: int, search_text: str, matches: list[SearchUserEntry], mode: str):
        super().__init__(timeout=300)
        self.requester_id = requester_id
        self.search_text = search_text
        self.matches = matches
        self.mode = mode
        self.message: Optional[discord.Message] = None
        self.add_item(SearchResultSelect(self))

    def render_message(self) -> str:
        lines = [
            f"I found multiple Steam users for **{self.search_text}**.",
            "Pick the correct account from the dropdown below:",
            "",
        ]
        for i, result in enumerate(self.matches, start=1):
            lines.append(f"{i}. `{result.steamid}` - `{result.profile_label}`")
        return "\n".join(lines)

    def build_embeds(self) -> list[discord.Embed]:
        embeds: list[discord.Embed] = []
        for i, result in enumerate(self.matches, start=1):
            title = result.personaname or result.profile_label or result.steamid
            embed = discord.Embed(title=f"Result {i}: {title}")
            embed.add_field(name="SteamID64", value=f"`{result.steamid}`", inline=False)
            embed.add_field(name="Profile path", value=f"`{result.profile_label}`", inline=False)
            if result.profileurl:
                embed.add_field(name="Profile URL", value=result.profileurl, inline=False)
            if result.avatar:
                embed.set_thumbnail(url=result.avatar)
            embeds.append(embed)
        return embeds

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


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
        mode: str,
        channel: Optional[discord.abc.Messageable],
        guild: Optional[discord.Guild],
    ):
        super().__init__(timeout=600)
        self.requester_id = requester_id
        self.owner_steamid = owner_steamid
        self.owner_name = owner_name
        self.friends = friends
        self.mode = mode
        self.channel = channel
        self.guild = guild
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
        selected_on_page = self.selected_ids
        options: list[discord.SelectOption] = []
        for friend in self.get_page_friends():
            options.append(
                discord.SelectOption(
                    label=friend.personaname[:100],
                    value=friend.steamid,
                    description=friend.steamid[:100],
                    default=friend.steamid in selected_on_page,
                )
            )
        return options

    def render_header(self) -> str:
        selected_names = [
            f.personaname for f in self.friends if f.steamid in self.selected_ids
        ]

        action_label = "Compare multiplayer common games" if self.mode == "compare" else "Start roulette"
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
        lines.append(f"Pick up to 5 friends, then click **{action_label}**.")
        if self.mode == "roulette":
            lines.append("Roulette will post the game publicly in this channel, but this account-picking menu stays private.")
        return "\n".join(lines)

    def refresh_components(self):
        self.clear_items()
        self.add_item(FriendSelect(self))
        self.prev_button.disabled = (self.page == 0)
        self.next_button.disabled = (self.page >= self.page_count - 1)
        self.run_button.disabled = (len(self.selected_ids) == 0 or len(self.selected_ids) > MAX_COMPARE_FRIENDS)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.run_button)
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

    @discord.ui.button(label="Run", style=discord.ButtonStyle.primary)
    async def run_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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

            if self.mode == "roulette":
                game_choices, _ = await get_multiplayer_common_game_choices(session, compare_ids)
            else:
                game_choices = []

        if self.mode == "compare":
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
            return

        if unavailable_names:
            await interaction.followup.send(
                "Some selected libraries were unavailable or empty/private:\n" + "\n".join(f"- {name}" for name in unavailable_names),
                ephemeral=True,
            )

        if not game_choices:
            await interaction.followup.send(
                "I couldn't find any shared multiplayer games for roulette among the selected visible libraries.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Roulette is live. I found **{len(game_choices)}** shared multiplayer games and posted the first candidate publicly in this channel.",
            ephemeral=True,
        )
        asyncio.create_task(
            run_roulette_vote(
                self.channel,
                self.owner_name,
                chosen,
                game_choices,
                participant_count=len(compare_ids),
            )
        )

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


@bot.tree.command(name="compare", description="Choose Steam friends and find common multiplayer games.")
async def comparefriends(interaction: discord.Interaction):
    await interaction.response.send_modal(SteamUserInputModal("compare"))


@bot.tree.command(name="roulette", description="Choose Steam friends, then publicly vote on a random shared multiplayer game.")
async def roulettefriends(interaction: discord.Interaction):
    await interaction.response.send_modal(SteamUserInputModal("roulette"))


bot.run(DISCORD_TOKEN)
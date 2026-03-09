import os
import re
import aiohttp
import discord
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from discord.ext import commands
from urllib.parse import quote_plus

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

STEAM_ID_URL_RE = re.compile(r"steamcommunity\.com/profiles/(\d+)", re.IGNORECASE)
STEAM_VANITY_URL_RE = re.compile(r"steamcommunity\.com/id/([^/?#]+)", re.IGNORECASE)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DiscordSteamBot/1.0)"
}


def parse_entries(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def classify_input(entry: str) -> tuple[str, str]:
    m = STEAM_ID_URL_RE.search(entry)
    if m:
        return ("steamid", m.group(1))

    m = STEAM_VANITY_URL_RE.search(entry)
    if m:
        return ("vanity", m.group(1))

    if entry.isdigit() and len(entry) >= 16:
        return ("steamid", entry)

    return ("unknown_text", entry)


async def resolve_vanity(session: aiohttp.ClientSession, vanity: str) -> str | None:
    url = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
    params = {
        "key": STEAM_API_KEY,
        "vanityurl": vanity,
    }
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        response = data.get("response", {})
        if response.get("success") == 1:
            return response.get("steamid")
        return None


async def get_player_summary(session: aiohttp.ClientSession, steamid: str) -> dict | None:
    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
    params = {
        "key": STEAM_API_KEY,
        "steamids": steamid,
    }
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        players = data.get("response", {}).get("players", [])
        return players[0] if players else None


async def get_owned_games(session: aiohttp.ClientSession, steamid: str) -> list[dict] | None:
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": steamid,
        "include_appinfo": "true",
        "include_played_free_games": "true",
    }
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
        return data.get("response", {}).get("games", [])


async def search_steam_by_alias(session: aiohttp.ClientSession, query: str, max_candidates: int = 5) -> list[str]:
    """
    Returns profile URLs from Steam Community search results.
    """
    search_url = f"https://steamcommunity.com/search/users/?text={quote_plus(query)}"

    async with session.get(search_url, headers=HEADERS) as resp:
        if resp.status != 200:
            return []

        html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")

    profile_links = []
    seen = set()

    # Grab all anchors and keep only profile/id links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        if re.match(r"^https://steamcommunity\.com/profiles/\d+/?$", href, re.IGNORECASE) or \
           re.match(r"^https://steamcommunity\.com/id/[^/?#]+/?$", href, re.IGNORECASE):

            normalized = href.rstrip("/")
            if normalized not in seen:
                seen.add(normalized)
                profile_links.append(normalized)

        if len(profile_links) >= max_candidates:
            break

    return profile_links


def score_candidate(query: str, personaname: str, profileurl: str) -> tuple[int, int]:
    """
    Higher is better.
    """
    q = query.strip().casefold()
    p = (personaname or "").strip().casefold()
    u = (profileurl or "").strip().casefold()

    score = 0

    if p == q:
        score += 100
    elif p.startswith(q):
        score += 60
    elif q in p:
        score += 35

    if f"/id/{q}" in u:
        score += 80

    # smaller length difference is slightly better
    closeness = -abs(len(p) - len(q))
    return (score, closeness)


async def resolve_text_input(session: aiohttp.ClientSession, text: str) -> dict | None:
    """
    Tries:
    1) vanity resolve
    2) alias/display-name community search fallback
    Returns:
      {
        "steamid": ...,
        "display_name": ...,
        "profileurl": ...,
        "method": "vanity" | "alias_search"
      }
    """
    # First assume it might be a custom vanity URL name
    steamid = await resolve_vanity(session, text)
    if steamid:
        summary = await get_player_summary(session, steamid)
        if summary:
            return {
                "steamid": steamid,
                "display_name": summary.get("personaname", text),
                "profileurl": summary.get("profileurl", ""),
                "method": "vanity",
            }

    # Fallback: search Steam Community by alias/display name
    candidate_urls = await search_steam_by_alias(session, text, max_candidates=5)
    if not candidate_urls:
        return None

    best = None
    best_score = None

    for url in candidate_urls:
        kind, value = classify_input(url)

        candidate_steamid = None
        if kind == "steamid":
            candidate_steamid = value
        elif kind == "vanity":
            candidate_steamid = await resolve_vanity(session, value)

        if not candidate_steamid:
            continue

        summary = await get_player_summary(session, candidate_steamid)
        if not summary:
            continue

        personaname = summary.get("personaname", "")
        profileurl = summary.get("profileurl", url)
        score = score_candidate(text, personaname, profileurl)

        if best is None or score > best_score:
            best = {
                "steamid": candidate_steamid,
                "display_name": personaname or text,
                "profileurl": profileurl,
                "method": "alias_search",
            }
            best_score = score

    return best


async def resolve_any_user(session: aiohttp.ClientSession, entry: str) -> dict | None:
    kind, value = classify_input(entry)

    if kind == "steamid":
        summary = await get_player_summary(session, value)
        if not summary:
            return None
        return {
            "steamid": value,
            "display_name": summary.get("personaname", entry),
            "profileurl": summary.get("profileurl", ""),
            "method": "steamid",
        }

    if kind == "vanity":
        steamid = await resolve_vanity(session, value)
        if steamid:
            summary = await get_player_summary(session, steamid)
            if summary:
                return {
                    "steamid": steamid,
                    "display_name": summary.get("personaname", entry),
                    "profileurl": summary.get("profileurl", ""),
                    "method": "vanity",
                }

        # If a URL vanity failed, also try text fallback on the vanity token
        return await resolve_text_input(session, value)

    return await resolve_text_input(session, value)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.command()
async def commongames(ctx, *, names: str):
    if not STEAM_API_KEY:
        await ctx.send("Steam API key is missing from .env")
        return

    entries = parse_entries(names)
    if len(entries) < 2:
        await ctx.send("Please provide at least 2 Steam users.")
        return

    if len(entries) > 8:
        await ctx.send("Please send 8 or fewer users at a time.")
        return

    await ctx.send(
        "Checking profiles and comparing libraries...\n"
        "You can send profile links, SteamIDs, custom profile names, or display names."
    )

    invalid_users = []
    unavailable_libraries = []
    resolved_users = []

    async with aiohttp.ClientSession() as session:
        for entry in entries:
            resolved = await resolve_any_user(session, entry)

            if not resolved:
                invalid_users.append(entry)
                continue

            steamid = resolved["steamid"]
            display_name = resolved["display_name"]
            method = resolved["method"]

            games = await get_owned_games(session, steamid)

            if games is None or len(games) == 0:
                unavailable_libraries.append(f"{entry} ({display_name})")
                continue

            games_map = {}
            for game in games:
                appid = game.get("appid")
                name = game.get("name", f"App {appid}")
                if appid is not None:
                    games_map[appid] = name

            resolved_users.append({
                "original": entry,
                "steamid": steamid,
                "display_name": display_name,
                "profileurl": resolved.get("profileurl", ""),
                "method": method,
                "games_map": games_map,
            })

    if len(resolved_users) < 2:
        parts = []
        if invalid_users:
            parts.append("Could not resolve:\n" + "\n".join(f"- {u}" for u in invalid_users))
        if unavailable_libraries:
            parts.append(
                "Resolved, but library unavailable/private:\n"
                + "\n".join(f"- {u}" for u in unavailable_libraries)
            )
        parts.append("Need at least 2 users with visible game libraries to compare.")
        await ctx.send("\n\n".join(parts))
        return

    common_appids = set(resolved_users[0]["games_map"].keys())
    for user in resolved_users[1:]:
        common_appids &= set(user["games_map"].keys())

    common_games = sorted(
        [resolved_users[0]["games_map"][appid] for appid in common_appids],
        key=str.lower
    )

    matched_users_lines = []
    for user in resolved_users:
        via = {
            "steamid": "SteamID",
            "vanity": "custom URL",
            "alias_search": "name search fallback",
        }.get(user["method"], user["method"])

        matched_users_lines.append(
            f"- `{user['original']}` → **{user['display_name']}** ({via})"
        )

    parts = [
        "Matched users:\n" + "\n".join(matched_users_lines)
    ]

    if invalid_users:
        parts.append("Could not resolve:\n" + "\n".join(f"- {u}" for u in invalid_users))

    if unavailable_libraries:
        parts.append(
            "Resolved, but library unavailable/private:\n"
            + "\n".join(f"- {u}" for u in unavailable_libraries)
        )

    if common_games:
        preview = common_games[:50]
        parts.append(
            f"Common games found: **{len(common_games)}**\n"
            + "\n".join(f"- {game}" for game in preview)
        )
        if len(common_games) > 50:
            parts.append(f"...and {len(common_games) - 50} more.")
    else:
        parts.append("No common games found among the users with visible libraries.")

    await ctx.send("\n\n".join(parts))


bot.run(DISCORD_TOKEN)
import os
import requests
import json

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

uIDs=(76561198148289427, 76561198262549310, 76561198834362035, 76561199077420855)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

MULTIPLAYER_CATEGORY_NAMES = (
    "Multi-player",
    "Multiplayer",
    "Co-op",
    "Online Co-op",
    "Online PvP",
    "cross-platform multiplayer",
    "mmo"
)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def get_app_categories(appid: str):
    url = "https://store.steampowered.com/api/appdetails"
    params = {
        "appids": str(appid),
        "cc": "ca",
        "l": "en",
    }
    try:
        response = requests.get(url, params=params, timeout=10).json()
        categories = set(item["description"] for item in response[str(appid)]["data"]["categories"])

        return categories
    except:
        return set()

def is_multiplayer_game(appid: str):
    categories = get_app_categories(appid)

    return any(name in categories for name in MULTIPLAYER_CATEGORY_NAMES)

def get_friend_list(steamid: str):
    url = "https://api.steampowered.com/ISteamUser/GetFriendList/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": steamid,
        "relationship": "friend",
    }
    try:
        response = requests.get(url, params=params, timeout=10).json()

        friends_list = [item["steamid"] for item in response["friendslist"]["friends"]]
        return friends_list
    except:
        return []

def get_library(steamid: str):
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": steamid,
        "include_appinfo": "true",
        "include_played_free_games": "true",
    }
    try:
        response = requests.get(url, params=params, timeout=10).json()
        game_list = set((item["appid"], item["name"]) for item in response["response"]["games"])
    
        return game_list
    except:
        return set()

def get_shared_library(users):
    libraries=[]

    for user in users:
        library = get_library(user)
        if library:
            libraries.append(get_library(library))

    shared_library = set.intersection(*libraries) if libraries else set()

    multi_library=set()
    for game in shared_library:
        if is_multiplayer_game(game[0]):
            multi_library.add(game)

    return multi_library

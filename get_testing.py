import requests
import json

STEAM_API_KEY="50A0C901C8BE9578492C42A42785BC64"
MULTIPLAYER_CATEGORY_NAMES = (
    "Multi-player",
    "Multiplayer",
    "Co-op",
    "Online Co-op",
    "Online PvP",
    "cross-platform multiplayer",
    "mmo"
)

def get_app_categories(appid: str):
    url = "https://store.steampowered.com/api/appdetails"
    params = {
        "appids": str(appid),
        "cc": "ca",
        "l": "en",
    }

    response = requests.get(url,params).json()

    categories = set(item["description"] for item in response[str(appid)]["data"]["categories"])

    return categories

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
    response = requests.get(url, params).json()

    friends_list = [item["steamid"] for item in response["friendslist"]["friends"]]

    return friends_list

def get_library(steamid: str):
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": STEAM_API_KEY,
        "steamid": steamid,
        "include_appinfo": "true",
        "include_played_free_games": "true",
    }
    response = requests.get(url,params).json()

    game_list = set((item["appid"], item["name"]) for item in response["response"]["games"])

    return game_list

def get_shared_library(users):
    libraries=[]
    for user in users:
        libraries.append(get_library(user))
    shared_library = set.intersection(*libraries) if libraries else set()

    multi_library=set()
    for game in shared_library:
        if is_multiplayer_game(game[0]):
            multi_library.add(game)

    return shared_library

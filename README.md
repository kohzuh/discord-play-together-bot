# discord-play-together-bot# Steam Friends Common Multiplayer Discord Bot

A Discord bot that lets a user enter their **SteamID64**, choose from their **Steam friends list**, and then find **common games** that are marked as **multiplayer** in Steam store categories.

## What it does

- Opens a Discord modal for the user to enter their **SteamID64**, or **Steam display name**
- Fetches that user's **Steam friends list**
- Displays friends in a paged select menu
- Lets the user choose up to **5 friends**
- Compares owned games between the user and selected friends
- Filters the shared games to only show **multiplayer** titles

## Features

- Slash command based
- Discord modal input
- Paged friend selection menu
- Multiplayer filtering using Steam store categories
- Handles private or unavailable libraries
- Basic error handling for Steam/store API failures

---

## Requirements

- Python 3.10+
- A Discord bot token
- A Steam Web API key

---

## Usage

1. [Click here to invite the bot to your server](https://discord.com/oauth2/authorize?client_id=1480349911651450991)
2. Run the `/compare` command in any channel the bot can access.
3. Enter either:
   - your **SteamID64**
   - or your **Steam username**
4. If you enter a username and multiple matches are found, the bot will show the first few results so you can choose the correct account.
5. Once your account is identified, select up to **5 friends** from your Steam friends list.
6. Press **Run** and the bot will return the multiplayer games that all selected users have in common.

1. [Click here to invite the bot to your server](https://discord.com/oauth2/authorize?client_id=1480349911651450991)
2. Run the `/roulette` command in any channel the bot can access.
3. Enter either:
   - your **SteamID64**
   - or your **Steam username**
4. If you enter a username and multiple matches are found, the bot will show the first few results so you can choose the correct account.
5. Once your account is identified, select up to **5 friends** from your Steam friends list.
6. Press **Run** and the bot will begin shuffling through shared games.

## Linking your account

Users do **not** need to manually copy account data into Discord.

They can simply click a link to open the Steam profile or activity page in their browser, sign in if needed, and confirm the correct account from there.

If a username search returns multiple results, profile pictures and account details are shown to make it easier to pick the right person.

## Notes

- Your Steam friends list and game library must be visible enough for the bot to access them.
- If a profile is private, the bot may not be able to retrieve friends or owned games.
- Results are limited by the data Steam makes publicly available through its APIs.
- The bot checks for multiplayer-related categories before listing shared games.

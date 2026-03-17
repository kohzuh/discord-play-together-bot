# discord-play-together-bot# Steam Friends Common Multiplayer Discord Bot

A Discord bot that lets a user enter their **SteamID64**, choose from their **Steam friends list**, and then find **common games** that are marked as **multiplayer** in Steam store categories.

## What it does

- Opens a Discord modal for the user to enter their **SteamID64**
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

## Installation

Install dependencies:

```bash
pip install -U discord.py requests python-dotenv

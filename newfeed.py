#!.venv/bin/python3
# Copyright (c) 2020 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE for full details.

# See README.md for instructions on setup and usage

import discord
import os
import re
import readline  # noqa: F401 -- imported for its side effect: input() line editing
import sys
from configparser import ConfigParser
from pathlib import Path

from feedfields import fetch_feed, print_rendered

USER_AGENT = "linux:github.com/freiheit/discord_feedbot:newfeed.py (by /u/freiheit)"


# Get login_token from config:
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.expanduser("~")

AUTH_CONFIG_PATHS = [
    os.path.join(HOME_DIR, ".feed2discord.auth.ini"),
    os.path.join(BASE_DIR, "feed2discord.auth.ini"),
    "feed2discord.auth.ini",
    os.path.join(HOME_DIR, ".feed2discord.ini"),
    os.path.join(BASE_DIR, "feed2discord.local.ini"),
    "feed2discord.local.ini",
    "/etc/feed2discord.ini",
    os.path.join(BASE_DIR, "feed2discord.ini"),
    "feed2discord.ini",
]
config = ConfigParser()
config_paths = []

for path in AUTH_CONFIG_PATHS:
    if os.path.isfile(path):
        config_paths.append(path)
        break
else:
    print("No configuration file found.")
    exit()

config.read(config_paths)

login_token = config.get("MAIN", "login_token")
default_room = config.getint("MAIN", "default_room")

# Get feed URL from CLI or prompt for it:
feed_url = ""
if len(sys.argv) == 2:
    feed_url = sys.argv[1]
else:
    feed_url = input("Feed URL: ")

feed_data = fetch_feed(feed_url, USER_AGENT)
if feed_data.entries:
    print("Latest feed item to help you figure out fields")
    print("----------")
    print_rendered(feed_data.entries[0], truncate=500)
    print("----------")
    print(
        "Recommend: try posting links in a room somewhere to see if discord gives a nice preview"
    )
    print("----------")
else:
    print("No entries in feed? Are you sure that URL is good?")
    print("(version=%r bozo=%r)" % (feed_data.version, feed_data.bozo))
print()
print("Example (if discord has nice link preview): link")
print("Example (super-typical): ##title,-#published,<link>,>summary")
print(
    "Example (if various useful authors): ##title,_author_,-#published,<link>,>summary"
)
print("Example (super-typical): ##title,-#published,<link>,>description")
print(
    'Example (if title not great): "# Discord Status",##title,-#published,<link>,>summary'
)
print(
    "Example (podcast): ##title,###subtitle,-#published,itunes.duration,<enclosures.href>"
)
fields = input("Feed Fields: ")

name = input("Feed and Channel Name: ")


class MyClient(discord.Client):
    room_id = 0

    async def on_ready(self):
        print("Connected!")
        print("Username: {0.name}\nID: {0.id}".format(self.user))

        old_room = self.get_channel(default_room)
        new_room = await old_room.clone(
            name=name, reason=f"feedbot {feed_url} {fields}"
        )
        await new_room.edit(reason="Update topic", topic=feed_url)
        self.room_id = new_room.id

        await self.close()


intents = discord.Intents.default()
client = MyClient(intents=intents)
client.run(login_token)

room_id = client.room_id

room_slug = f"{name} = {room_id}"

feed_slug = f"""[{name}]
channels = {name}
feed_url = {feed_url}
fields = {fields}"""

print(room_slug)
print(feed_slug)

print("Do those look good?")
yesno = input("y/n: ")

if yesno == "y" or yesno == "Y":
    ini = Path("feed2discord.local.ini")
    original = ini.read_text()
    Path("feed2discord.local.ini~").write_text(original)  # backup
    out = []
    for line in original.splitlines(keepends=True):
        if re.match(f"default *= *{default_room}", line):
            out.append(room_slug + "\n")
        out.append(line)
    out.append("\n" + feed_slug + "\n\n")
    ini.write_text("".join(out))
    print("Done!")
    print("Restart feedbot to activate")
else:
    print("Not editing configuration; you probably need to cleanup a room")

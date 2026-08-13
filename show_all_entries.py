#!.venv/bin/python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import sys

from feedfields import fetch_feed, print_rendered

USER_AGENT = (
    "linux:github.com/freiheit/discord_feedbot:show_all_entries.py (by /u/freiheit)"
)


# 0 is command itself:
if len(sys.argv) == 2:
    feed_data = fetch_feed(sys.argv[1], USER_AGENT)
    for i, entry in enumerate(feed_data.entries):
        print(f"\n## Entry {i}:")
        print_rendered(entry, truncate=500)
else:
    print(
        "Give me 1 feed URL on the command-line, and I'll give all the entries from it."
    )

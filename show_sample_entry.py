#!.venv/bin/python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import sys

from feedfields import fetch_feed, print_rendered

USER_AGENT = (
    "linux:github.com/freiheit/discord_feedbot:show_sample_entry.py (by /u/freiheit)"
)


# 0 is command itself:
if len(sys.argv) == 2:
    feed_data = fetch_feed(sys.argv[1], USER_AGENT)
    if not feed_data.entries:
        print("No entries in feed -- is that URL a working feed?")
        print("(version=%r bozo=%r)" % (feed_data.version, feed_data.bozo))
        sys.exit(1)
    print_rendered(feed_data.entries[0])
else:
    print(
        "Give me 1 feed URL on the command-line, and I'll give the first entry from it."
    )

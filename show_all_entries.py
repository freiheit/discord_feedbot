#!.venv/bin/python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import pprint
import sys

import feedparser_rs as feedparser  # Rust parser: matches feed2discord
import requests

USER_AGENT = (
    "linux:github.com/freiheit/discord_feedbot:show_all_entries.py (by /u/freiheit)"
)
# Fetch the same way feed2discord does (real UA, gzip/deflate -- no brotli, which
# some servers emit undecodably).  feedparser_rs.parse() takes content, not a URL.
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def fetch_feed(url):
    resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    return feedparser.parse(resp.content)


# 0 is command itself:
if len(sys.argv) == 2:
    feed_data = fetch_feed(sys.argv[1])
    pp = pprint.PrettyPrinter(indent=4, depth=3)
    print("# Feed metadata plus every entry.")
    print("# Use the top-level string fields; ignore the [...]/{...} structures.")
    pp.pprint(
        {
            "version": feed_data.version,
            "bozo": feed_data.bozo,
            "feed": dict(feed_data.feed),
            "entries": [dict(entry) for entry in feed_data.entries],
        }
    )
else:
    print(
        "Give me 1 feed URL on the command-line, and I'll give all the entries from it."
    )

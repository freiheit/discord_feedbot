#!.venv/bin/python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import sys

import feedparser_rs as feedparser  # Rust parser: matches feed2discord
import requests

from feedfields import enumerate_fields

USER_AGENT = (
    "linux:github.com/freiheit/discord_feedbot:show_all_entries.py (by /u/freiheit)"
)
# Fetch the same way feed2discord does (real UA, gzip/deflate -- no brotli, which
# some servers emit undecodably).  feedparser_rs.parse() takes content, not a URL.
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def print_rendered(entry):
    """Print every reachable field as `=== token ===` + value (values truncated).

    token is exactly what to drop into a feed's `fields =` line (including dotted
    names like `itunes.duration` / `enclosures.href`).
    """
    for token, value, in_list in enumerate_fields(entry):
        print(f"\n=== {token} ===")
        print(value[:500])
        if len(value) > 500:
            print("... (truncated)")
        if in_list:
            print(
                f"(list -- join all with e.g. [; ]{token}; delim can't contain a comma)"
            )


def fetch_feed(url):
    resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    return feedparser.parse(resp.content)


# 0 is command itself:
if len(sys.argv) == 2:
    feed_data = fetch_feed(sys.argv[1])
    for i, entry in enumerate(feed_data.entries):
        print(f"\n## Entry {i}:")
        print_rendered(entry)
else:
    print(
        "Give me 1 feed URL on the command-line, and I'll give all the entries from it."
    )

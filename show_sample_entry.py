#!.venv/bin/python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import sys

import feedparser_rs as feedparser  # Rust parser: matches feed2discord
import requests

from feedfields import enumerate_fields

USER_AGENT = (
    "linux:github.com/freiheit/discord_feedbot:show_sample_entry.py (by /u/freiheit)"
)
# Fetch the same way feed2discord does (real UA, gzip/deflate -- no brotli, which
# some servers emit undecodably).  feedparser_rs.parse() takes content, not a URL.
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def print_rendered(entry):
    """Print every reachable field as `=== token ===` + value.

    token is exactly what to drop into a feed's `fields =` line (including dotted
    names like `itunes.duration` / `enclosures.href`), so there's no need to read
    the raw feed to figure out how to address a field.
    """
    for token, value, in_list in enumerate_fields(entry):
        print(f"\n=== {token} ===")
        print(value)
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
    if not feed_data.entries:
        print("No entries in feed -- is that URL a working feed?")
        print("(version=%r bozo=%r)" % (feed_data.version, feed_data.bozo))
        sys.exit(1)
    print_rendered(feed_data.entries[0])
else:
    print(
        "Give me 1 feed URL on the command-line, and I'll give the first entry from it."
    )

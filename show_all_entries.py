#!.venv/bin/python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import html
import re
import sys

import feedparser_rs as feedparser  # Rust parser: matches feed2discord
import requests
from html2text import HTML2Text

USER_AGENT = (
    "linux:github.com/freiheit/discord_feedbot:show_all_entries.py (by /u/freiheit)"
)
# Fetch the same way feed2discord does (real UA, gzip/deflate -- no brotli, which
# some servers emit undecodably).  feedparser_rs.parse() takes content, not a URL.
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def render_text_field(value):
    """Render a string field the same way feed2discord's process_field does."""
    h = HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.ignore_emphasis = False
    h.body_width = 1000
    h.unicode_snob = True
    h.ul_item_mark = "-"
    rendered = h.handle(html.unescape(value))
    return re.sub("<[^<]+?>", "", rendered).strip()


def print_rendered(entry_dict):
    for key, value in entry_dict.items():
        if isinstance(value, str):
            text = value
        elif isinstance(value, list):
            # content-style list (Atom <content>, RSS <content:encoded>, JSON
            # content_html): a list of dict-like objects each with a 'value'.
            parts = [x["value"] for x in value if hasattr(x, "get") and x.get("value")]
            text = "\n".join(parts) if parts else None
        else:
            text = None
        if not text or not text.strip():
            continue
        rendered = render_text_field(text)
        truncated = len(rendered) > 500
        print(f"\n=== {key} ===")
        print(rendered[:500])
        if truncated:
            print("... (truncated)")


def fetch_feed(url):
    resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    return feedparser.parse(resp.content)


# 0 is command itself:
if len(sys.argv) == 2:
    feed_data = fetch_feed(sys.argv[1])
    entries = [dict(entry) for entry in feed_data.entries]
    for i, entry in enumerate(entries):
        print(f"\n## Entry {i}:")
        print_rendered(entry)
else:
    print(
        "Give me 1 feed URL on the command-line, and I'll give all the entries from it."
    )

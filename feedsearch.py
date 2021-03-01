#!/usr/bin/env python3.8
import pprint
import shutil
import sys

from feedsearch_crawler import search

# Get feed URL from CLI or prompt for it:
feed_url = ''
if len(sys.argv) == 2:
    feed_url = sys.argv[1]
else:
    feed_url = input("Feed URL: ")

feeds = search(feed_url)

# Get terminal width
term_size = shutil.get_terminal_size(fallback=(80, 24))
columns = term_size[0]

pp = pprint.PrettyPrinter(indent=4, width=columns, compact=False)
pp.pprint(feeds)

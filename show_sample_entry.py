#!/usr/bin/env python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import pprint
import sys
import feedparser
feedparser.PREFERRED_XML_PARSERS.remove('drv_libxml2')


# 0 is command itself:
if len(sys.argv) == 2:
    feed_url = sys.argv[1]
    feed_data = feedparser.parse(feed_url)
    pp = pprint.PrettyPrinter(indent=4, depth=1)
    print('# We currently restrict this output to depth=1,')
    print("# because that's all the bot can currently handle.")
    print(
        "# So, ignore those `[...]` and `{...}` structures and only look at 'strings'.")
    pp.pprint(feed_data.entries[0])
else:
    print("Give me 1 feed URL on the command-line, and I'll give the first entry from it.")

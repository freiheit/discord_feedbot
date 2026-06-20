#!.venv/bin/python3
# Copyright (c) 2016-2020 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.
"""Find the RSS/Atom feed(s) for a web page.

Tries several methods IN ORDER and stops at the first one that turns up a
working feed (every candidate is validated by actually parsing it with
feedparser, so we never report a dead/198 link):

  1. Autodiscovery -- well-formed <link rel="alternate" type=".../rss+xml">
     tags in the page <head>.
  2. Feed-looking links/URLs anywhere in the page body text.
  3. Sitemap(s) -- robots.txt "Sitemap:" lines and /sitemap.xml, in case one
     points at a feed.
  4. A linked "subscribe / feeds / rss" page, re-scanned with methods 1 + 2.
  5. Common feed URL patterns (shapes pulled from feed2discord.local.ini:
     WordPress /feed/, /rss[.xml], /atom.xml, Hugo /index.xml, Squarespace
     ?format=rss, plus host-specific GitHub/Reddit/Substack/Tumblr forms).

We fetch with the SAME User-Agent and Accept-Encoding the bot uses, so any
feed found here is one feed2discord can actually fetch too.

Usage: feedsearch.py [URL]   (prompts for the URL if not given)
"""
import json
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import feedparser
import requests

USER_AGENT = "linux:github.com/freiheit/discord_feedbot:feedsearch.py (by /u/freiheit)"
# gzip/deflate only: some servers emit brotli we can't decode (see feed2discord)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
TIMEOUT = 15
MAX_CANDIDATES = 30  # politeness cap on how many URLs a single method validates

session = requests.Session()
session.headers.update(HEADERS)

# Substrings that hint a link/URL is a feed (used by methods 2/4).
FEED_HINT = re.compile(r"(feed|rss|atom|\.xml|\.rdf|format=rss|format=atom)", re.I)

_fetch_cache = {}


def fetch(url):
    """GET url (cached). Returns (status, final_url, content_bytes) or None."""
    if url in _fetch_cache:
        return _fetch_cache[url]
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        result = (r.status_code, r.url, r.content, r.headers.get("Content-Type", ""))
    except requests.RequestException:
        result = None
    _fetch_cache[url] = result
    return result


def feed_info(content):
    """If content parses as a real feed return (title, version, n_entries).

    Covers every format feedparser understands (RSS 0.9x/1.0/2.0, RDF, Atom,
    CDF, ...) plus JSON Feed, which feedparser does NOT support, so we detect
    that one ourselves.
    """
    parsed = feedparser.parse(content)
    if parsed.version:  # a feedparser-recognized XML feed
        # Accept a feed with entries even if slightly malformed; for an empty
        # feed insist it parsed cleanly, so we don't mistake HTML for a feed.
        if len(parsed.entries) == 0 and parsed.bozo:
            return None
        return (parsed.feed.get("title", "(untitled)"), parsed.version, len(parsed.entries))
    # JSON Feed (https://jsonfeed.org) -- feedparser can't parse it.
    if content.lstrip(b"\xef\xbb\xbf").lstrip()[:1] == b"{":
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            return None
        version = data.get("version", "") if isinstance(data, dict) else ""
        if isinstance(version, str) and "jsonfeed.org" in version:
            return (data.get("title", "(untitled)"), "json", len(data.get("items", [])))
    return None


def _looks_like_feed(content):
    """Cheap check: does the body start like an XML feed or a JSON Feed?"""
    head = content.lstrip(b"\xef\xbb\xbf").lstrip()[:1024].lower()
    return (head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head
            or b"<rdf" in head or head.startswith(b"{"))


def validate(url):
    """Fetch url; return (final_url, title, version, n_entries) if it's a feed."""
    got = fetch(url)
    if not got or got[0] != 200:
        return None
    # An HTML response is essentially never a real feed; skip parsing it unless
    # the body actually starts like XML (covers servers that mislabel feeds).
    # This stops SPA sites that return 200 HTML for every path from being slow.
    if "html" in got[3].lower() and not _looks_like_feed(got[2]):
        return None
    info = feed_info(got[2])
    if info is None:
        return None
    return (got[1],) + info


def _validate_candidates(urls, limit=MAX_CANDIDATES, stop_after=None):
    """Validate an ordered iterable of candidate URLs, dropping dupes.

    Stops after `limit` URLs have been tried, or once `stop_after` working
    feeds have been collected (so we don't keep hammering a site after we've
    clearly found its feed).
    """
    found, seen, tried = [], set(), 0
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        tried += 1
        if tried > limit:
            break
        hit = validate(url)
        if hit:
            found.append(hit)
            if stop_after and len(found) >= stop_after:
                break
    return found


class LinkExtractor(HTMLParser):
    """Pull <link> autodiscovery tags and <a> anchors out of a page."""

    def __init__(self):
        super().__init__()
        self.alt_links = []   # hrefs from <link rel=alternate type=.../rss+xml>
        self.anchors = []     # (href, text) from <a href=...>
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link":
            rel = d.get("rel", "").lower()
            typ = d.get("type", "").lower()
            href = d.get("href")
            if href and ("alternate" in rel or not rel) and (
                "rss" in typ or "atom" in typ or "rdf" in typ
            ):
                self.alt_links.append(href)
        elif tag == "a" and d.get("href"):
            self._href = d["href"]
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text).strip()))
            self._href = None
            self._text = []


def _parse_links(base, html):
    p = LinkExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    return p


# --- Method 1: autodiscovery <link> tags -----------------------------------
def method_autodiscovery(base, html):
    links = _parse_links(base, html)
    return _validate_candidates(urljoin(base, h) for h in links.alt_links)


# --- Method 2: feed-looking links/URLs in the page body --------------------
def method_body_links(base, html, limit=20, stop_after=3):
    links = _parse_links(base, html)
    cands = [
        urljoin(base, href)
        for href, text in links.anchors
        if FEED_HINT.search(href) or FEED_HINT.search(text)
    ]
    # plus any raw absolute URLs in the markup that look feed-ish
    cands += [u for u in re.findall(r'https?://[^\s"\'<>]+', html) if FEED_HINT.search(u)]
    # link-heavy pages (e.g. GitHub) can have many feed-ish-looking URLs that
    # aren't feeds; bound how many we probe so this stays responsive.
    return _validate_candidates(cands, limit=limit, stop_after=stop_after)


# --- Method 3: sitemap(s) ---------------------------------------------------
def method_sitemap(url):
    p = urlparse(url)
    origin = "%s://%s" % (p.scheme, p.netloc)
    queue = []
    robots = fetch(urljoin(origin, "/robots.txt"))
    if robots and robots[0] == 200:
        for line in robots[2].decode("utf-8", "replace").splitlines():
            m = re.match(r"\s*sitemap:\s*(\S+)", line, re.I)
            if m:
                queue.append(m.group(1))
    queue.append(urljoin(origin, "/sitemap.xml"))

    seen, feed_cands, visited = set(), [], 0
    while queue and visited < 10:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        got = fetch(sm)
        if not got or got[0] != 200:
            continue
        visited += 1
        text = got[2].decode("utf-8", "replace")
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text):
            if FEED_HINT.search(loc):
                feed_cands.append(loc)
            elif "sitemap" in loc.lower() and loc.lower().endswith(".xml"):
                queue.append(loc)  # nested sitemap index
    return _validate_candidates(feed_cands, stop_after=2)


# --- Method 4: a linked (or well-known) "subscribe / feeds" page ------------
def method_feed_page(base, html):
    p = urlparse(base)
    origin = "%s://%s" % (p.scheme, p.netloc)
    links = _parse_links(base, html)
    pages = []
    # links in the page whose href/text suggests a feeds/subscribe page
    for href, text in links.anchors:
        hay = (href + " " + text).lower()
        if any(k in hay for k in ("subscribe", "/feeds", "feeds", "rss", "follow us")):
            pages.append(urljoin(base, href))
    # plus a few well-known "where are the feeds" pages to probe directly
    for guess in ("about/feeds", "rss-feeds", "about", "subscribe", "feeds"):
        pages.append(urljoin(origin + "/", guess))
    for page_url in list(dict.fromkeys(pages))[:8]:
        got = fetch(page_url)
        if not got or got[0] != 200:
            continue
        sub_html = got[2].decode("utf-8", "replace")
        found = (method_autodiscovery(got[1], sub_html)
                 or method_body_links(got[1], sub_html, limit=6, stop_after=2))
        if found:
            return found
    return []


# --- Method 5: common feed URL patterns -------------------------------------
def method_url_patterns(url):
    p = urlparse(url)
    origin = "%s://%s" % (p.scheme, p.netloc)
    host = p.netloc.lower()
    path = p.path or "/"
    path_dir = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"

    # Common feed paths -- WordPress/Hugo/Squarespace/Blogger shapes plus the
    # set used by swiss-rss/rss-digger; tried against the site root and the
    # given path's directory.  Ordered roughly most-common-first so the
    # short-circuit usually hits quickly.  (JSON Feed / index.json is omitted:
    # feedparser can't parse it, so the bot couldn't consume it anyway.)
    suffixes = [
        "feed/", "feed", "feed.xml", "feed/default",
        "rss", "rss/", "rss.xml", "rss/index.xml",
        "atom.xml", "atom",
        "index.xml", "index.rss", "index.atom", "index.rdf",
        "feeds", "feeds/default", "feeds/rss/", "feeds/posts/default",
        "rdf", "data/rss", "feed/rss/",
        "?feed=rss2", "?feed=rss", "?feed=atom", "?feed=rdf",
    ]
    roots = [origin + "/"]
    if path_dir not in ("/", ""):
        roots.append(origin + path_dir)

    # urljoin handles "?..." correctly (replaces the query, keeps the path).
    # Host-specific shapes FIRST (high-precision, cheap) so e.g. a GitHub repo
    # is found in 1-2 requests instead of after the whole generic sweep.
    host_cands = []
    parts = [seg for seg in path.split("/") if seg]
    if host.endswith("github.com") and len(parts) >= 2:
        repo = "%s/%s/%s" % (origin, parts[0], parts[1])
        host_cands += [repo + "/releases.atom", repo + "/commits.atom"]
    if "reddit.com" in host:
        host_cands.append(url.rstrip("/") + "/.rss")
    if host.endswith("substack.com"):
        host_cands.append(origin + "/feed")
    if host.endswith(".tumblr.com"):
        host_cands.append(origin + "/rss")

    generic = [urljoin(root, suf) for root in roots for suf in suffixes]
    # Squarespace-style query on the given page
    generic.append(url + ("&" if "?" in url else "?") + "format=rss")

    # stop once a couple of working feeds are confirmed; otherwise sweep the
    # whole curated list (~50 URLs).
    return _validate_candidates(host_cands + generic, limit=80, stop_after=2)


def search(url):
    """Run the methods in order; return (method_name, [feeds]) at first hit."""
    # 0. Maybe the URL handed to us is already a feed.
    direct = validate(url)
    if direct:
        return ("0. the URL is already a feed", [direct])

    got = fetch(url)
    base = got[1] if got else url
    html = got[2].decode("utf-8", "replace") if got and got[0] == 200 else ""

    methods = [
        ("1. autodiscovery <link> tags", lambda: method_autodiscovery(base, html)),
        ("2. feed links in page body", lambda: method_body_links(base, html)),
        ("3. sitemap", lambda: method_sitemap(url)),
        ("4. linked subscribe/feeds page", lambda: method_feed_page(base, html)),
        ("5. common URL patterns", lambda: method_url_patterns(url)),
    ]
    for name, fn in methods:
        try:
            feeds = fn()
        except Exception as exc:  # one method blowing up shouldn't stop the rest
            print("  [%s] error: %s" % (name, exc), file=sys.stderr)
            feeds = []
        if feeds:
            # de-dupe by final URL, preserving order
            uniq, seen = [], set()
            for f in feeds:
                if f[0] not in seen:
                    seen.add(f[0])
                    uniq.append(f)
            return (name, uniq)
    return (None, [])


def main():
    if len(sys.argv) == 2:
        url = sys.argv[1].strip()
    else:
        url = input("Feed URL: ").strip()
    if not urlparse(url).scheme:
        url = "https://" + url

    method, feeds = search(url)
    if not feeds:
        print("No feeds found for %s" % url)
        sys.exit(1)
    print("Found via %s:" % method)
    for feed_url, title, version, n_entries in feeds:
        print("  %s" % feed_url)
        print("      title: %s" % title)
        note = "  (JSON Feed -- feed2discord/feedparser cannot consume this)" if version == "json" else ""
        print("      type: %s, entries: %d%s" % (version, n_entries, note))


if __name__ == "__main__":
    main()

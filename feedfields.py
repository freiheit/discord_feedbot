# Copyright (c) 2016-2026 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.
"""Shared field access + rendering for feed2discord and its utility scripts.

feedparser_rs returns typed, *dict-like* objects for structured feed data --
each supports ``.get()`` / ``.items()`` / ``.keys()`` (and attribute access):

    entry["itunes"]     -> one ItunesEntryMeta  (duration, explicit, image, ...)
    entry["enclosures"] -> list[Enclosure]      (href, type, length, ...)
    entry["links"]      -> list[Link]           (href, rel, type)
    entry["tags"]       -> list[Tag]            (term, scheme, label)

A **dotted** field name reaches into these: ``itunes.duration``,
``enclosures.href``, ``image.href``.  When the base is a list, a dotted name
resolves against its *first* element (use the ``[delim]field.key`` templating
form to join *all* elements instead).

This module is the single source of truth so the bot (``feed2discord.py``) and
the discovery helpers (``show_sample_entry.py``, ``show_all_entries.py``,
``newfeed.py``) always agree on which fields exist and how they render.
"""

import gzip
import html
import re
import urllib.request
import zlib
from html.parser import HTMLParser

import feedparser_rs as feedparser
from html2text import HTML2Text


def http_get(url, user_agent, timeout=30):
    """GET url the way feed2discord does (gzip/deflate accepted, no brotli).

    Returns (status, final_url, body_bytes, content_type).  Decompresses the
    body itself since urllib, unlike requests, doesn't.  Raises urllib.error /
    OSError on network failure or non-2xx status; callers handle or crash.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        encoding = resp.headers.get("Content-Encoding", "").lower()
        if encoding == "gzip":
            body = gzip.decompress(body)
        elif encoding == "deflate":
            try:
                body = zlib.decompress(body)
            except zlib.error:  # some servers send raw deflate, no zlib header
                body = zlib.decompress(body, -zlib.MAX_WBITS)
        return (resp.status, resp.geturl(), body, resp.headers.get("Content-Type", ""))


# Shared HTML2Text, configured the way feed2discord renders body fields.
# handle() resets its output buffer each call, so reusing one instance is safe.
_h2t = HTML2Text()
_h2t.ignore_links = True
_h2t.ignore_images = True
_h2t.ignore_emphasis = False
_h2t.body_width = 1000
_h2t.unicode_snob = True
_h2t.ul_item_mark = "-"


def _is_mapping(obj):
    """True for a dict-like value (a dict or a feedparser_rs typed object)."""
    return hasattr(obj, "get") and hasattr(obj, "items")


def _scalar(value):
    """Return value as a str if it's a simple scalar (str/int/float), else None.

    bool is deliberately excluded -- feedparser flags like ``guidislink`` aren't
    useful as message text.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def resolve_field(item, name):
    """Resolve a field spec to a string value, or None when unavailable.

    Bare name (``summary``): ``item[name]`` as a string, coalescing a
    content-style list of dict-likes (each carrying a ``value``) into
    newline-joined text.  Dotted name (``itunes.duration``): walk into dict-like
    objects; a list base resolves against its first element.  Returns the final
    scalar as a str, else None.
    """
    if "." in name:
        base, rest = name.split(".", 1)
        obj = item.get(base)
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if _is_mapping(obj):
            return resolve_field(obj, rest)
        return None

    value = item.get(name)
    if value is None:
        return None
    scalar = _scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, list):
        parts = []
        for x in value:
            s = _scalar(x)
            if s:
                parts.append(s)
            elif _is_mapping(x) and x.get("value"):
                parts.append(x["value"])
        return "\n".join(parts) if parts else None
    return None


def render_text_field(value):
    """Render a field value the way feed2discord's bare-field path does.

    Prose (anything containing whitespace) is converted HTML->markdown; a
    whitespace-free value (a URL, id, or single token) is returned raw, because
    html2text has nothing to convert there and actively corrupts URLs -- it
    rewrites ``&e=2`` into ``&e;=2``.
    """
    unescaped = html.unescape(value)
    if not re.search(r"\s", value):
        return unescaped
    rendered = _h2t.handle(unescaped)
    return re.sub("<[^<]+?>", "", rendered).strip()


# HTML elements that never have a closing tag -- cut as a single tag, not a block.
_VOID_TAGS = frozenset(
    (
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    )
)

# One skip_elements selector: optional tag, optional .class, optional #id --
# "div.away-mode", ".footer", "hr", "aside#note". At least one part required.
_SELECTOR_RE = re.compile(r"^([A-Za-z0-9]+)?(?:\.([\w-]+))?(?:#([\w-]+))?$")


def _parse_selectors(spec):
    """Parse a comma-separated skip_elements spec into (tag, class, id) tuples.

    Each selector is ``tag``, ``.class``, ``tag.class``, ``#id``, or ``tag#id``.
    Tag names are lower-cased (HTML tags are case-insensitive); unparseable
    tokens are skipped.  Returns [] for an empty/blank spec.
    """
    selectors = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        m = _SELECTOR_RE.match(token)
        if not m:
            continue
        tag, cls, ident = m.groups()
        if tag or cls or ident:
            selectors.append((tag.lower() if tag else None, cls, ident))
    return selectors


class _ElementStripper(HTMLParser):
    """Record byte ranges of elements matching any selector, for deletion.

    Collects ``(start, end)`` offsets into the *original* HTML rather than
    rebuilding it, so every non-matching byte is preserved verbatim (rebuilding
    would re-escape attributes and corrupt things like ``&e=2`` in URLs).  A
    matched block element is removed through its *matching* close tag (depth
    counted so a nested same-name tag doesn't end it early); a matched void
    element (``hr``, ``img``, ...) is removed as its single tag.
    """

    def __init__(self, raw, selectors):
        super().__init__(convert_charrefs=False)
        self.raw = raw
        self.selectors = selectors
        self.cuts = []
        self._skip_tag = None  # tag name of the block currently being skipped
        self._depth = 0  # open count of that tag, for nesting
        self._skip_start = None  # offset where the skipped block began

    def _offset(self):
        """Absolute index into raw of the tag the parser is currently at."""
        line, col = self.getpos()
        idx = 0
        for _ in range(line - 1):
            idx = self.raw.index("\n", idx) + 1
        return idx + col

    def _matches(self, tag, attrs):
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        ident = attr.get("id")
        for stag, scls, sid in self.selectors:
            if stag and stag != tag:
                continue
            if scls and scls not in classes:
                continue
            if sid and sid != ident:
                continue
            return True
        return False

    def handle_starttag(self, tag, attrs):
        if self._skip_tag is not None:
            if tag == self._skip_tag and tag not in _VOID_TAGS:
                self._depth += 1
            return
        if self._matches(tag, attrs):
            start = self._offset()
            if tag in _VOID_TAGS:
                # get_starttag_text() is exact, so a '>' inside an attribute
                # value (title="a > b") doesn't truncate the cut.
                self.cuts.append((start, start + len(self.get_starttag_text())))
            else:
                self._skip_tag = tag
                self._depth = 1
                self._skip_start = start

    def handle_startendtag(self, tag, attrs):
        # An explicitly self-closing tag (<foo/>) -- never a block.
        if self._skip_tag is None and self._matches(tag, attrs):
            start = self._offset()
            self.cuts.append((start, start + len(self.get_starttag_text())))

    def handle_endtag(self, tag):
        if self._skip_tag is not None and tag == self._skip_tag:
            self._depth -= 1
            if self._depth == 0:
                start = self._offset()
                end = self.raw.index(">", start) + 1  # </tag> has no attributes
                self.cuts.append((self._skip_start, end))
                self._skip_tag = None
                self._skip_start = None

    def close(self):
        super().close()
        # Unclosed matched block (malformed HTML): drop from its start onward.
        if self._skip_tag is not None:
            self.cuts.append((self._skip_start, len(self.raw)))
            self._skip_tag = None


def strip_html_elements(raw, spec):
    """Return raw HTML with every element matching the skip_elements spec removed.

    ``spec`` is a comma-separated list of ``tag`` / ``.class`` / ``tag.class`` /
    ``#id`` / ``tag#id`` selectors.  A matched element (and, for a block element,
    everything through its matching close tag) is deleted; all other bytes are
    preserved exactly.  An empty/blank spec (or a falsy ``raw``) returns ``raw``
    unchanged.  Used by the body-field handlers to drop wrapper cruft (e.g. a
    site's ``div.away-mode`` notice) before HTML->markdown rendering.
    """
    if not raw:
        return raw
    selectors = _parse_selectors(spec or "")
    if not selectors:
        return raw
    parser = _ElementStripper(raw, selectors)
    parser.feed(raw)
    parser.close()
    for start, end in sorted(parser.cuts, reverse=True):
        raw = raw[:start] + raw[end:]
    return raw


# A bare http(s) URL: not already opened with '<', not mid-word, not in `code`.
# The body stops at whitespace / angle brackets / backtick.
_BARE_URL_RE = re.compile(r"(?<![<\w`])(https?://[^\s<>`]+)")


def wrap_bare_urls(text):
    """Wrap bare http(s) URLs in angle brackets (``<https://...>``).

    Discord makes a link preview for every unadorned URL; wrapping in ``<>``
    suppresses that.  Trailing sentence punctuation (``.,;:!?'"`` and *unbalanced*
    closing brackets) is left outside the brackets, so ``see https://x.co/a.``
    becomes ``see <https://x.co/a>.`` while ``.../Foo_(bar)`` keeps its ``)``.
    URLs already wrapped in ``<>`` or sitting inside ``code`` spans are left alone.
    """

    def repl(m):
        url = m.group(1)
        trail = ""
        while url and url[-1] in ".,;:!?\"')]}":
            if url[-1] in ")]}":
                opener = {")": "(", "]": "[", "}": "{"}[url[-1]]
                if url.count(opener) >= url.count(url[-1]):
                    break  # balanced closer -- part of the URL
            trail = url[-1] + trail
            url = url[:-1]
        return "<" + url + ">" + trail

    return _BARE_URL_RE.sub(repl, text)


def _collect(token, value, pairs, in_list=False):
    """Append (token, rendered_value, in_list) leaves for value under token."""
    scalar = _scalar(value)
    if scalar is not None:
        if scalar.strip():
            pairs.append((token, render_text_field(scalar), in_list))
        return
    if _is_mapping(value):
        # A mapping carrying a 'value' is a text construct (title_detail,
        # summary_detail, ...): show just its text, not its type/base/language
        # metadata, which merely duplicates the plain sibling field.
        text = value.get("value")
        if _scalar(text) is not None and _scalar(text).strip():
            pairs.append((f"{token}.value", render_text_field(text), in_list))
            return
        for key, sub in value.items():
            _collect(f"{token}.{key}", sub, pairs, in_list)
        return
    if isinstance(value, list):
        if not value:
            return
        # Content-style list (Atom <content>, RSS <content:encoded>, JSON
        # content_html): dict-likes each with a 'value' -- join into one field.
        texts = [x["value"] for x in value if _is_mapping(x) and x.get("value")]
        if texts:
            pairs.append((token, render_text_field("\n".join(texts)), False))
            return
        first = value[0]
        if _is_mapping(first):
            # List of attribute objects (enclosures, links, tags): show the
            # first element's leaves; the [delim]field.key form joins them all.
            for key, sub in first.items():
                _collect(f"{token}.{key}", sub, pairs, in_list=True)
            return
        strs = [s for s in (_scalar(x) for x in value) if s]
        if strs:
            pairs.append((token, render_text_field(", ".join(strs)), False))


def enumerate_fields(entry):
    """List every reachable field of a parsed entry as (token, value, in_list).

    ``token`` is exactly what you'd put in a feed's ``fields =`` line -- e.g.
    ``title``, ``itunes.duration``, ``enclosures.href``.  ``value`` is rendered
    the same way the bot would render it.  ``in_list`` is True when the field
    comes from a list of objects, so ``[delim]token`` would join every element
    (the plain token shows only the first).  None/empty values are omitted.
    """
    pairs = []
    for key, value in dict(entry).items():
        _collect(key, value, pairs)
    return pairs


def fetch_feed(url, user_agent):
    """Fetch url and parse it with feedparser_rs. Returns the parsed feed.

    Shared by the discovery helpers (show_sample_entry.py, show_all_entries.py,
    newfeed.py) so they fetch and parse exactly the way the bot does.
    """
    return feedparser.parse(http_get(url, user_agent)[2])


def print_rendered(entry, truncate=None):
    """Print every reachable field of an entry as ``=== token ===`` + value.

    ``token`` is exactly what to drop into a feed's ``fields =`` line (including
    dotted names like ``itunes.duration`` / ``enclosures.href``), so there's no
    need to read the raw feed to figure out how to address a field.  When
    ``truncate`` is set, long values are cut to that many characters.
    """
    for token, value, in_list in enumerate_fields(entry):
        print(f"\n=== {token} ===")
        if truncate is not None and len(value) > truncate:
            print(value[:truncate])
            print("... (truncated)")
        else:
            print(value)
        if in_list:
            print(
                f"(list -- join all with e.g. [; ]{token}; delim can't contain a comma)"
            )

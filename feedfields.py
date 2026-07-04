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

import html
import re

from html2text import HTML2Text


def make_html2text():
    """Return an HTML2Text configured the way feed2discord renders body fields."""
    h = HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.ignore_emphasis = False
    h.body_width = 1000
    h.unicode_snob = True
    h.ul_item_mark = "-"
    return h


# Shared instance: HTML2Text.handle() resets its output buffer each call, so the
# object is stateless between uses and safe to reuse.
_h2t = make_html2text()


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

#!/usr/bin/env python3
# Copyright (c) 2016-2020 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import asyncio
import calendar
import hashlib
import logging
import os
import random
import re
import socket
import sqlite3
import struct
import sys
import time
import warnings
import html

from argparse import ArgumentParser
from configparser import ConfigParser
from datetime import datetime, timezone
from importlib import reload
from urllib.parse import urljoin
from pprint import pformat

import aiohttp
import discord
import feedparser_rs as feedparser  # Rust parser: faster + supports JSON Feed

from aiohttp.web_exceptions import HTTPError, HTTPNotModified
from dateutil.parser import parse as parse_datetime
from dateutil.tz import gettz
from html2text import HTML2Text


__version__ = "4.0.0"

TRACE_LEVEL = 5
VERBOSE_LEVEL = 8
NOTICE_LEVEL = 35
logging.addLevelName(TRACE_LEVEL, "TRACE")
logging.addLevelName(VERBOSE_LEVEL, "VERBOSE")
logging.addLevelName(NOTICE_LEVEL, "NOTICE")


def _logger_trace(self, message, *args, **kwargs):
    """Emit a TRACE-level log record. Bound to logging.Logger as .trace()."""
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **kwargs)


def _logger_verbose(self, message, *args, **kwargs):
    """Emit a VERBOSE-level log record. Bound to logging.Logger as .verbose()."""
    if self.isEnabledFor(VERBOSE_LEVEL):
        self._log(VERBOSE_LEVEL, message, args, **kwargs)


def _logger_notice(self, message, *args, **kwargs):
    """Emit a NOTICE-level log record. Bound to logging.Logger as .notice()."""
    if self.isEnabledFor(NOTICE_LEVEL):
        self._log(NOTICE_LEVEL, message, args, **kwargs)


logging.Logger.trace = _logger_trace
logging.Logger.verbose = _logger_verbose
logging.Logger.notice = _logger_notice


PROG_NAME = "linux:github.com/freiheit/discord_feedbot"
USER_AGENT = "%s:%s (by /u/freiheit)" % (PROG_NAME, __version__)

# Timezone abbreviations dateutil can't resolve on its own (some RSS/RFC-822
# feeds use these instead of numeric offsets).  US zones cover the feeds we
# follow; every date is normalized to UTC regardless.
TZINFOS = {
    "UT": timezone.utc,
    "UTC": timezone.utc,
    "GMT": timezone.utc,
    "Z": timezone.utc,
    "EST": gettz("America/New_York"),
    "EDT": gettz("America/New_York"),
    "CST": gettz("America/Chicago"),
    "CDT": gettz("America/Chicago"),
    "MST": gettz("America/Denver"),
    "MDT": gettz("America/Denver"),
    "PST": gettz("America/Los_Angeles"),
    "PDT": gettz("America/Los_Angeles"),
}

# HTTP statuses that mean "you're being rate-limited / the server is overloaded":
# 403 Forbidden, 420 Enhance Your Calm, 429 Too Many Requests, 503 Service
# Unavailable, 508 Loop Detected, 509 Bandwidth Limit Exceeded.  When a feed
# returns one of these we exponentially back off how often we poll it
# (see background_check_feed).
BACKOFF_STATUSES = {403, 420, 429, 503, 508, 509}

SQL_CREATE_FEED_INFO_TBL = """
CREATE TABLE IF NOT EXISTS feed_info (
    feed text PRIMARY KEY,
    url text UNIQUE,
    lastmodified text,
    etag text,
    content_hash text
)
"""

SQL_CREATE_FEED_ITEMS_TBL = """
CREATE TABLE IF NOT EXISTS feed_items (
    id text PRIMARY KEY,
    published text
)
"""

# 10 years (3650 days). Kept this long because some feeds (e.g. frontierforums)
# bump an item's "published" date when a reply is posted; retaining the id keeps
# such items from being treated as new and re-sent once their row is deleted.
SQL_CLEAN_OLD_ITEMS = """
DELETE FROM feed_items WHERE (julianday() - julianday(published)) > 3650
"""


if not sys.version_info[:2] >= (3, 6):
    print("Error: requires python 3.6 or newer")
    exit(1)


class ImproperlyConfigured(Exception):
    pass


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.expanduser("~")

DEFAULT_CONFIG_PATHS = [
    os.path.join(HOME_DIR, ".feed2discord.ini"),
    os.path.join(BASE_DIR, "feed2discord.local.ini"),
    os.path.join("feed2discord.local.ini"),
    os.path.join("/etc/feed2discord.ini"),
    os.path.join(BASE_DIR, "feed2discord.ini"),
    os.path.join("feed2discord.ini"),
]

DEFAULT_AUTH_CONFIG_PATHS = [
    os.path.join(HOME_DIR, ".feed2discord.auth.ini"),
    os.path.join(BASE_DIR, "feed2discord.auth.ini"),
    os.path.join("feed2discord.auth.ini"),
]


class _JournalHandler(logging.Handler):
    """Write log records directly to the systemd journal socket.

    Setting SYSLOG_IDENTIFIER per-message makes journald show the bot name
    instead of 'python3', regardless of how the process was started.
    Falls back gracefully — check `.available` before adding to a logger.
    """

    _SOCKET_PATH = "/run/systemd/journal/socket"
    _PRIORITY = {
        logging.CRITICAL: 2,
        logging.ERROR: 3,
        logging.WARNING: 4,
        NOTICE_LEVEL: 5,  # syslog NOTICE — visible at debug=0
        logging.INFO: 6,
        logging.DEBUG: 7,
        VERBOSE_LEVEL: 7,
        TRACE_LEVEL: 7,
    }

    def __init__(self, identifier, level=logging.NOTSET):
        super().__init__(level)
        self.identifier = identifier
        self.available = False
        self._sock = None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            s.connect(self._SOCKET_PATH)
            self._sock = s
            self.available = True
        except OSError:
            pass

    def emit(self, record):
        try:
            priority = self._PRIORITY.get(record.levelno, 7)
            msg = self.format(record).encode("utf-8", "replace")
            # Always use binary framing for MESSAGE so embedded newlines
            # (e.g. tracebacks) don't corrupt the journal record format.
            data = (
                b"PRIORITY="
                + str(priority).encode()
                + b"\n"
                + b"SYSLOG_IDENTIFIER="
                + self.identifier.encode()
                + b"\n"
                + b"MESSAGE\n"
                + struct.pack("<Q", len(msg))
                + msg
                + b"\n"
            )
            self._sock.send(data)
        except Exception:
            self.handleError(record)


def parse_args():
    """Parse command-line arguments. Returns argparse.Namespace. Called by get_config()."""
    version = "%(prog)s {}".format(__version__)
    p = ArgumentParser(prog=PROG_NAME)
    p.add_argument("--version", action="version", version=version)
    p.add_argument("--config")

    return p.parse_args()


def get_config():
    """Load config file, set up logging, return (config, logger). Called at module level."""
    args = parse_args()
    config = ConfigParser()
    config_paths = []

    if args.config:
        config_paths = [args.config]
    else:
        for path in DEFAULT_CONFIG_PATHS:
            if os.path.isfile(path):
                config_paths.append(path)
                break
        else:
            raise ImproperlyConfigured("No configuration file found.")

        for path in DEFAULT_AUTH_CONFIG_PATHS:
            if os.path.isfile(path):
                config_paths.append(path)
                break

    config.read(config_paths)

    debug = config["MAIN"].getint("debug", 0)

    if debug >= 5:
        os.environ["PYTHONASYNCIODEBUG"] = "1"
        # The AIO modules need to be reloaded because of the new env var
        reload(asyncio)
        reload(aiohttp)
        reload(discord)

    if debug >= 5:
        log_level = TRACE_LEVEL
    elif debug >= 4:
        log_level = VERBOSE_LEVEL
    elif debug >= 3:
        log_level = logging.DEBUG
    elif debug >= 2:
        log_level = logging.INFO
    elif debug >= 1:
        log_level = logging.WARNING
    else:
        log_level = NOTICE_LEVEL

    fmt = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    bot_name = os.path.splitext(os.path.basename(__file__))[0]
    journal = _JournalHandler(bot_name, level=log_level)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    # Keep noisy library loggers at WARNING unless debug=5 opens everything up.
    # discord.gateway in particular dumps full WebSocket JSON at DEBUG level.
    if debug < 5:
        for lib in ("discord", "aiohttp", "asyncio"):
            logging.getLogger(lib).setLevel(logging.WARNING)

    # discord.py warns about missing PyNaCl/davey (voice support) on every start.
    # This bot never uses voice; suppress those specific messages permanently.
    class _NoVoiceWarning(logging.Filter):
        def filter(self, record):
            return "voice will NOT be supported" not in record.getMessage()

    logging.getLogger("discord.client").addFilter(_NoVoiceWarning())

    if journal.available:
        journal.setFormatter(fmt)
        root.addHandler(journal)
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        handler.setFormatter(fmt)
        root.addHandler(handler)

    logger = logging.getLogger(__name__)
    warnings.resetwarnings()

    return config, logger


def get_timezone(config):
    """Return a pytz timezone from the [MAIN] timezone setting. Called at module level."""
    import pytz

    tzstr = config["MAIN"].get("timezone", "utc")
    # This has to work on both windows and unix
    try:
        timezone = pytz.timezone(tzstr)
    except Exception:
        timezone = pytz.utc

    return timezone


def get_feeds_config(config):
    """Return a list of feed section names (all sections except MAIN and CHANNELS). Called by main()."""
    feeds = list(config.sections())

    # remove non-feed sections
    feeds.remove("MAIN")
    feeds.remove("CHANNELS")

    return feeds


def get_sql_connection(config):
    """Open and return an SQLite connection with WAL mode enabled. Called by sql_maintenance() and background_check_feed()."""
    db_path = config["MAIN"].get("db_path", "feed2discord.db")
    conn = sqlite3.connect(db_path)
    # WAL: cheaper commits (~0.8ms vs ~1.9ms fsync) and concurrent reads while
    # writing.  It's a persistent property of the DB file, so this is idempotent
    # after the first connection converts it.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def sql_maintenance(config):
    """Create tables, run migrations, and purge items older than 10 years. Called by main()."""
    conn = get_sql_connection(config)

    # If our two tables don't exist, create them.
    conn.execute(SQL_CREATE_FEED_INFO_TBL)
    conn.execute(SQL_CREATE_FEED_ITEMS_TBL)

    migrate_db(conn)

    # Clean out *some* entries that are over 10 years old...
    # Doing this cleanup at start time because some feeds
    # do contain very old items and we don't want to keep
    # re-evaluating them.
    conn.execute(SQL_CLEAN_OLD_ITEMS)

    conn.commit()
    conn.close()


def migrate_db(conn):
    """Apply schema migrations and data fixes to an existing database."""
    feed_info_cols = {r[1] for r in conn.execute("PRAGMA table_info(feed_info)")}
    feed_items_cols = {r[1] for r in conn.execute("PRAGMA table_info(feed_items)")}

    if "content_hash" not in feed_info_cols:
        conn.execute("ALTER TABLE feed_info ADD COLUMN content_hash text")
        logger.notice("migrate_db: added content_hash column to feed_info")

    dead_cols = {"title", "url", "reposted"} & feed_items_cols
    if dead_cols:
        # ALTER TABLE DROP COLUMN requires SQLite 3.35+; use table-rebuild for
        # compatibility with older system SQLite (e.g. 3.34 on RHEL 9).
        conn.execute(
            "CREATE TABLE feed_items_new (id text PRIMARY KEY, published text)"
        )
        conn.execute("INSERT INTO feed_items_new SELECT id, published FROM feed_items")
        conn.execute("DROP TABLE feed_items")
        conn.execute("ALTER TABLE feed_items_new RENAME TO feed_items")
        logger.notice(
            "migrate_db: removed unused columns from feed_items: %s", dead_cols
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS feed_items_published ON feed_items(published)"
    )

    # Normalize any published dates that SQLite's julianday() can't parse
    # (old rows may have been stored in ctime or RFC-1123 format).
    bad_rows = conn.execute(
        "SELECT id, published FROM feed_items WHERE julianday(published) IS NULL"
    ).fetchall()
    updated = 0
    deleted = 0
    for row_id, raw_date in bad_rows:
        try:
            parsed = parse_datetime(raw_date, tzinfos=TZINFOS)
            conn.execute(
                "UPDATE feed_items SET published=? WHERE id=?",
                [parsed.astimezone(timezone.utc).isoformat(), row_id],
            )
            updated += 1
        except Exception:
            logger.warning(
                "migrate_db: unparseable published date %r for id %r, deleting row",
                raw_date,
                row_id,
            )
            conn.execute("DELETE FROM feed_items WHERE id=?", [row_id])
            deleted += 1
    if updated:
        logger.notice("migrate_db: normalized %d stale published date(s)", updated)
    if deleted:
        logger.notice("migrate_db: deleted %d unparseable feed item row(s)", deleted)


config, logger = get_config()

# Make main config area global, since used everywhere/anywhere
MAIN = config["MAIN"]
TIMEZONE = get_timezone(config)


# global discord client object
# Disable as much caching as we can, since we don't pay attention to users, members, messages, etc
intents = discord.Intents.default()
client = discord.Client(
    chunk_guilds_at_startup=False,
    member_cache_flags=discord.MemberCacheFlags.none(),
    max_messages=None,
    intents=intents,
)

# Feed names for which we've auto-disabled typing this run because Discord
# rate-limited the typing endpoint.  Resets on restart.
typing_disabled = set()


async def extract_best_item_date(item, tzinfo):
    """Return the best date for a feed item as a UTC-aware datetime, falling back to now. Called by background_check_feed()."""
    fields = ("published", "pubDate", "date", "created", "updated", "expiry")
    for date_field in fields:
        if item.get(date_field):
            # Prefer feedparser's pre-parsed struct_time: it resolves named
            # zones (EST/EDT/PST/...) that dateutil can't, and is already UTC.
            parsed = item.get(date_field + "_parsed")
            if parsed is not None:
                return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
            try:
                # Fall back to the raw string; TZINFOS lets dateutil understand
                # common zone abbreviations.  A string carrying no zone at all
                # is assumed to be in the configured timezone, then converted.
                date_obj = parse_datetime(item[date_field], tzinfos=TZINFOS)
                if date_obj.tzinfo is None:
                    date_obj = tzinfo.localize(date_obj)
                return date_obj.astimezone(timezone.utc)
            except Exception:
                pass

    # No potentials found, default to "now" in UTC
    return datetime.now(timezone.utc)


async def should_send_typing(conf, feed_name):
    """Return the effective send_typing setting (0 = off) for a feed. Called by maybe_send_typing()."""
    global_send_typing = conf.getint("send_typing", 0)
    return conf.getint("%s.send_typing" % (feed_name), global_send_typing)


async def maybe_send_typing(FEED, feed, channels):
    """Send a typing indicator to each channel if send_typing is enabled. Returns None. Called by background_check_feed() and actually_send_message()."""
    if feed in typing_disabled or not await should_send_typing(FEED, feed):
        return
    for channel in channels:
        try:
            await asyncio.wait_for(channel["object"].typing(), timeout=5)
            logger.verbose("%s:%s:sent typing", feed, channel["name"])
        except discord.errors.Forbidden:
            logger.exception(
                "%s:%s:forbidden - is bot allowed in channel?", feed, channel["name"]
            )
        except discord.errors.NotFound:
            logger.warning(
                "%s:%s:channel not found (404) — check channel config",
                feed,
                channel["name"],
            )
        except (asyncio.TimeoutError, discord.errors.RateLimited):
            typing_disabled.add(feed)
            logger.warning(
                "%s:typing rate-limited; disabling send_typing for this feed "
                "until restart",
                feed,
            )
            return


def _make_html2text():
    """Return a configured HTML2Text instance (links/images suppressed, wide body). Called by _field_quote() and _field_plain()."""
    h = HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.ignore_emphasis = False
    h.body_width = 1000
    h.unicode_snob = True
    h.ul_item_mark = "-"
    return h


def _field_string(m):
    """Return the literal string value from a "quoted" field spec. Called by process_field()."""
    return m.group(1)


def _field_highlight(m, item, FEED):
    """Return a field value wrapped in markup delimiters (bold, italic, spoiler, etc.). Called by process_field()."""
    begin, field, end = m.groups()
    if item.get(field) is not None:
        if field == "link":
            return begin + urljoin(FEED.get("feed_url"), item[field]) + end
        return begin + html.unescape(item[field]) + end
    logger.error("process_field:%s:no such field", field)
    return ""


def _field_header(m, item):
    """Return a Markdown heading line (## / ### / etc.) from a field value. Called by process_field()."""
    prefix, field = m.group(1), m.group(2)
    if item.get(field) is not None:
        content = re.sub("<[^<]+?>", "", html.unescape(item[field]))
        content = content.splitlines()[0].strip() if content.strip() else ""
        return prefix + " " + content if content else ""
    logger.error("process_field:%s:no such field", field)
    return ""


def _field_bigcode(m, item):
    """Return a field value wrapped in a triple-backtick code block. Called by process_field()."""
    field = m.group(1)
    if item.get(field) is not None:
        return "```\n%s\n```" % html.unescape(item[field])
    logger.error("process_field:%s:no such field", field)
    return ""


def _field_quote(m, item):
    """Return a field's HTML-to-markdown content as Discord blockquote lines (> …). Called by process_field()."""
    field = m.group(1)
    if item.get(field) is not None:
        content = _make_html2text().handle(html.unescape(item[field]))
        content = re.sub("<[^<]+?>", "", content).strip()
        return "\n".join("> " + ln for ln in content.splitlines())
    logger.error("process_field:%s:no such field", field)
    return ""


def _field_code(m, item):
    """Return a field value wrapped in backtick inline code. Called by process_field()."""
    field = m.group(1)
    if item.get(field) is not None:
        return "`%s`" % html.unescape(item[field])
    logger.error("process_field:%s:no such field", field)
    return ""


def _field_tag(m, item, channel):
    """Return a comma-separated tag list with matching guild roles replaced by @-mentions. Called by process_field()."""
    field = m.group(1)
    if item.get(field) is not None:
        taglist = item[field].split(", ")
        for role in channel["object"].guild.roles:
            rn = str(role.name)
            taglist = ["<@&%s>" % role.id if rn == str(i) else i for i in taglist]
        return ", ".join(taglist)
    logger.error("process_field:%s:no such field", field)
    return ""


def _field_dict(m, item):
    """Return values from a list-of-dicts field joined by the configured delimiter. Called by process_field()."""
    delim, field, dictkey = m.group(1), m.group(2), m.group(3)
    if item.get(field) is not None:
        return delim.join([x[dictkey] for x in item[field]])
    logger.error("process_field:%s:no such field", field)
    return ""


def _field_plain(field, item, FEED):
    """Return a bare field value converted from HTML to Markdown. Called by process_field()."""
    if item.get(field) is not None:
        if field == "link":
            return urljoin(FEED.get("feed_url"), item[field])
        markdownfield = _make_html2text().handle(html.unescape(item[field]))
        return re.sub("<[^<]+?>", "", markdownfield)
    logger.error("process_field:%s:no such field", field)
    return ""


_RE_STRING = re.compile(r'^"(.+?)"$')
_RE_HIGHLIGHT = re.compile(r"^((?:[*_<]|~~|\|\|)+)(.+?)((?:[*_>]|~~|\|\|)+)$")
_RE_HEADER = re.compile(r"^(-?#+)\s*(.+)$")
_RE_BIGCODE = re.compile(r"^```(.+)```$")
_RE_QUOTE = re.compile(r"^>\s*(.+)$")
_RE_CODE = re.compile(r"^`(.+)`$")
_RE_TAG = re.compile(r"^@(.+)$")
_RE_DICT = re.compile(r"^\[(.+)\](.+)\.(.+)$")


async def process_field(field, item, FEED, channel):
    """Render one field spec to a string. Returns str. Called by build_message() and _apply_channel_filter()."""
    logger.trace("%s:process_field:%s: started", FEED, field)

    item_url_base = FEED.get("item_url_base", None)
    if field == "guid" and item_url_base is not None:
        if item.get("guid") is not None:
            return item_url_base + item["guid"]
        else:
            logger.error(
                "process_field:guid:no such field; try show_sample_entry.py on feed"
            )
            return ""

    logger.trace("%s:process_field:%s: checking regexes", FEED, field)
    stringmatch = _RE_STRING.match(field)
    highlightmatch = _RE_HIGHLIGHT.match(field)
    headermatch = _RE_HEADER.match(field)
    bigcodematch = _RE_BIGCODE.match(field)
    quotematch = _RE_QUOTE.match(field)
    codematch = _RE_CODE.match(field)
    tagmatch = _RE_TAG.match(field)
    dictmatch = _RE_DICT.match(field)

    if stringmatch is not None:
        logger.trace("%s:process_field:%s:isString", FEED, field)
        return _field_string(stringmatch)
    elif highlightmatch is not None:
        logger.trace("%s:process_field:%s:isHighlight", FEED, field)
        return _field_highlight(highlightmatch, item, FEED)
    elif headermatch is not None:
        logger.trace("%s:process_field:%s:isHeader", FEED, field)
        return _field_header(headermatch, item)
    elif bigcodematch is not None:
        logger.trace("%s:process_field:%s:isCodeBlock", FEED, field)
        return _field_bigcode(bigcodematch, item)
    elif quotematch is not None:
        logger.trace("%s:process_field:%s:isBlockquote", FEED, field)
        return _field_quote(quotematch, item)
    elif codematch is not None:
        logger.trace("%s:process_field:%s:isCode", FEED, field)
        return _field_code(codematch, item)
    elif tagmatch is not None:
        logger.trace("%s:process_field:%s:isTag", FEED, field)
        return _field_tag(tagmatch, item, channel)
    elif dictmatch is not None:
        logger.trace("%s:process_field:%s:isDict", FEED, field)
        return _field_dict(dictmatch, item)
    else:
        logger.trace("%s:process_field:%s:isPlain", FEED, field)
        return _field_plain(field, item, FEED)


async def build_message(FEED, item, channel):
    """Build the full Discord message string for an item (truncated to 1800 chars). Called by _process_item()."""
    message = ""
    fieldlist = FEED.get(
        channel["name"] + ".fields", FEED.get("fields", "id,description")
    ).split(",")
    # Extract fields in order
    for field in fieldlist:
        logger.trace("feed:item:build_message:%s:added to message", field)
        message += await process_field(field, item, FEED, channel) + "\n"

    # Naked spaces are terrible:
    message = re.sub(r"(?<!>) +\n", "\n", message)
    message = re.sub("\n +", "\n", message)

    # squash newlines down to single ones, and do that last...
    message = re.sub("(\n)+", "\n", message)

    if len(message) > 1800:
        message = message[:1800] + "\n... post truncated ..."
    return message


async def send_message_wrapper(asyncioloop, FEED, feed, channel, client, message):
    """Schedule actually_send_message as an asyncio task (with optional delay). Called by _process_item()."""
    delay = FEED.getint(channel["name"] + ".delay", FEED.getint("delay", 0))
    logger.debug(
        feed + ":" + channel["name"] + ":scheduling message with delay of " + str(delay)
    )
    asyncioloop.create_task(actually_send_message(channel, message, delay, FEED, feed))
    logger.debug(feed + ":" + channel["name"] + ":message scheduled")


async def actually_send_message(channel, message, delay, FEED, feed):
    """Sleep for delay seconds then send message to Discord, publishing if configured. Called via asyncio task by send_message_wrapper()."""
    await maybe_send_typing(FEED, feed, [channel])

    logger.debug(
        "%s:%s:sleeping for %i seconds before sending message",
        feed,
        channel["name"],
        delay,
    )

    if delay > 0:
        await asyncio.sleep(delay)

    logger.debug("%s:%s:actually sending message", feed, channel["name"])
    msg = await channel["object"].send(message)

    # if publish=1, channel is news/announcement and we have manage_messsages,
    # then "publish" so it goes to all servers
    if (
        config["MAIN"].getint("publish", FEED.getint("publish", 0)) >= 1
        and channel["object"].is_news()
    ):
        try:
            await msg.publish()
        except BaseException:
            logger.warning(feed + ": Could not publish message")

    logger.debug("%s:%s:message sent: %r", feed, channel["name"], message)


def _resolve_channels(feed, FEED, config, client):
    """Return a list of channel dicts ({object, name, id}) for a feed's configured channels. Called by background_check_feed()."""
    channels = []
    for key in FEED.get("channels").split(","):
        channel_id = config["CHANNELS"].getint(key)
        logger.trace(feed + ": adding channel " + key + ":" + str(channel_id))
        channel_obj = client.get_channel(channel_id)
        logger.trace(pformat(channel_obj))
        if channel_obj is not None:
            channels.append({"object": channel_obj, "name": key, "id": channel_id})
            logger.trace(feed + ": added channel " + key)
        else:
            logger.warning(
                feed + ": did not add channel " + key + "/" + str(channel_id)
            )
            logger.warning(pformat(channel_obj))
    if not channels:
        logger.warning(
            "%s: no valid channels found — messages will never be sent", feed
        )
    return channels


def _load_feed_cache(conn, feed, feed_url):
    """Look up cached etag/lastmodified/hash; register feed row if first-seen.

    Returns (lastmodified, etag, stored_hash) with None for absent values.
    """
    cursor = conn.execute(
        "select lastmodified,etag,content_hash from feed_info where feed=? OR url=?",
        [feed, feed_url],
    )
    data = cursor.fetchone()
    if data is None:
        logger.trace(feed + ":looks like updated version. saving info")
        conn.execute("REPLACE INTO feed_info (feed,url) VALUES (?,?)", [feed, feed_url])
        logger.trace(feed + ":feed info saved")
        return None, None, None
    lastmodified, etag, stored_hash = data[0], data[1], data[2]
    if lastmodified:
        logger.trace(feed + ":cached lastmodified: " + lastmodified)
    else:
        logger.trace(feed + ":no stored lastmodified")
        lastmodified = None
    if etag:
        logger.trace(feed + ":cached etag: " + etag)
    else:
        logger.trace(feed + ":no stored ETag")
        etag = None
    return lastmodified, etag, stored_hash


async def _read_feed_response(http_response, feed, stored_hash):
    """Validate HTTP status and read body.

    Returns (http_data, new_hash) on HTTP 200 with changed content.
    Raises HTTPNotModified on 304 or unchanged content hash.
    Raises HTTPError on null status or unexpected non-200 (BACKOFF_STATUSES
    are handled by the caller so it can update current_refresh).
    """
    logger.trace("%s:%s", feed, http_response)
    if http_response.status is None:
        logger.error(feed + ":HTTP response code is NONE")
        http_response.close()
        raise HTTPError()
    if http_response.status == 304:
        logger.debug(feed + ":data is old; moving on")
        http_response.close()
        raise HTTPNotModified()
    if http_response.status != 200:
        logger.warning("%s:unexpected HTTP status %s", feed, http_response.status)
        http_response.close()
        raise HTTPError()

    # HTTP 200 — read and check content hash
    logger.debug(feed + ":HTTP success")
    logger.trace(feed + ":reading http response")
    http_data = await http_response.read()

    new_hash = hashlib.sha256(http_data).hexdigest()
    if new_hash == stored_hash:
        logger.debug("%s:content hash unchanged; skipping parse", feed)
        http_response.close()
        raise HTTPNotModified()

    return http_data, new_hash


def _parse_feed(http_data, feed):
    """Parse raw feed bytes and log any bozo/empty-entry warnings."""
    logger.trace(feed + ":parsing http data")
    feed_data = feedparser.parse(http_data)
    logger.trace(feed + ":done fetching")
    if len(feed_data.entries) == 0:
        if feed_data.bozo or not feed_data.version:
            logger.warning(
                "%s:HTTP 200 but parsed 0 entries from %d bytes%s",
                feed,
                len(http_data),
                (" - bozo: %r" % feed_data.bozo_exception) if feed_data.bozo else "",
            )
        else:
            logger.debug("%s:feed parsed cleanly but currently has 0 entries", feed)
    elif feed_data.bozo:
        logger.info(
            "%s:parsed %d entries but feedparser set bozo: %r",
            feed,
            len(feed_data.entries),
            getattr(feed_data, "bozo_exception", None),
        )
    return feed_data


def _store_feed_cache(conn, http_response, new_hash, feed, feed_url):
    """Persist etag, lastmodified, and content hash from a successful fetch."""
    if "ETAG" in http_response.headers:
        etag = http_response.headers["ETAG"]
        logger.trace(feed + ":saving etag: " + etag)
        conn.execute(
            "UPDATE feed_info SET etag=? where feed=? or url=?",
            [etag, feed, feed_url],
        )
        logger.trace(feed + ":etag saved")
    else:
        logger.trace(feed + ":no etag")
    if "LAST-MODIFIED" in http_response.headers:
        modified = http_response.headers["LAST-MODIFIED"]
        logger.trace(feed + ":saving lastmodified: " + modified)
        conn.execute(
            "UPDATE feed_info SET lastmodified=? where feed=? or url=?",
            [modified, feed, feed_url],
        )
        logger.trace(feed + ":saved lastmodified")
    else:
        logger.trace(feed + ":no last modified date")
    conn.execute(
        "UPDATE feed_info SET content_hash=? WHERE feed=? OR url=?",
        [new_hash, feed, feed_url],
    )


def _get_item_id(item, feed):
    """Return the best available unique id for a feed item, or None."""
    if item.get("id") is not None:
        return item.get("id")
    if item.get("guid") is not None:
        return item.get("guid")
    if item.get("link") is not None:
        return item.get("link")
    logger.error(feed + ":item:no itemid, skipping")
    return None


async def _apply_channel_filter(channel, item, FEED, feed):
    """Return True if item passes the filter configured for this channel."""
    filter_field = FEED.get(
        channel["name"] + ".filter_field",
        FEED.get("filter_field", "title"),
    )
    if channel["name"] + ".filter" in FEED or "filter" in FEED:
        logger.debug(feed + ":item:running filter for" + channel["name"])
        regexpat = FEED.get(
            channel["name"] + ".filter",
            FEED.get("filter", "^.*$"),
        )
        logger.info(
            feed
            + ":item:using filter:"
            + regexpat
            + " on "
            + item.get("title", "?")
            + " field "
            + filter_field
        )
        match = re.search(
            regexpat, await process_field(filter_field, item, FEED, channel)
        )
        if match is None:
            logger.info(feed + ":item:failed filter for " + channel["name"])
            return False
        return True
    if channel["name"] + ".filter_exclude" in FEED or "filter_exclude" in FEED:
        logger.debug(feed + ":item:running exclude filter for" + channel["name"])
        regexpat = FEED.get(
            channel["name"] + ".filter_exclude",
            FEED.get("filter_exclude", "^.*$"),
        )
        logger.info(
            feed
            + ":item:using filter_exclude:"
            + regexpat
            + " on "
            + item.get("title", "?")
            + " field "
            + filter_field
        )
        match = re.search(
            regexpat, await process_field(filter_field, item, FEED, channel)
        )
        if match is not None:
            logger.info(feed + ":item:failed exclude filter for " + channel["name"])
            return False
        logger.info(feed + ":item:passed exclude filter for " + channel["name"])
        return True
    logger.debug(feed + ":item:no filter configured for" + channel["name"])
    return True


async def _process_item(
    item, itemid, pubdate, feed, FEED, channels, asyncioloop, conn, max_age
):
    """Mark item seen, check age, and send to each channel that passes its filter."""
    conn.execute(
        "INSERT INTO feed_items (id,published) VALUES (?,?)",
        [itemid, pubdate.isoformat()],
    )
    time_since_published = datetime.now(timezone.utc) - pubdate
    logger.trace(
        "%s:time_since_published.total_seconds:%s,max_age:%s",
        feed,
        time_since_published.total_seconds(),
        max_age,
    )
    if time_since_published.total_seconds() >= max_age:
        logger.verbose("%s:too old, skipping", feed)
        logger.verbose("%s:now:now:%s", feed, time.time())
        logger.verbose("%s:now:gmtime:%s", feed, time.gmtime())
        logger.verbose("%s:now:localtime:%s", feed, time.localtime())
        logger.verbose("%s:pubDate:%r", feed, pubdate)
        logger.verbose(item)
        return
    logger.info(feed + ":item:fresh and ready for parsing")
    for channel in channels:
        if await _apply_channel_filter(channel, item, FEED, feed):
            logger.debug(feed + ":item:building message for " + channel["name"])
            message = await build_message(FEED, item, channel)
            logger.info(
                feed + ":item:sending message (eventually) to " + channel["name"]
            )
            await send_message_wrapper(
                asyncioloop, FEED, feed, channel, client, message
            )
        else:
            logger.info(
                feed
                + ":item:skipping item due to not passing filter for "
                + channel["name"]
            )


async def background_check_feed(feed, asyncioloop):
    """Poll one feed forever: fetch, parse, dedupe, filter, and send new items. Called by main() via loop.create_task()."""
    # Try to wait until Discord client has connected, etc:
    await asyncio.sleep(5)
    await client.wait_until_ready()
    # make sure debug output has this check run in the right order...
    await asyncio.sleep(1)

    user_agent = config["MAIN"].get("user_agent", USER_AGENT)

    # just a bit easier to use...
    FEED = config[feed]

    # pull config for this feed out:
    feed_url = FEED.get("feed_url")
    logger.info("Starting feed: %s (%s)", feed, feed_url)
    if not feed_url:
        logger.warning("%s: no feed_url configured — feed will never fetch", feed)
    rss_refresh_time = FEED.getint("rss_refresh_time", 3600)
    start_skew = FEED.getint("start_skew", rss_refresh_time)
    start_skew_min = FEED.getint("start_skew_min", 1)
    max_age = FEED.getint("max_age", 86400)
    # Cap for the exponential backoff applied on rate-limit/overload responses.
    backoff_max = FEED.getint("backoff_max", 86400)

    channels = _resolve_channels(feed, FEED, config, client)

    if start_skew > 0:
        sleep_time = random.uniform(start_skew_min, start_skew)
        logger.debug("%s:start_skew:sleeping for %.1f seconds", feed, sleep_time)
        await asyncio.sleep(sleep_time)

    # One HTTP session per feed task, reused across polls instead of opening
    # (and tearing down) a fresh one on every fetch.
    httpclient = aiohttp.ClientSession()

    # Interval between polls.  Starts at the configured refresh time, doubles
    # (up to backoff_max) when the feed rate-limits us, and resets on success.
    current_refresh = rss_refresh_time

    # Basically run forever
    while True:
        # And try to catch all the exceptions and just keep going
        # (but see list of except/finally stuff below)
        conn = None
        try:
            # set current "game played" constantly so that it sticks around
            gameplayed = MAIN.get("gameplayed", "gitlab.com/ffreiheit/discord_feedbot")
            await client.change_presence(activity=discord.Game(name=gameplayed))

            logger.info(feed + ": processing feed")

            db_path = config["MAIN"].get("db_path", "feed2discord.db")
            logger.trace(feed + ":db_debug:db_path=" + db_path)
            conn = get_sql_connection(config)
            logger.trace(feed + ":db_debug:conn=" + type(conn).__name__)

            lastmodified, etag, stored_hash = _load_feed_cache(conn, feed, feed_url)
            # Only advertise encodings we can always decode.  aiohttp would
            # otherwise add "br", but some servers emit a brotli stream that
            # even brotlicffi can't decode (raising ClientPayloadError and
            # silently killing the feed); gzip/deflate are stdlib-backed.
            http_headers = {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
            if lastmodified:
                http_headers["If-Modified-Since"] = lastmodified
            if etag:
                http_headers["If-None-Match"] = etag

            logger.debug(feed + ":sending http request for " + feed_url)
            # Send actual request.  await can yield control to another instance.
            http_response = await httpclient.get(feed_url, headers=http_headers)

            # Rate-limited / overloaded: exponentially back off how often we
            # poll this feed (double the interval, capped at backoff_max).
            # Handled here (not in _read_feed_response) so current_refresh stays
            # in one place.
            if http_response.status in BACKOFF_STATUSES:
                current_refresh = min(current_refresh * 2, backoff_max)
                logger.warning(
                    "%s:HTTP %s (rate-limited/overloaded); backing off, "
                    "next check in %d seconds",
                    feed,
                    http_response.status,
                    current_refresh,
                )
                http_response.close()
                raise HTTPError()

            http_data, new_hash = await _read_feed_response(
                http_response, feed, stored_hash
            )

            # Server responded with 200; clear any previous backoff.
            if current_refresh != rss_refresh_time:
                logger.warning(
                    "%s:recovered; refresh interval back to %d seconds",
                    feed,
                    rss_refresh_time,
                )
                current_refresh = rss_refresh_time

            # send_typing is configurable per-room.  Only do it now that we
            # know the feed actually changed (HTTP 200, not a 304/not-modified),
            # so we don't ping "typing..." on every no-op poll.
            await maybe_send_typing(FEED, feed, channels)

            feed_data = _parse_feed(http_data, feed)
            _store_feed_cache(conn, http_response, new_hash, feed, feed_url)
            http_response.close()

            # Process all of the entries in the feed
            # Use reversed to start with end, which is usually oldest
            logger.trace(feed + ":processing entries")
            for item in reversed(feed_data.entries):
                itemid = _get_item_id(item, feed)
                if not itemid:
                    continue

                pubdate = await extract_best_item_date(item, TIMEZONE)
                logger.trace(
                    "%s:item:processing this entry:%s:%s",
                    feed,
                    itemid,
                    pubdate.isoformat(),
                )
                logger.trace(feed + ":item:itemid:" + itemid)
                logger.trace(feed + ":item:checking database history for this item")
                if conn.execute(
                    "SELECT 1 FROM feed_items WHERE id=?", [itemid]
                ).fetchone():
                    logger.trace(feed + ":item:" + itemid + " seen before, skipping")
                    continue

                logger.info(feed + ":item " + itemid + " unseen, processing:")
                await _process_item(
                    item,
                    itemid,
                    pubdate,
                    feed,
                    FEED,
                    channels,
                    asyncioloop,
                    conn,
                    max_age,
                )

        # This is completely expected behavior for a well-behaved feed:
        except HTTPNotModified:
            current_refresh = rss_refresh_time
            logger.debug(
                feed + ":Headers indicate feed unchanged since last time fetched:"
            )
            logger.trace("%s:exc_info: %s", feed, sys.exc_info())
        # Many feeds have random periodic problems that shouldn't cause
        # permanent death.  The specific status was already logged above (the
        # status / backoff line), so retry quietly -- the bare HTTPError we
        # raise as our "non-200" signal carries no detail worth a WARNING.
        # (Genuinely unexpected errors are caught by `except Exception` below,
        # which logs a full traceback.)
        except HTTPError:
            logger.debug("%s:HTTP error, treating as transient; will retry later", feed)
            logger.trace("%s:exc_info: %s", feed, sys.exc_info())
        # sqlite3 errors are probably really bad and we should just totally
        # give up on life
        except sqlite3.Error:
            logger.exception("%s:sqlite error", feed)
            logger.trace("%s:exc_info: %s", feed, sys.exc_info())
            raise
        # Ideally we'd remove the specific channel or something...
        # But I guess just throw an error into the log and try again later...
        except discord.errors.Forbidden:
            logger.error(
                "%s:discord.errors.Forbidden — bot may lack permission in a channel",
                feed,
            )
            logger.trace("%s:exc_info: %s", feed, sys.exc_info())
            # raise # or not? hmm...
        # Transient network problems -- server dropped the connection, connection
        # refused/reset, request timed out, etc.  Like the HTTP errors above these
        # are expected and self-heal on the next poll, so log one concise line
        # (with the feed name) instead of a scary "unexpected error" traceback.
        except (aiohttp.ClientError, asyncio.TimeoutError) as neterr:
            logger.warning(
                "%s:network error (%s); will retry later", feed, type(neterr).__name__
            )
        # unknown error: definitely give up and die and move on
        except Exception:
            logger.exception("%s:Unexpected error - giving up", feed)
            # Don't raise?
            # raise
        # No matter what goes wrong, wait same time and try again
        finally:
            # One commit per poll (instead of after every write) -- far fewer
            # fsyncs -- then close the connection (else it leaks until GC,
            # "ResourceWarning: unclosed database").  Committing here flushes
            # whatever this poll wrote, however the poll ended.
            if conn is not None:
                try:
                    conn.commit()
                except sqlite3.Error:
                    pass
                conn.close()
            logger.info(feed + ":sleeping for " + str(current_refresh) + " seconds")
            await asyncio.sleep(current_refresh)


@client.event
async def on_ready():
    """Log connection details and set bot avatar on startup. Called by discord.py when the client is ready."""
    logger.notice(
        "Connected to Discord as %s (id=%s) on %d guild(s)",
        client.user.name,
        client.user.id,
        len(client.guilds),
    )

    # set avatar if specified
    avatar_file_name = MAIN.get("avatarfile")
    if avatar_file_name:
        with open(avatar_file_name, "rb") as f:
            avatar = f.read()
        await client.user.edit(avatar=avatar)


@client.event
async def on_disconnect():
    """Log disconnection. Called by discord.py when the WebSocket closes."""
    logger.notice("Disconnected from Discord")


@client.event
async def on_resumed():
    """Log session resumption. Called by discord.py when the gateway reconnects."""
    logger.notice("Reconnected to Discord (session resumed)")


def main():
    """Create the asyncio event loop, launch one task per feed, and run the Discord client. Called from __main__."""
    # Create our own loop instead of asyncio.get_event_loop(), which is
    # deprecated (and slated for removal) when called with no running loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    feeds = get_feeds_config(config)
    logger.notice(
        "Starting up feed2discord v%s with %d feed(s)", __version__, len(feeds)
    )
    sql_maintenance(config)

    try:
        for feed in feeds:
            loop.create_task(background_check_feed(feed, loop))
        loop.run_until_complete(client.login(MAIN.get("login_token")))
        loop.run_until_complete(client.connect())
    except Exception:
        loop.run_until_complete(client.close())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

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


__version__ = "3.2.0"

TRACE_LEVEL = 5
VERBOSE_LEVEL = 8
NOTICE_LEVEL = 35
logging.addLevelName(TRACE_LEVEL, "TRACE")
logging.addLevelName(VERBOSE_LEVEL, "VERBOSE")
logging.addLevelName(NOTICE_LEVEL, "NOTICE")


def _logger_trace(self, message, *args, **kwargs):
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, message, args, **kwargs)


def _logger_verbose(self, message, *args, **kwargs):
    if self.isEnabledFor(VERBOSE_LEVEL):
        self._log(VERBOSE_LEVEL, message, args, **kwargs)


def _logger_notice(self, message, *args, **kwargs):
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
    version = "%(prog)s {}".format(__version__)
    p = ArgumentParser(prog=PROG_NAME)
    p.add_argument("--version", action="version", version=version)
    p.add_argument("--config")

    return p.parse_args()


def get_config():
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
    import pytz

    tzstr = config["MAIN"].get("timezone", "utc")
    # This has to work on both windows and unix
    try:
        timezone = pytz.timezone(tzstr)
    except Exception:
        timezone = pytz.utc

    return timezone


def get_feeds_config(config):
    feeds = list(config.sections())

    # remove non-feed sections
    feeds.remove("MAIN")
    feeds.remove("CHANNELS")

    return feeds


def get_sql_connection(config):
    db_path = config["MAIN"].get("db_path", "feed2discord.db")
    conn = sqlite3.connect(db_path)
    # WAL: cheaper commits (~0.8ms vs ~1.9ms fsync) and concurrent reads while
    # writing.  It's a persistent property of the DB file, so this is idempotent
    # after the first connection converts it.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def sql_maintenance(config):
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
    for row_id, raw_date in bad_rows:
        try:
            parsed = parse_datetime(raw_date, tzinfos=TZINFOS)
            conn.execute(
                "UPDATE feed_items SET published=? WHERE id=?",
                [parsed.astimezone(timezone.utc).isoformat(), row_id],
            )
        except Exception:
            pass
    if bad_rows:
        logger.notice(
            "migrate_db: normalized %d stale published date(s)", len(bad_rows)
        )


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
    # This function loops through all the common date fields for an item in
    # a feed and returns the "best" one as a UTC-aware datetime (we keep and
    # compare all times in UTC).  Falls back to "now" if nothing is found.
    fields = ("published", "pubDate", "date", "created", "updated", "expiry")
    for date_field in fields:
        if item.get(date_field) and len(item[date_field]) > 0:
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
    global_send_typing = conf.getint("send_typing", 0)
    return conf.getint("%s.send_typing" % (feed_name), global_send_typing)


async def maybe_send_typing(FEED, feed, channels):
    # Trigger a "typing..." indicator in each channel (if configured).
    # discord.py silently sleeps+retries on a 429, so we bound the wait: if
    # typing is being rate-limited badly, give up and stop sending typing for
    # this feed until restart, rather than piling on more requests.
    if feed in typing_disabled or not await should_send_typing(FEED, feed):
        return
    for channel in channels:
        try:
            await asyncio.wait_for(channel["object"].typing(), timeout=5)
            logger.verbose("%s:%s:sent typing", feed, channel["name"])
        except discord.errors.Forbidden:
            logger.exception(
                "%s:%s:forbidden - is bot allowed in channel?", feed, channel
            )
        except (asyncio.TimeoutError, discord.errors.RateLimited):
            typing_disabled.add(feed)
            logger.warning(
                "%s:typing rate-limited; disabling send_typing for this feed "
                "until restart",
                feed,
            )
            return


# This looks at the field from the config, and returns the processed string
# naked item in fields: return that field from the feed item
# *, **, _, ~, `, ```: markup the field and return it from the feed item
# " around the field: string literal
# Added new @, turns each comma separated tag into a group mention
async def process_field(field, item, FEED, channel):
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
    stringmatch = re.match('^"(.+?)"$', field)
    highlightmatch = re.match("^([*_~<]+)(.+?)([*_~>]+)$", field)
    bigcodematch = re.match("^```(.+)```$", field)
    codematch = re.match("^`(.+)`$", field)

    tagmatch = re.match("^@(.+)$", field)  # new tag regex
    dictmatch = re.match(r"^\[(.+)\](.+)\.(.+)$", field)  # new dict regex

    if stringmatch is not None:
        # Return an actual string literal from config:
        logger.trace("%s:process_field:%s:isString", FEED, field)
        return stringmatch.group(1)  # string from config
    elif highlightmatch is not None:
        logger.trace("%s:process_field:%s:isHighlight", FEED, field)

        # If there's any markdown on the field, return field with that
        # markup on it:
        begin, field, end = highlightmatch.groups()
        if item.get(field) is not None:
            if field == "link":
                url = urljoin(FEED.get("feed-url"), item[field])
                return begin + url + end
            else:
                return begin + html.unescape(item[field]) + end
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    elif bigcodematch is not None:
        logger.trace("%s:process_field:%s:isCodeBlock", FEED, field)

        # Code blocks are a bit different, with a newline and stuff:
        field = bigcodematch.group(1)
        if item.get(field) is not None:
            return "```\n%s\n```" % (html.unescape(item[field]))
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    elif codematch is not None:
        logger.trace("%s:process_field:%s:isCode", FEED, field)

        # Since code chunk can't have other highlights, also do them
        # separately:
        field = codematch.group(1)
        if item.get(field) is not None:
            return "`%s`" % (html.unescape(item[field]))
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    elif tagmatch is not None:
        logger.trace("%s:process_field:%s:isTag", FEED, field)
        field = tagmatch.group(1)
        if item.get(field) is not None:
            # Assuming tags are ', ' separated
            taglist = item[field].split(", ")
            # Iterate through channel roles, see if a role is mentionable and
            # then substitute the role for its id
            for role in channel["object"].guild.roles:
                rn = str(role.name)
                taglist = ["<@&%s>" % (role.id) if rn == str(i) else i for i in taglist]
            return ", ".join(taglist)
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    elif dictmatch is not None:
        logger.trace("%s:process_field:%s:isDict", FEED, field)
        delim = dictmatch.group(1)
        field = dictmatch.group(2)
        dictkey = dictmatch.group(3)
        if item.get(field) is not None:
            return delim.join([x[dictkey] for x in item[field]])
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    else:
        logger.trace("%s:process_field:%s:isPlain", FEED, field)
        # Just asking for plain field:
        if item.get(field) is not None:
            # If field is special field "link",
            # then use urljoin to turn relative URLs into absolute URLs
            if field == "link":
                return urljoin(FEED.get("feed_url"), item[field])
            # Else assume it's a "summary" or "content" or whatever field
            # and turn HTML into markdown and don't add any markup:
            else:
                htmlfixer = HTML2Text()
                htmlfixer.ignore_links = True
                htmlfixer.ignore_images = True
                htmlfixer.ignore_emphasis = False
                htmlfixer.body_width = 1000
                htmlfixer.unicode_snob = True
                htmlfixer.ul_item_mark = "-"  # Default of "*" likely
                # to bold things, etc...
                markdownfield = htmlfixer.handle(html.unescape(item[field]))

                # Try to strip any remaining HTML out.  Not "safe", but
                # simple and should catch most stuff:
                markdownfield = re.sub("<[^<]+?>", "", markdownfield)
                return markdownfield
        else:
            logger.error("process_field:%s:no such field", field)
            return ""


# This builds a message.
#
# Pulls the fields (trying for channel_name.fields in FEED, then fields in
# FEED, then fields in DEFAULT, then "id,description".
# fields in config is comma separate string, so pull into array.
# then just adds things, separated by newlines.
# truncates if too long.


async def build_message(FEED, item, channel):
    message = ""
    fieldlist = FEED.get(
        channel["name"] + ".fields", FEED.get("fields", "id,description")
    ).split(",")
    # Extract fields in order
    for field in fieldlist:
        logger.trace("feed:item:build_message:%s:added to message", field)
        message += await process_field(field, item, FEED, channel) + "\n"

    # Naked spaces are terrible:
    message = re.sub(" +\n", "\n", message)
    message = re.sub("\n +", "\n", message)

    # squash newlines down to single ones, and do that last...
    message = re.sub("(\n)+", "\n", message)

    if len(message) > 1800:
        message = message[:1800] + "\n... post truncated ..."
    return message


# This schedules an 'actually_send_message' coroutine to run
async def send_message_wrapper(asyncioloop, FEED, feed, channel, client, message):
    delay = FEED.getint(channel["name"] + ".delay", FEED.getint("delay", 0))
    logger.debug(
        feed + ":" + channel["name"] + ":scheduling message with delay of " + str(delay)
    )
    asyncioloop.create_task(actually_send_message(channel, message, delay, FEED, feed))
    logger.debug(feed + ":" + channel["name"] + ":message scheduled")


# Simply sleeps for delay and then sends message.


async def actually_send_message(channel, message, delay, FEED, feed):
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


# The main work loop
# One of these is run for each feed.
# It's an asyncio thing. "await" (sleep or I/O) returns to main loop
# and gives other feeds a chance to run.
async def background_check_feed(feed, asyncioloop):

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

    # loop through all the channels this feed is configured to send to
    channels = []
    for key in FEED.get("channels").split(","):
        # stick a dict in the channels array so we have more to work with
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

            # Only advertise encodings we can always decode.  aiohttp would
            # otherwise add "br", but some servers emit a brotli stream that
            # even brotlicffi can't decode (raising ClientPayloadError and
            # silently killing the feed); gzip/deflate are stdlib-backed.
            http_headers = {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            }

            db_path = config["MAIN"].get("db_path", "feed2discord.db")

            # Debugging crazy issues
            logger.trace(feed + ":db_debug:db_path=" + db_path)

            conn = get_sql_connection(config)

            # Download the actual feed, if changed since last fetch

            # Debugging crazy issues
            logger.trace(feed + ":db_debug:conn=" + type(conn).__name__)

            # pull data about history of this *feed* from DB:
            cursor = conn.execute(
                "select lastmodified,etag,content_hash from feed_info where feed=? OR url=?",
                [feed, feed_url],
            )
            data = cursor.fetchone()

            # If we've handled this feed before,
            # and we have etag from last run, add etag to headers.
            # and if we have a last modified time from last run,
            # add "If-Modified-Since" to headers.
            if data is None:  # never handled this feed before...
                logger.trace(feed + ":looks like updated version. saving info")
                conn.execute(
                    "REPLACE INTO feed_info (feed,url) VALUES (?,?)", [feed, feed_url]
                )
                logger.trace(feed + ":feed info saved")
            else:
                logger.trace(feed + ":setting up extra headers for HTTP request.")
                lastmodified = data[0]
                etag = data[1]
                if lastmodified is not None and len(lastmodified):
                    logger.trace(
                        feed + ":adding header If-Modified-Since: " + lastmodified
                    )
                    http_headers["If-Modified-Since"] = lastmodified
                else:
                    logger.trace(feed + ":no stored lastmodified")
                if etag is not None and len(etag):
                    logger.trace(feed + ":adding header If-None-Match: " + etag)
                    http_headers["If-None-Match"] = etag
                else:
                    logger.trace(feed + ":no stored ETag")

            logger.debug(feed + ":sending http request for " + feed_url)
            # Send actual request.  await can yield control to another
            # instance.
            http_response = await httpclient.get(feed_url, headers=http_headers)

            logger.trace("%s:%s", feed, http_response)

            # First check that we didn't get a "None" response, since that's
            # some sort of internal error thing:
            if http_response.status is None:
                logger.error(feed + ":HTTP response code is NONE")
                raise HTTPError()
            # Some feeds are smart enough to use that if-modified-since or
            # etag info, which gives us a 304 status.  If that happens,
            # assume no new items, fall through rest of this and try again
            # later.
            elif http_response.status == 304:
                logger.debug(feed + ":data is old; moving on")
                current_refresh = rss_refresh_time  # server responded fine
                http_response.close()
                raise HTTPNotModified()
            # Rate-limited / overloaded: exponentially back off how often we
            # poll this feed (double the interval, capped at backoff_max).
            elif http_response.status in BACKOFF_STATUSES:
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
            # If we get anything but a 200, that's a problem and we don't
            # have good data, so give up and try later.
            # Mostly handled different than 304/not-modified to make logging
            # clearer.
            elif http_response.status != 200:
                logger.warning(
                    "%s:unexpected HTTP status %s", feed, http_response.status
                )
                http_response.close()
                raise HTTPError()
            else:
                logger.debug(feed + ":HTTP success")
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

            # pull data out of the http response
            logger.trace(feed + ":reading http response")
            http_data = await http_response.read()

            # For feeds that don't support ETag or Last-Modified, compare a
            # hash of the raw body to detect unchanged content and skip
            # re-parsing (equivalent to a 304 Not Modified).
            new_hash = hashlib.sha256(http_data).hexdigest()
            stored_hash = data[2] if data is not None else None
            if new_hash == stored_hash:
                logger.debug("%s:content hash unchanged; skipping parse", feed)
                current_refresh = rss_refresh_time
                http_response.close()
                raise HTTPNotModified()

            # parse the data from the http response with feedparser
            logger.trace(feed + ":parsing http data")
            feed_data = feedparser.parse(http_data)
            logger.trace(feed + ":done fetching")

            # Surface parse problems that would otherwise be invisible.  Zero
            # entries together with a parse error (bozo) or an unrecognized
            # format usually means a malformed/undecodable body (e.g. a
            # compression we couldn't handle) -- how a feed silently goes quiet.
            # A well-formed feed that simply has no items right now (e.g. a
            # security feed with no current updates) is normal -> only INFO.
            if len(feed_data.entries) == 0:
                if feed_data.bozo or not feed_data.version:
                    logger.warning(
                        "%s:HTTP 200 but parsed 0 entries from %d bytes%s",
                        feed,
                        len(http_data),
                        (" - bozo: %r" % feed_data.bozo_exception)
                        if feed_data.bozo
                        else "",
                    )
                else:
                    logger.debug(
                        "%s:feed parsed cleanly but currently has 0 entries",
                        feed,
                    )
            elif feed_data.bozo:
                logger.info(
                    "%s:parsed %d entries but feedparser set bozo: %r",
                    feed,
                    len(feed_data.entries),
                    getattr(feed_data, "bozo_exception", None),
                )

            # If we got an ETAG back in headers, store that, so we can
            # include on next fetch
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

            # If we got a Last-Modified header back, store that, so we can
            # include on next fetch
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

            http_response.close()

            # Process all of the entries in the feed
            # Use reversed to start with end, which is usually oldest
            logger.trace(feed + ":processing entries")
            for item in reversed(feed_data.entries):
                # Pull out the unique id, or just give up on this item.
                itemid = ""
                if item.get("id"):
                    itemid = item.id
                elif item.get("guid"):
                    itemid = item.guid
                elif item.get("link"):
                    itemid = item.link
                else:
                    logger.error(feed + ":item:no itemid, skipping")
                    continue

                # Get our best date out, in both raw and parsed form
                pubdate = await extract_best_item_date(item, TIMEZONE)
                # ISO-8601 so SQLite's julianday() can parse it (used by
                # SQL_CLEAN_OLD_ITEMS); the old ctime-style format returned NULL.
                pubdate_fmt = pubdate.isoformat()

                logger.trace(
                    "%s:item:processing this entry:%s:%s",
                    feed,
                    itemid,
                    pubdate_fmt,
                )

                logger.trace(feed + ":item:itemid:" + itemid)
                logger.trace(feed + ":item:checking database history for this item")
                # Check DB for this item
                cursor = conn.execute(
                    "SELECT 1 FROM feed_items WHERE id=?",
                    [itemid],
                )
                data = cursor.fetchone()

                # If we've never seen it before, then actually processing
                # this:
                if data is None:
                    logger.info(feed + ":item " + itemid + " unseen, processing:")

                    # Store info about this item, so next time we skip it:
                    conn.execute(
                        "INSERT INTO feed_items (id,published) VALUES (?,?)",
                        [itemid, pubdate_fmt],
                    )

                    # Doing some crazy date math stuff...
                    # max_age is mostly so that first run doesn't spew too
                    # much stuff into a room, but is also a useful safety
                    # measure in case a feed suddenly reverts to something
                    # ancient or other weird problems...
                    time_since_published = datetime.now(timezone.utc) - pubdate

                    logger.trace(
                        "%s:time_since_published.total_seconds:%s,max_age:%s",
                        feed,
                        time_since_published.total_seconds(),
                        max_age,
                    )

                    if time_since_published.total_seconds() < max_age:
                        logger.info(feed + ":item:fresh and ready for parsing")

                        # Loop over all channels for this particular feed
                        # and process appropriately:
                        for channel in channels:
                            include = True
                            filter_field = FEED.get(
                                channel["name"] + ".filter_field",
                                FEED.get("filter_field", "title"),
                            )
                            # Regex if channel exists
                            if (
                                channel["name"] + ".filter"
                            ) in FEED or "filter" in FEED:
                                logger.debug(
                                    feed + ":item:running filter for" + channel["name"]
                                )
                                regexpat = FEED.get(
                                    channel["name"] + ".filter",
                                    FEED.get("filter", "^.*$"),
                                )
                                logger.info(
                                    feed
                                    + ":item:using filter:"
                                    + regexpat
                                    + " on "
                                    + item["title"]
                                    + " field "
                                    + filter_field
                                )
                                regexmatch = re.search(
                                    regexpat,
                                    await process_field(
                                        filter_field, item, FEED, channel
                                    ),
                                )
                                if regexmatch is None:
                                    include = False
                                    logger.info(
                                        feed
                                        + ":item:failed filter for "
                                        + channel["name"]
                                    )
                            elif (
                                channel["name"] + ".filter_exclude"
                            ) in FEED or "filter_exclude" in FEED:
                                logger.debug(
                                    feed
                                    + ":item:running exclude filter for"
                                    + channel["name"]
                                )
                                regexpat = FEED.get(
                                    channel["name"] + ".filter_exclude",
                                    FEED.get("filter_exclude", "^.*$"),
                                )
                                logger.info(
                                    feed
                                    + ":item:using filter_exclude:"
                                    + regexpat
                                    + " on "
                                    + item["title"]
                                    + " field "
                                    + filter_field
                                )
                                regexmatch = re.search(
                                    regexpat,
                                    await process_field(
                                        filter_field, item, FEED, channel
                                    ),
                                )
                                if regexmatch is None:
                                    include = True
                                    logger.info(
                                        feed
                                        + ":item:passed exclude filter for "
                                        + channel["name"]
                                    )
                                else:
                                    include = False
                                    logger.info(
                                        feed
                                        + ":item:failed exclude filter for "
                                        + channel["name"]
                                    )
                            else:
                                include = True  # redundant safety net
                                logger.debug(
                                    feed
                                    + ":item:no filter configured for"
                                    + channel["name"]
                                )

                            if include is True:
                                logger.debug(
                                    feed
                                    + ":item:building message for "
                                    + channel["name"]
                                )
                                message = await build_message(FEED, item, channel)
                                logger.info(
                                    feed
                                    + ":item:sending message (eventually) to "
                                    + channel["name"]
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

                    else:
                        # Logs of debugging info for date handling stuff...
                        logger.verbose("%s:too old, skipping", feed)
                        logger.verbose("%s:now:now:%s", feed, time.time())
                        logger.verbose("%s:now:gmtime:%s", feed, time.gmtime())
                        logger.verbose("%s:now:localtime:%s", feed, time.localtime())
                        logger.verbose("%s:pubDate:%r", feed, pubdate)
                        logger.verbose(item)
                # seen before, move on:
                else:
                    logger.trace(feed + ":item:" + itemid + " seen before, skipping")
        # This is completely expected behavior for a well-behaved feed:
        except HTTPNotModified:
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
            logger.exception("Unexpected error - giving up")
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
    logger.notice("Disconnected from Discord")


@client.event
async def on_resumed():
    logger.notice("Reconnected to Discord (session resumed)")


# Set up the tasks for each feed and start the main event loop thing.
# In this __main__ thing so can be used as library.
def main():
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
        if "login_token" in MAIN:
            loop.run_until_complete(client.login(MAIN.get("login_token")))
        else:
            loop.run_until_complete(
                client.login(MAIN.get("login_email"), MAIN.get("login_password"))
            )
        loop.run_until_complete(client.connect())
    except Exception:
        loop.run_until_complete(client.close())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

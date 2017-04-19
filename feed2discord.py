#!/usr/bin/env python3
# Copyright (c) 2016-2017 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

import asyncio
import logging
import os
import random
import re
import sqlite3
import sys
import time
import warnings

from argparse import ArgumentParser
from configparser import ConfigParser
from datetime import datetime
from importlib import reload
from urllib.parse import urljoin

import aiohttp
import discord
import feedparser

from aiohttp.web_exceptions import HTTPError, HTTPNotModified
from dateutil.parser import parse as parse_datetime
from html2text import HTML2Text


__version__ = "2.4.0"


PROG_NAME = "feedbot"
USER_AGENT = "%s/%s" % (PROG_NAME, __version__)

SQL_CREATE_FEED_INFO_TBL = """
CREATE TABLE IF NOT EXISTS feed_info (
    feed text PRIMARY KEY,
    url text UNIQUE,
    lastmodified text,
    etag text
)
"""

SQL_CREATE_FEED_ITEMS_TBL = """
CREATE TABLE IF NOT EXISTS feed_items (
    id text PRIMARY KEY,
    published text,
    title text,
    url text,
    reposted text
)
"""

SQL_CLEAN_OLD_ITEMS = """
DELETE FROM feed_items WHERE (julianday() - julianday(published)) > 365
"""


if not sys.version_info[:2] >= (3, 4):
    print("Error: requires python 3.4 or newer")
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


def parse_args():
    version = "%(prog)s {}".format(__version__)
    p = ArgumentParser(prog=PROG_NAME)
    p.add_argument("--version", action="version", version=version)
    p.add_argument("--config")

    return p.parse_args()


def get_config():
    args = parse_args()
    config = ConfigParser()
    if args.config:
        config.read(args.config)
    else:
        for path in DEFAULT_CONFIG_PATHS:
            if os.path.isfile(path):
                config.read(path)
                break
        else:
            raise ImproperlyConfigured("No configuration file found.")

    debug = config["MAIN"].getint("debug", 0)

    if debug:
        os.environ["PYTHONASYNCIODEBUG"] = "1"
        # The AIO modules need to be reloaded because of the new env var
        reload(asyncio)
        reload(aiohttp)
        reload(discord)

    if debug >= 3:
        log_level = logging.DEBUG
    elif debug >= 2:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    logging.basicConfig(level=log_level)
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
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


def get_sqlite_connection(config):
    db_path = config["MAIN"].get("db_path", "feed2discord.db")
    conn = sqlite3.connect(db_path)

    # If our two tables don't exist, create them.
    conn.execute(SQL_CREATE_FEED_INFO_TBL)
    conn.execute(SQL_CREATE_FEED_ITEMS_TBL)

    # Clean out *some* entries that are over 1 year old...
    # Doing this cleanup at start time because some feeds
    # do contain very old items and we don't want to keep
    # re-evaluating them.
    conn.execute(SQL_CLEAN_OLD_ITEMS)

    return conn


config, logger = get_config()

# Make main config area global, since used everywhere/anywhere
MAIN = config['MAIN']
TIMEZONE = get_timezone(config)


# Crazy workaround for a bug with parsing that doesn't apply on all
# pythons:
feedparser.PREFERRED_XML_PARSERS.remove('drv_libxml2')

# set up a single http client for everything to use.
httpclient = aiohttp.ClientSession()

# global discord client object
client = discord.Client()


def extract_best_item_date(item, tzinfo):
    # This function loops through all the common date fields for an item in
    # a feed, and extracts the "best" one.  Falls back to "now" if nothing
    # is found.
    fields = ("published", "pubDate", "date", "created", "updated")
    for date_field in fields:
        if date_field in item and len(item[date_field]) > 0:
            try:
                date_obj = parse_datetime(item[date_field])

                if date_obj.tzinfo is None:
                    tzinfo.localize(date_obj)

                return date_obj
            except Exception:
                pass

    # No potentials found, default to current timezone's "now"
    return tzinfo.localize(datetime.now())


def should_send_typing(conf, feed_name):
    global_send_typing = conf.getint("send_typing", 0)
    return conf.getint("%s.send_typing" % (feed_name), global_send_typing)


# This looks at the field from the config, and returns the processed string
# naked item in fields: return that field from the feed item
# *, **, _, ~, `, ```: markup the field and return it from the feed item
# " around the field: string literal
# Added new @, turns each comma separated tag into a group mention
def process_field(field, item, FEED, channel):
    logger.debug("%s:process_field:%s: started", FEED, field)

    item_url_base = FEED.get('item_url_base', None)
    if field == 'guid' and item_url_base is not None:
        if 'guid' in item:
            return item_url_base + item['guid']
        else:
            logger.error(
                'process_field:guid:no such field; try show_sample_entry.py on feed')
            return ''

    logger.debug("%s:process_field:%s: checking regexes", FEED, field)
    stringmatch = re.match('^"(.+?)"$', field)
    highlightmatch = re.match('^([*_~<]+)(.+?)([*_~>]+)$', field)
    bigcodematch = re.match('^```(.+)```$', field)
    codematch = re.match('^`(.+)`$', field)

    tagmatch = re.match('^@(.+)$', field)  # new tag regex

    if stringmatch is not None:
        # Return an actual string literal from config:
        logger.debug("%s:process_field:%s:isString", FEED, field)
        return stringmatch.group(1)  # string from config
    elif highlightmatch is not None:
        logger.debug("%s:process_field:%s:isHighlight", FEED, field)

        # If there's any markdown on the field, return field with that
        # markup on it:
        begin, field, end = highlightmatch.groups()
        if field in item:
            if field == "link":
                url = urljoin(FEED.get("feed-url"), item[field])
                return begin + url + end
            else:
                return begin + item[field] + end
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    elif bigcodematch is not None:
        logger.debug("%s:process_field:%s:isCodeBlock", FEED, field)

        # Code blocks are a bit different, with a newline and stuff:
        field = bigcodematch.group(1)
        if field in item:
            return "```\n%s\n```" % (item[field])
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    elif codematch is not None:
        logger.debug("%s:process_field:%s:isCode", FEED, field)

        # Since code chunk can't have other highlights, also do them
        # separately:
        field = codematch.group(1)
        if field in item:
            return "`%s`" % (item[field])
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    elif tagmatch is not None:
        logger.debug("%s:process_field:%s:isTag", FEED, field)
        field = tagmatch.group(1)
        if field in item:
            # Assuming tags are ', ' separated
            taglist = item[field].split(', ')
            # Iterate through channel roles, see if a role is mentionable and
            # then substitute the role for its id
            for role in client.get_channel(channel['id']).server.roles:
                rn = str(role.name)
                taglist = [
                    "<@&%s>" % (role.id) if rn == str(i) else i for i in taglist
                ]
                return ", ".join(taglist)
        else:
            logger.error("process_field:%s:no such field", field)
            return ""

    else:
        logger.debug("%s:process_field:%s:isPlain", FEED, field)
        # Just asking for plain field:
        if field in item:
            # If field is special field "link",
            # then use urljoin to turn relative URLs into absolute URLs
            if field == 'link':
                return urljoin(FEED.get('feed_url'), item[field])
            # Else assume it's a "summary" or "content" or whatever field
            # and turn HTML into markdown and don't add any markup:
            else:
                htmlfixer = HTML2Text()
                logger.debug(htmlfixer)
                htmlfixer.ignore_links = True
                htmlfixer.ignore_images = True
                htmlfixer.ignore_emphasis = False
                htmlfixer.body_width = 1000
                htmlfixer.unicode_snob = True
                htmlfixer.ul_item_mark = '-'  # Default of "*" likely
                # to bold things, etc...
                markdownfield = htmlfixer.handle(item[field])

                # Try to strip any remaining HTML out.  Not "safe", but
                # simple and should catch most stuff:
                markdownfield = re.sub('<[^<]+?>', '', markdownfield)
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


def build_message(FEED, item, channel):
    message = ''
    fieldlist = FEED.get(
        channel['name'] + '.fields',
        FEED.get('fields', 'id,description')
    ).split(',')
    # Extract fields in order
    for field in fieldlist:
        logger.debug("feed:item:build_message:%s:added to message", field)
        message += process_field(field, item, FEED, channel) + "\n"

    # Naked spaces are terrible:
    message = re.sub(' +\n', '\n', message)
    message = re.sub('\n +', '\n', message)

    # squash newlines down to single ones, and do that last...
    message = re.sub("(\n)+", "\n", message)

    if len(message) > 1800:
        message = message[:1800] + "\n... post truncated ..."
    return message

# This schedules an 'actually_send_message' coroutine to run


@asyncio.coroutine
def send_message_wrapper(asyncioloop, FEED, feed, channel, client, message):
    delay = FEED.getint(channel['name'] + '.delay', FEED.getint('delay', 0))
    logger.debug(feed + ':' + channel['name'] +
                 ':scheduling message with delay of ' + str(delay))
    asyncioloop.create_task(
        actually_send_message(channel, message, delay, FEED, feed))
    logger.debug(feed + ':' + channel['name'] + ':message scheduled')

# Simply sleeps for delay and then sends message.


@asyncio.coroutine
def actually_send_message(channel, message, delay, FEED, feed):
    logger.debug(
        "%s:%s:sleeping for %i seconds before sending message",
        feed, channel["name"], delay
    )

    if should_send_typing(FEED, feed):
        yield from client.send_typing(channel["object"])
    yield from asyncio.sleep(delay)

    logger.debug("%s:%s:actually sending message", feed, channel["name"])
    yield from client.send_message(channel["object"], message)

    logger.debug("%s:%s:message sent: %r", feed, channel["name"], message)

# The main work loop
# One of these is run for each feed.
# It's an asyncio thing. "yield from" (sleep or I/O) returns to main loop
# and gives other feeds a chance to run.


@asyncio.coroutine
def background_check_feed(conn, feed, asyncioloop):
    logger.info(feed + ': Starting up background_check_feed')

    # Try to wait until Discord client has connected, etc:
    yield from client.wait_until_ready()
    # make sure debug output has this check run in the right order...
    yield from asyncio.sleep(1)

    user_agent = config["MAIN"].get("user_agent", USER_AGENT)

    # just a bit easier to use...
    FEED = config[feed]

    # pull config for this feed out:
    feed_url = FEED.get('feed_url')
    rss_refresh_time = FEED.getint('rss_refresh_time', 3600)
    start_skew = FEED.getint('start_skew', rss_refresh_time)
    start_skew_min = FEED.getint('start_skew_min', 1)
    max_age = FEED.getint('max_age', 86400)

    # loop through all the channels this feed is configured to send to
    channels = []
    for key in FEED.get('channels').split(','):
        logger.debug(feed + ': adding channel ' + key)
        # stick a dict in the channels array so we have more to work with
        channels.append(
            {
                'object': discord.Object(id=config['CHANNELS'][key]),
                'name': key,
                'id': config['CHANNELS'][key],
            }
        )

    if start_skew > 0:
        sleep_time = random.uniform(start_skew_min, start_skew)
        logger.info(feed + ':start_skew:sleeping for ' + str(sleep_time))
        yield from asyncio.sleep(sleep_time)

    # Basically run forever
    while not client.is_closed:
        # And tries to catch all the exceptions and just keep going
        # (but see list of except/finally stuff below)
        try:
            logger.info(feed + ': processing feed')

            # If send_typing is on for the feed, send a little "typing ..."
            # whenever a feed is being worked on.  configurable per-room
            if should_send_typing(FEED, feed):
                for channel in channels:
                    # Since this is first attempt to talk to this channel,
                    # be very verbose about failures to talk to channel
                    try:
                        yield from client.send_typing(channel['object'])
                    except discord.errors.Forbidden:
                        logger.exception(
                            "%s:%s:forbidden - is bot allowed in channel?",
                            feed, channel
                        )

            http_headers = {"User-Agent": user_agent}

            # Download the actual feed, if changed since last fetch

            # pull data about history of this *feed* from DB:
            cursor = conn.cursor()
            cursor.execute(
                "select lastmodified,etag from feed_info where feed=? OR url=?", [
                    feed, feed_url])
            data = cursor.fetchone()

            # If we've handled this feed before,
            # and we have etag from last run, add etag to headers.
            # and if we have a last modified time from last run,
            # add "If-Modified-Since" to headers.
            if data is None:  # never handled this feed before...
                logger.info(feed + ':looks like updated version. saving info')
                cursor.execute(
                    "REPLACE INTO feed_info (feed,url) VALUES (?,?)",
                    [feed, feed_url])
                conn.commit()
                logger.debug(feed + ':feed info saved')
            else:
                logger.debug(feed +
                             ':setting up extra headers for HTTP request.')
                logger.debug(data)
                lastmodified = data[0]
                etag = data[1]
                if lastmodified is not None and len(lastmodified):
                    logger.debug(feed +
                                 ':adding header If-Modified-Since: ' +
                                 lastmodified)
                    http_headers['If-Modified-Since'] = lastmodified
                else:
                    logger.debug(feed + ':no stored lastmodified')
                if etag is not None and len(etag):
                    logger.debug(feed + ':adding header ETag: ' + etag)
                    http_headers['ETag'] = etag
                else:
                    logger.debug(feed + ':no stored ETag')

            logger.debug(feed + ':sending http request for ' + feed_url)
            # Send actual request.  yield from can yield control to another
            # instance.
            http_response = yield from httpclient.request('GET',
                                                          feed_url,
                                                          headers=http_headers)
            logger.debug(http_response)

            # First check that we didn't get a "None" response, since that's
            # some sort of internal error thing:
            if http_response.status is None:
                logger.error(feed + ':HTTP response code is NONE')
                raise HTTPError()
            # Some feeds are smart enough to use that if-modified-since or
            # etag info, which gives us a 304 status.  If that happens,
            # assume no new items, fall through rest of this and try again
            # later.
            elif http_response.status == 304:
                logger.debug(feed + ':data is old; moving on')
                http_response.close()
                raise HTTPNotModified()
            # If we get anything but a 200, that's a problem and we don't
            # have good data, so give up and try later.
            # Mostly handled different than 304/not-modified to make logging
            # clearer.
            elif http_response.status != 200:
                logger.debug(feed + ':HTTP error not 200')
                # + str(http_response.status))
                # raise HTTPError()
            else:
                logger.debug(feed + ':HTTP success')

            # pull data out of the http response
            logger.debug(feed + ':reading http response')
            http_data = yield from http_response.read()

            # parse the data from the http response with feedparser
            logger.debug(feed + ':parsing http data')
            feed_data = feedparser.parse(http_data)
            logger.debug(feed + ':done fetching')

            # If we got an ETAG back in headers, store that, so we can
            # include on next fetch
            if 'ETAG' in http_response.headers:
                etag = http_response.headers['ETAG']
                logger.debug(feed + ':saving etag: ' + etag)
                cursor.execute(
                    "UPDATE feed_info SET etag=? where feed=? or url=?",
                    [etag, feed, feed_url])
                conn.commit()
                logger.debug(feed + ':etag saved')
            else:
                logger.debug(feed + ':no etag')

            # If we got a Last-Modified header back, store that, so we can
            # include on next fetch
            if 'LAST-MODIFIED' in http_response.headers:
                modified = http_response.headers['LAST-MODIFIED']
                logger.debug(feed + ':saving lastmodified: ' + modified)
                cursor.execute(
                    "UPDATE feed_info SET lastmodified=? where feed=? or url=?", [
                        modified, feed, feed_url])
                conn.commit()
                logger.debug(feed + ':saved lastmodified')
            else:
                logger.debug(feed + ':no last modified date')

            http_response.close()

            # Process all of the entries in the feed
            # Use reversed to start with end, which is usually oldest
            logger.debug(feed + ':processing entries')
            for item in reversed(feed_data.entries):
                logger.debug("%s:item:processing this entry:%r", feed, item)

                # Pull out the unique id, or just give up on this item.
                id = ''
                if 'id' in item:
                    id = item.id
                elif 'guid' in item:
                    id = item.guid
                elif 'link' in item:
                    id = item.link
                else:
                    logger.error(feed + ':item:no id, skipping')
                    continue

                # Get our best date out, in both raw and parsed form
                pubdate = extract_best_item_date(item, TIMEZONE)
                pubdate_fmt = pubdate.strftime("%a %b %d %H:%M:%S %Z %Y")

                logger.debug(feed + ':item:id:' + id)
                logger.debug(feed +
                             ':item:checking database history for this item')
                # Check DB for this item
                cursor.execute(
                    "SELECT published,title,url,reposted FROM feed_items WHERE id=?", [id])
                data = cursor.fetchone()

                # If we've never seen it before, then actually processing
                # this:
                if data is None:
                    logger.info(feed + ':item ' + id + ' unseen, processing:')

                    # Store info about this item, so next time we skip it:
                    cursor.execute(
                        "INSERT INTO feed_items (id,published) VALUES (?,?)",
                        [id, pubdate_fmt])
                    conn.commit()

                    # Doing some crazy date math stuff...
                    # max_age is mostly so that first run doesn't spew too
                    # much stuff into a room, but is also a useful safety
                    # measure in case a feed suddenly reverts to something
                    # ancient or other weird problems...
                    time_since_published = TIMEZONE.localize(
                        datetime.now()) - pubdate.astimezone(TIMEZONE)

                    if time_since_published.total_seconds() < max_age:
                        logger.info(feed + ':item:fresh and ready for parsing')

                        # Loop over all channels for this particular feed
                        # and process appropriately:
                        for channel in channels:
                            include = True
                            filter_field = FEED.get(
                                channel['name'] + '.filter_field',
                                FEED.get('filter_field',
                                         'title'))
                            # Regex if channel exists
                            if (channel['name'] +
                                    '.filter') in FEED or 'filter' in FEED:
                                logger.debug(
                                    feed + ':item:running filter for' + channel['name'])
                                regexpat = FEED.get(
                                    channel['name'] + '.filter',
                                    FEED.get('filter', '^.*$'))
                                logger.debug(
                                    feed +
                                    ':item:using filter:' +
                                    regexpat +
                                    ' on ' +
                                    item['title'] +
                                    ' field ' +
                                    filter_field)
                                regexmatch = re.search(
                                    regexpat, item[filter_field])
                                if regexmatch is None:
                                    include = False
                                    logger.info(
                                        feed + ':item:failed filter for ' + channel['name'])
                            elif (channel['name'] + '.filter_exclude') in FEED or 'filter_exclude' in FEED:
                                logger.debug(
                                    feed + ':item:running exclude filter for' + channel['name'])
                                regexpat = FEED.get(
                                    channel['name'] + '.filter_exclude',
                                    FEED.get('filter_exclude',
                                             '^.*$'))
                                logger.debug(
                                    feed +
                                    ':item:using filter_exclude:' +
                                    regexpat +
                                    ' on ' +
                                    item['title'] +
                                    ' field ' +
                                    filter_field)
                                regexmatch = re.search(regexpat, item[filter_field])
                                if regexmatch is None:
                                    include = True
                                    logger.info(
                                        feed + ':item:passed exclude filter for ' + channel['name'])
                                else:
                                    include = False
                                    logger.info(
                                        feed + ':item:failed exclude filter for ' + channel['name'])
                            else:
                                include = True  # redundant safety net
                                logger.debug(
                                    feed + ':item:no filter configured for' + channel['name'])

                            if include is True:
                                logger.debug(
                                    feed + ':item:building message for ' + channel['name'])
                                message = build_message(FEED, item, channel)
                                logger.debug(
                                    feed + ':item:sending message (eventually) to ' + channel['name'])
                                yield from send_message_wrapper(asyncioloop,
                                                                FEED,
                                                                feed,
                                                                channel,
                                                                client,
                                                                message)
                            else:
                                logger.info(
                                    feed + ':item:skipping item due to not passing filter for ' + channel['name'])

                    else:
                        # Logs of debugging info for date handling stuff...
                        logger.info("%s:too old, skipping", feed)
                        logger.debug("%s:now:now:%s", feed, time.time())
                        logger.debug("%s:now:gmtime:%s", feed, time.gmtime())
                        logger.debug("%s:now:localtime:%s", feed, time.localtime())
                        logger.debug("%s:pubDate:%r", feed, pubdate)
                        logger.debug(item)
                # seen before, move on:
                else:
                    logger.debug(feed + ':item:' + id +
                                 ' seen before, skipping')
        # This is completely expected behavior for a well-behaved feed:
        except HTTPNotModified:
            logger.debug(
                feed + ':Headers indicate feed unchanged since last time fetched:')
            logger.debug(sys.exc_info())
        # Many feeds have random periodic problems that shouldn't cause
        # permanent death:
        except HTTPError:
            logger.warn(feed + ':Unexpected HTTP error:')
            logger.warn(sys.exc_info())
            logger.warn(
                feed + ':Assuming error is transient and trying again later')
        # sqlite3 errors are probably really bad and we should just totally
        # give up on life
        except sqlite3.Error as sqlerr:
            logger.error(feed + ':sqlite3 error: ')
            logger.error(sys.exc_info())
            logger.error(sqlerr)
            raise
        # Ideally we'd remove the specific channel or something...
        # But I guess just throw an error into the log and try again later...
        except discord.errors.Forbidden:
            logger.error(feed + ':discord.errors.Forbidden')
            logger.error(sys.exc_info())
            logger.error(
                feed +
                ":Perhaps bot isn't allowed in one of the channels for this feed?")
            # raise # or not? hmm...
        # unknown error: definitely give up and die and move on
        except Exception:
            logger.exception("Unexpected error - giving up")
            raise
        # No matter what goes wrong, wait same time and try again
        finally:
            logger.debug(feed + ':sleeping for ' +
                         str(rss_refresh_time) + ' seconds')
            yield from asyncio.sleep(rss_refresh_time)


@client.async_event
def on_ready():
    logger.info("Logged in as %r (%r)" % (client.user.name, client.user.id))

    # set current game played
    gameplayed = MAIN.get("gameplayed", "github/freiheit/discord_feedbot")
    if gameplayed:
        yield from client.change_presence(
            game=discord.Game(name=gameplayed), status=discord.Status.idle
        )

    # set avatar if specified
    avatar_file_name = MAIN.get("avatarfile")
    if avatar_file_name:
        with open(avatar_file_name, "rb") as f:
            avatar = f.read()
        yield from client.edit_profile(avatar=avatar)


# Set up the tasks for each feed and start the main event loop thing.
# In this __main__ thing so can be used as library.
def main():
    loop = asyncio.get_event_loop()

    feeds = get_feeds_config(config)
    conn = get_sqlite_connection(config)

    try:
        for feed in feeds:
            loop.create_task(background_check_feed(conn, feed, loop))
        if "login_token" in MAIN:
            loop.run_until_complete(client.login(MAIN.get("login_token")))
        else:
            loop.run_until_complete(
                client.login(MAIN.get("login_email"),
                             MAIN.get("login_password"))
            )
        loop.run_until_complete(client.connect())
    except Exception:
        loop.run_until_complete(client.close())
    finally:
        loop.close()


if __name__ == "__main__":
    main()

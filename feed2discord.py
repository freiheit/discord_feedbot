#!/usr/bin/python3
# Copyright (c) 2016 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

# We do the config stuff very first, so that we can pull debug from
# there
import configparser
import feedparser
import html2text
import logging
import os
import pytz
import re
import sqlite3
import sys
import time
import traceback
import warnings
from aiohttp.web_exceptions import HTTPError, HTTPNotModified
from datetime import datetime
from dateutil.parser import parse as parse_datetime
from urllib.parse import urljoin

if not sys.version_info[:2] >= (3, 4):
    print("Error: requires python 3.4 or newer")
    exit(1)

# Parse the config and stick in global "config" var.
config = configparser.ConfigParser()
for inifile in [
                os.path.expanduser('~')+'/.feed2discord.ini',
                'feed2discord.local.ini',
                'feed2discord.ini']:
    if os.path.isfile(inifile):
       config.read(inifile)
       break # First config file wins

# Make main config area global, since used everywhere/anywhere
MAIN = config['MAIN']

# set global debug verbosity level.
debug = MAIN.getint('debug',0)

# If debug is on, turn on the asyncio debug
if debug:
    os.environ['PYTHONASYNCIODEBUG'] = '1' # needs to be set before
                                           # asyncio is pulled in

# import those last modules that had to be done after the
# os.environ thing above
import aiohttp
import asyncio
import discord

# Tell logging module about my debug level
if debug >= 3:
    logging.basicConfig(level=logging.DEBUG)
elif debug >= 2:
    logging.basicConfig(level=logging.INFO)
else:
    logging.basicConfig(level=logging.WARNING)

# Create global logger object
logger = logging.getLogger(__name__)

# And finish telling logger module about my debug level
if debug >= 1:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)

warnings.resetwarnings()

# Because windows hates you...
# More complicated way to set the timezone stuff, but works on windows
# and unix.
tzstr = MAIN.get('timezone', 'utc')
try:
    timezone = pytz.timezone(tzstr)
except Exception as e:
    timezone = pytz.utc

db_path = MAIN.get('db_path','feed2discord.db')

# Parse out total list of feeds
feeds = config.sections()
# these are non-feed sections:
feeds.remove('MAIN')
feeds.remove('CHANNELS')

# Crazy workaround for a bug with parsing that doesn't apply on all
# pythons:
feedparser.PREFERRED_XML_PARSERS.remove('drv_libxml2')

# set up a single http client for everything to use.
httpclient = aiohttp.ClientSession()

# global database thing
conn = sqlite3.connect(db_path)

# If our two tables don't exist, create them.
conn.execute('''CREATE TABLE IF NOT EXISTS feed_info
              (feed text PRIMARY KEY,
              url text UNIQUE,
              lastmodified text,
              etag text)''')

conn.execute('''CREATE TABLE IF NOT EXISTS feed_items
              (id text PRIMARY KEY,
               published text,
               title text,
               url text,reposted text)''')

# Clean out *some* entries that are over 1 year old...
# Doing this cleanup at start time because some feeds
# do contain very old items and we don't want to keep
# re-evaluating them.
conn.execute('''DELETE FROM feed_items
               where
               (julianday() - julianday(published)) > 366''')


# global discord client object
client = discord.Client()

# This function loops through all the common date fields for an item in
# a feed, and extracts the "best" one.  Falls back to "now" if nothing
# is found.
DATE_FIELDS = ('published','pubDate','date','created','updated')
def extract_best_item_date(item):
    global timezone
    result = {}

    # Look for something vaguely timestamp-ish.
    for date_field in DATE_FIELDS:
        if date_field in item and len(item[date_field]) > 0:
            try:
                date_obj = parse_datetime(item[date_field])

                if date_obj.tzinfo is None:
                    timezone.localize(date_obj)

                #result['date'] = item[date_field]
                #result['date_parsed'] = item[date_field+"_parsed"]

                # Standardize stored time based on the timezone in the
                # ini file
                result['date'] = date_obj.strftime("%a %b %d %H:%M:%S %Z %Y")
                result['date_parsed'] = date_obj

                return result
            except Exception as e:
                pass

    # No potentials found, default to current timezone's "now"
    curtime = timezone.localize(datetime.now())
    result['date'] = curtime.strftime("%a %b %d %H:%M:%S %Z %Y")
    result['date_parsed'] = curtime


    return result


# This looks at the field from the config, and returns the processed string
# naked item in fields: return that field from the feed item
# *, **, _, ~, `, ```: markup the field and return it from the feed item
# " around the field: string literal
# Added new @, turns each comma separated tag into a group mention
def process_field(field,item,FEED,channel):
    logger.debug(feed+':process_field:'+field+': started')

    item_url_base = FEED.get('item_url_base',None)
    if field == 'guid' and item_url_base is not None:
        if 'guid' in item:
            return item_url_base + item['guid']
        else:
            logger.error('process_field:guid:no such field; try show_sample_entry.py on feed')
            return ''

    logger.debug(feed+':process_field:'+field+': checking against regexes')
    stringmatch = re.match('^"(.+?)"$',field)
    highlightmatch = re.match('^([*_~<]+)(.+?)([*_~>]+)$',field)
    bigcodematch = re.match('^```(.+)$',field)
    codematch = re.match('^`(.+)`$',field)

    tagmatch = re.match('^@(.+)$',field) # new tag regex

    if stringmatch is not None:
        # Return an actual string literal from config:
        logger.debug(feed+':process_field:'+field+':isString')
        return stringmatch.group(1) # string from config
    elif highlightmatch is not None:
        logger.debug(feed+':process_field:'+field+':isHighlight')

        # If there's any markdown on the field, return field with that
        # markup on it:
        field = highlightmatch.group(2)
        if field in item:
            if field == 'link':
                return  highlightmatch.group(1) + urljoin(FEED.get('feed_url'),item[field]) + highlightmatch.group(3)
            else:
                return highlightmatch.group(1) + item[field] + highlightmatch.group(3)
        else:
            logger.error('process_field:'+field+':no such field; try show_sample_entry.py on feed')
            return ''
    elif bigcodematch is not None:
        logger.debug(feed+':process_field:'+field+':isCodeBlock')
        # Code blocks are a bit different, with a newline and stuff:
        field = bigcodematch.group(1)
        if field in item:
            return '```\n'+item[field]
        else:
            logger.error('process_field:'+field+':no such field; try show_sample_entry.py on feed')
            return ''
    elif codematch is not None:
        logger.debug(feed+':process_field:'+field+':isCode')

        # Since code chunk can't have other highlights, also do them
        # separately:
        field = codematch.group(1)
        if field in item:
            return '`'+item[field]+'`'
        else:
            logger.error('process_field:'+field+':no such field; try show_sample_entry.py on feed')
            return ''
    elif tagmatch is not None:
        logger.debug(feed+':process_field:'+field+':isTag')
        field = tagmatch.group(1)
        if field in item:
            taglist = item[field].split(', ') # Assuming tags are ', ' separated
            # Iterate through channel roles, see if a role is mentionable and then substitute the role for its id
            for role in client.get_channel(channel['id']).server.roles:
                taglist = ['<@&'+role.id+'>' if str(role.name) == str(i) else i for i in taglist]
            return ', '.join(taglist)
        else:
            logger.error('process_field:'+field+':no such field; try show_sample_entry.py on feed')
            return ''
    else:
        logger.debug(feed+':process_field:'+field+':isPlain')
        # Just asking for plain field:
        if field in item:
            # If field is special field "link",
            # then use urljoin to turn relative URLs into absolute URLs
            if field == 'link':
                return urljoin(FEED.get('feed_url'),item[field])
            # Else assume it's a "summary" or "content" or whatever field
            # and turn HTML into markdown and don't add any markup:
            else:
                htmlfixer = html2text.HTML2Text()
                logger.debug(htmlfixer)
                htmlfixer.ignore_links = True
                htmlfixer.ignore_images = True
                htmlfixer.ignore_emphasis = False
                htmlfixer.body_width = 1000
                htmlfixer.unicode_snob = True
                htmlfixer.ul_item_mark = '-' # Default of "*" likely
                                             # to bold things, etc...
                markdownfield = htmlfixer.handle(item[field])

                # Try to strip any remaining HTML out.  Not "safe", but
                # simple and should catch most stuff:
                markdownfield = re.sub('<[^<]+?>', '', markdownfield)
                return markdownfield
        else:
            logger.error('process_field:'+field+':no such field; try show_sample_entry.py on feed')
            return ''

# This builds a message.

# Pulls the fields (trying for channel_name.fields in FEED, then fields in
# FEED, then fields in DEFAULT, then "id,description".
# fields in config is comma separate string, so pull into array.
# then just adds things, separated by newlines.
# truncates if too long.

def build_message(FEED,item,channel):
    message=''
    fieldlist = FEED.get(
                         channel['name']+'.fields',
                         FEED.get('fields','id,description')
                        ).split(',')
    # Extract fields in order
    for field in fieldlist:
        logger.debug(feed+':item:build_message:'+field+':added to message')
        message+=process_field(field,item,FEED,channel)+"\n"

    # Naked spaces are terrible:
    message = re.sub(' +\n','\n',message)
    message = re.sub('\n +','\n',message)

    # squash newlines down to single ones, and do that last...
    message = re.sub("(\n)+","\n",message)

    if len(message) > 1800:
        message = message[:1800] + "\n... post truncated ..."
    return message

# This schedules an 'actually_send_message' coroutine to run
@asyncio.coroutine
def send_message_wrapper(asyncioloop,FEED,feed,channel,client,message):
    delay = FEED.getint(channel['name']+'.delay',FEED.getint('delay',0))
    logger.debug(feed+':'+channel['name']+
                 ':scheduling message with delay of '+str(delay))
    asyncioloop.create_task(
        actually_send_message(channel,message,delay,FEED,feed))
    logger.debug(feed+':'+channel['name']+':message scheduled')

# Simply sleeps for delay and then sends message.
@asyncio.coroutine
def actually_send_message(channel,message,delay,FEED,feed):
    logger.debug(feed+':'+channel['name']+':'+'sleeping for '+
                 str(delay)+' seconds before sending message')
    if FEED.getint(feed+'.send_typing',FEED.getint('send_typing',0)) >= 1:
        yield from client.send_typing(channel['object'])
    yield from asyncio.sleep(delay)

    logger.debug(feed+':'+channel['name']+':actually sending message')
    if FEED.getint(feed+'.send_typing',FEED.getint('send_typing',0)) >= 1:
        yield from client.send_typing(channel['object'])
    yield from client.send_message(channel['object'],message)
    logger.debug(feed+':'+channel['name']+':message sent')
    logger.debug(message)

# The main work loop
# One of these is run for each feed.
# It's an asyncio thing. "yield from" (sleep or I/O) returns to main loop
# and gives other feeds a chance to run.
@asyncio.coroutine
def background_check_feed(feed,asyncioloop):
    global timezone
    logger.info(feed+': Starting up background_check_feed')

    # Try to wait until Discord client has connected, etc:
    yield from client.wait_until_ready()
    # make sure debug output has this check run in the right order...
    yield from asyncio.sleep(1)

    # just a bit easier to use...
    FEED=config[feed]

    # pull config for this feed out:
    feed_url = FEED.get('feed_url')
    rss_refresh_time = FEED.getint('rss_refresh_time',3600)
    max_age = FEED.getint('max_age',86400)

    # loop through all the channels this feed is configured to send to
    channels = []
    for key in FEED.get('channels').split(','):
        logger.debug(feed+': adding channel '+key)
        # stick a dict in the channels array so we have more to work with
        channels.append(
            {
              'object': discord.Object(id=config['CHANNELS'][key]),
              'name': key,
              'id': config['CHANNELS'][key],
            }
        )

    # Basically run forever
    while not client.is_closed:
        # And tries to catch all the exceptions and just keep going
        # (but see list of except/finally stuff below)
        try:
            logger.info(feed+': processing feed')

            # If send_typing is on for the feed, send a little "typing ..."
            # whenever a feed is being worked on.  configurable per-room
            if FEED.getint(
                           feed+'.send_typing',
                           FEED.getint('send_typing',0)) >= 1:
                for channel in channels:
                    # Since this is first attempt to talk to this channel,
                    # be very verbose about failures to talk to channel
                    try:
                        yield from client.send_typing(channel['object'])
                    except discord.errors.Forbidden:
                        logger.error(feed+':discord.errors.Forbidden')
                        logger.error(sys.exc_info())
                        logger.error(
                            feed+
                            ":Perhaps bot isn't allowed in this channel?")
                        logger.error(channel)

            http_headers = {}
            http_headers['User-Agent'] = MAIN.get('UserAgent',
                                                  'feed2discord/1.0')

            ### Download the actual feed, if changed since last fetch

            # pull data about history of this *feed* from DB:
            cursor = conn.cursor()
            cursor.execute(
                "select lastmodified,etag from feed_info where feed=? OR url=?",
                [feed,feed_url])
            data=cursor.fetchone()

            # If we've handled this feed before,
            # and we have etag from last run, add etag to headers.
            # and if we have a last modified time from last run,
            # add "If-Modified-Since" to headers.
            if data is None: # never handled this feed before...
                logger.info(feed+':looks like updated version. saving info')
                cursor.execute(
                    "REPLACE INTO feed_info (feed,url) VALUES (?,?)",
                    [feed,feed_url])
                conn.commit()
                logger.debug(feed+':feed info saved')
            else:
                logger.debug(feed+
                             ':setting up extra headers for HTTP request.')
                logger.debug(data)
                lastmodified = data[0]
                etag = data[1]
                if lastmodified is not None and len(lastmodified):
                    logger.debug(feed+
                                 ':adding header If-Modified-Since: '+
                                 lastmodified)
                    http_headers['If-Modified-Since'] = lastmodified
                else:
                    logger.debug(feed+':no stored lastmodified')
                if etag is not None and len(etag):
                    logger.debug(feed+':adding header ETag: '+etag)
                    http_headers['ETag'] = etag
                else:
                    logger.debug(feed+':no stored ETag')

            logger.debug(feed+':sending http request for '+feed_url)
            # Send actual request.  yield from can yield control to another
            # instance.
            http_response = yield from httpclient.request('GET',
                                                          feed_url,
                                                          headers=http_headers)
            logger.debug(http_response)

            # Some feeds are smart enough to use that if-modified-since or
            # etag info, which gives us a 304 status.  If that happens,
            # assume no new items, fall through rest of this and try again
            # later.
            if http_response.status == 304:
                logger.debug(feed+':data is old; moving on')
                http_response.close()
                raise HTTPNotModified()
            elif http_response.status is None:
                logger.error(feed+':HTTP response code is NONE')
                raise HTTPError()
            # If we get anything but a 200, that's a problem and we don't
            # have good data, so give up and try later.
            # Mostly handled different than 304/not-modified to make logging
            # clearer.
            elif http_response.status != 200:
                logger.debug(feed+':HTTP error: '+str(http_response.status))
                raise HTTPError()
            else:
                logger.debug(feed+':HTTP success')


            # pull data out of the http response
            logger.debug(feed+':reading http response')
            http_data = yield from http_response.read()

            # parse the data from the http response with feedparser
            logger.debug(feed+':parsing http data')
            feed_data = feedparser.parse(http_data)
            logger.debug(feed+':done fetching')


            # If we got an ETAG back in headers, store that, so we can
            # include on next fetch
            if 'ETAG' in http_response.headers:
                etag = http_response.headers['ETAG']
                logger.debug(feed+':saving etag: '+etag)
                cursor.execute(
                    "UPDATE feed_info SET etag=? where feed=? or url=?",
                    [etag,feed,feed_url])
                conn.commit()
                logger.debug(feed+':etag saved')
            else:
                logger.debug(feed+':no etag')

            # If we got a Last-Modified header back, store that, so we can
            # include on next fetch
            if 'LAST-MODIFIED' in http_response.headers:
                modified = http_response.headers['LAST-MODIFIED']
                logger.debug(feed+':saving lastmodified: '+modified)
                cursor.execute(
                    "UPDATE feed_info SET lastmodified=? where feed=? or url=?",
                    [modified,feed,feed_url])
                conn.commit()
                logger.debug(feed+':saved lastmodified')
            else:
                logger.debug(feed+':no last modified date')

            http_response.close()

            # Process all of the entries in the feed
            # Use reversed to start with end, which is usually oldest
            logger.debug(feed+':processing entries')
            for item in reversed(feed_data.entries):
                logger.debug(feed+':item:processing this entry')
                if debug > 1:
                    logger.debug(item) # can be very noisy

                # Pull out the unique id, or just give up on this item.
                id = ''
                if 'id' in item:
                    id=item.id
                elif 'guid' in item:
                    id=item.guid
                elif 'link' in item:
                    id=item.link
                else:
                    logger.error(feed+':item:no id, skipping')
                    continue

                # Get our best date out, in both raw and parsed form
                pubDateDict = extract_best_item_date(item)
                pubDate = pubDateDict['date']
                pubDate_parsed = pubDateDict['date_parsed']

                logger.debug(feed+':item:id:'+id)
                logger.debug(feed+
                             ':item:checking database history for this item')
                # Check DB for this item
                cursor.execute(
                    "SELECT published,title,url,reposted FROM feed_items WHERE id=?",
                    [id])
                data=cursor.fetchone()

                # If we've never seen it before, then actually processing
                # this:
                if data is None:
                    logger.info(feed+':item '+id+' unseen, processing:')

                    # Store info about this item, so next time we skip it:
                    cursor.execute(
                        "INSERT INTO feed_items (id,published) VALUES (?,?)",
                        [id,pubDate])
                    conn.commit()

                    # Doing some crazy date math stuff...
                    # max_age is mostly so that first run doesn't spew too
                    # much stuff into a room, but is also a useful safety
                    # measure in case a feed suddenly reverts to something
                    # ancient or other weird problems...
                    time_since_published = timezone.localize(datetime.now()) - pubDate_parsed.astimezone(timezone)

                    if time_since_published.total_seconds() < max_age:
                        logger.info(feed+':item:fresh and ready for parsing')

                        # Loop over all channels for this particular feed
                        # and process appropriately:
                        for channel in channels:
                            include = True
                            filter_field = FEED.get(
                                                    channel['name']+'.filter_field',
                                                    FEED.get('filter_field',
                                                        'title'))
                            # Regex if channel exists
                            if (channel['name']+'.filter') in FEED or 'filter' in FEED:
                                logger.debug(feed+':item:running filter for'+channel['name'])
                                regexpat = FEED.get(
                                                    channel['name']+'.filter',
                                                    FEED.get('filter','^.*$'))
                                logger.debug(feed+':item:using filter:'+regexpat+' on '+item['title']+' field '+filter_field)
                                regexmatch = re.search(regexpat,item[filter_field])
                                if regexmatch is None:
                                    include = False
                                    logger.info(feed+':item:failed filter for '+channel['name'])
                            elif (channel['name']+'.filter_exclude') in FEED or 'filter_exclude' in FEED:
                                logger.debug(feed+':item:running exclude filter for'+channel['name'])
                                regexpat = FEED.get(
                                                    channel['name']+'.filter_exclude',
                                                    FEED.get('filter_exclude',
                                                    '^.*$'))
                                logger.debug(feed+':item:using filter_exclude:'+regexpat+' on '+item['title']+' field '+filter_field)
                                regexmatch = re.search(regexpat,item[filter_field])
                                if regexmatch is None:
                                    include = True
                                    logger.info(feed+':item:passed exclude filter for '+channel['name'])
                                else:
                                    include = False
                                    logger.info(feed+':item:failed exclude filter for '+channel['name'])
                            else:
                                include = True # redundant safety net
                                logger.debug(feed+':item:no filter configured for'+channel['name'])

                            if include is True:
                                logger.debug(feed+':item:building message for '+channel['name'])
                                message = build_message(FEED,item,channel)
                                logger.debug(feed+':item:sending message (eventually) to '+channel['name'])
                                yield from send_message_wrapper(asyncioloop,
                                                                FEED,
                                                                feed,
                                                                channel,
                                                                client,
                                                                message)
                            else:
                                logger.info(feed+':item:skipping item due to not passing filter for '+channel['name'])

                    else:
                        # Logs of debugging info for date handling stuff...
                        logger.info(feed+':too old; skipping')
                        logger.debug(feed+':now:'+str(time.time()))
                        logger.debug(feed+':now:gmtime:'+str(time.gmtime()))

                        logger.debug(feed+':now:localtime:'+str(time.localtime()))
                        logger.debug(feed+':timezone.localize(datetime.now()):'+str(timezone.localize(datetime.now())))
                        logger.debug(feed+':pubDate:'+str(pubDate))
                        logger.debug(feed+':pubDate_parsed:'+str(pubDate_parsed))
                        logger.debug(feed+':pubDate_parsed.astimezome(timezone):'+str(pubDate_parsed.astimezone(timezone)))
                        if debug >= 4:
                            logger.debug(item)
                # seen before, move on:
                else:
                    logger.debug(feed+':item:'+id+' seen before, skipping')
        # This is completely expected behavior for a well-behaved feed:
        except HTTPNotModified:
            logger.debug(feed+':Headers indicate feed unchanged since last time fetched:')
            logger.debug(sys.exc_info())
        # Many feeds have random periodic problems that shouldn't cause
        # permanent death:
        except HTTPError:
            logger.warn(feed+':Unexpected HTTP error:')
            logger.warn(sys.exc_info())
            logger.warn(feed+':Assuming error is transient and trying again later')
        # sqlite3 errors are probably really bad and we should just totally
        # give up on life
        except sqlite3.Error as sqlerr:
            logger.error(feed+':sqlite3 error: ')
            logger.error(sys.exc_info())
            logger.error(sqlerr)
            raise
        # Ideally we'd remove the specific channel or something...
        # But I guess just throw an error into the log and try again later...
        except discord.errors.Forbidden:
            logger.error(feed+':discord.errors.Forbidden')
            logger.error(sys.exc_info())
            logger.error(feed+":Perhaps bot isn't allowed in one of the channels for this feed?")
            # raise # or not? hmm...
        # unknown error: definitely give up and die and move on
        except:
            logger.error(feed+':Unexpected error:')
            # logger.error(sys.exc_info())
            logger.error(traceback.format_exc())
            logger.error(feed+':giving up')
            raise
        # No matter what goes wrong, wait same time and try again
        finally:
            logger.debug(feed+':sleeping for '+str(rss_refresh_time)+' seconds')
            yield from asyncio.sleep(rss_refresh_time)

# When client is "ready", set gameplayed, set avatar, and log startup...
@client.async_event
def on_ready():
    logger.info("Logged in as %r (%r)" % (client.user.name, client.user.id))
    gameplayed = MAIN.get("gameplayed", "github/freiheit/discord_rss_bot")
    yield from client.change_presence(game=discord.Game(name=gameplayed),status=discord.Status.idle)
    avatar_file_name = MAIN.get("avatarfile")
    if avatar_file_name:
        with open(avatar_file_name, "rb") as f:
            avatar = f.read()
        yield from client.edit_profile(avatar=avatar)


# Set up the tasks for each feed and start the main event loop thing.
# In this __main__ thing so can be used as library.
def main():
    loop = asyncio.get_event_loop()

    try:
        for feed in feeds:
            loop.create_task(background_check_feed(feed, loop))
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
    loop = asyncio.get_event_loop()

    try:

        ### !!!  If you use in a library, you really need to do this for
        ### !!! loop
        for feed in feeds:
            loop.create_task(background_check_feed(feed,loop))
        if 'login_token' in MAIN:
            loop.run_until_complete(client.login(MAIN.get('login_token')))
        else:
            loop.run_until_complete(client.login(MAIN.get('login_email'),
                                                 MAIN.get('login_password')))
        loop.run_until_complete(client.connect())
    except Exception:
        loop.run_until_complete(client.close())
    finally:
        loop.close()

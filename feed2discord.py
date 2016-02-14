#!/usr/bin/python3

# We do the config stuff very first, so that we can pull debug from there
import configparser
import os, sys

config = configparser.ConfigParser()
for inifile in [os.path.expanduser('~')+'/.feed2discord.ini','feed2discord.local.ini','feed2discord.ini']:
  if os.path.isfile(inifile):
    config.read(inifile)
    break # First config file wins

MAIN = config['MAIN']

debug = MAIN.getint('debug',0)

if debug:
    os.environ['PYTHONASYNCIODEBUG'] = '1' # needs to be set before asyncio is pulled in

import discord, asyncio
import feedparser, aiohttp
import sqlite3
import re
import html2text
import time
import logging, warnings
from aiohttp.web_exceptions import HTTPError, HTTPNotModified

if debug >= 3:
    logging.basicConfig(level=logging.DEBUG)
elif debug >= 2:
    logging.basicConfig(level=logging.INFO)
else:
    logging.basicConfig(level=logging.WARNING)

logger = logging.getLogger(__name__)

if debug >= 1:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)

warnings.resetwarnings()

db_path = MAIN.get('db_path','feed2discord.db')

feeds = config.sections()
feeds.remove('MAIN')
feeds.remove('CHANNELS')

# Crazy workaround for a bug with parsing that doesn't apply on all pythons:
feedparser.PREFERRED_XML_PARSERS.remove('drv_libxml2')

conn = sqlite3.connect(db_path)

conn.execute('''CREATE TABLE IF NOT EXISTS feed_items
              (id text PRIMARY KEY,published text,title text,url text,reposted text)''')

conn.execute('''CREATE TABLE IF NOT EXISTS feed_info
              (feed text PRIMARY KEY,url text UNIQUE,lastmodified text,etag text)''')

client = discord.Client()

DATE_FIELDS = ('published','pubDate','date','created','updated')
def extract_best_item_date(item):
    result = {}
    for date_field in DATE_FIELDS:
        if date_field in item:
            result['date'] = item[date_field]
            result['date_parsed'] = item[date_field+'_parsed']
            break
    else:
        result['date'] = time.asctime(time.gmtime())
        result['date_parsed'] = time.gmtime()

    return result
        

def process_field(field,item,FEED):
    logger.debug(feed+':process_field:'+field+': started')

    item_url_base = FEED.get('item_url_base',None)
    if field == 'guid' and item_url_base is not None:
        return item_url_base + guid

    logger.debug(feed+':process_field:'+field+': checking against regexes')
    stringmatch = re.match('^"(.+?)"$',field)
    highlightmatch = re.match('^([*_~]+)(.+?)([*_~]+)$',field)
    bigcodematch = re.match('^```(.+)$',field)
    codematch = re.match('^`(.+)`$',field)
    if stringmatch is not None:
        # Return an actual string literal from config:
        logger.debug(feed+':process_field:'+field+':isString')
        return stringmatch.group(1) # string from config
    elif highlightmatch is not None:
        logger.debug(feed+':process_field:'+field+':isHighlight')
        # If there's any markdown on the field, return field with that markup on it:
        field = highlightmatch.group(2)
        if field in item:
            return highlightmatch.group(1) + item[field] + highlightmatch.group(3)
        else:
            logger.error('process_field:'+field+':no such field')
            return ''
    elif bigcodematch is not None:
        logger.debug(feed+':process_field:'+field+':isCodeBlock')
        # Code blocks are a bit different, with a newline and stuff:
        field = bigcodematch.group(1)
        if field in item:
            return '```\n'+item[field]
        else:
            logger.error('process_field:'+field+':no such field')
            return ''
    elif codematch is not None:
        logger.debug(feed+':process_field:'+field+':isCode')
        # Since code chunk can't have other highlights, also do them separately:
        field = codematch.group(1)
        if field in item:
            return '`'+item[field]+'`'
        else:
            logger.error('process_field:'+field+':no such field')
            return ''
    else:
        logger.debug(feed+':process_field:'+field+':isPlain')
        # Otherwise, just return the plain field:
        if field in item:
            htmlfixer = html2text.HTML2Text()
            logger.debug(htmlfixer)
            htmlfixer.ignore_links = True
            htmlfixer.ignore_images = True

            htmlfixer = html2text.HTML2Text()
            htmlfixer.ignore_links = True
            htmlfixer.ignore_images = True
            htmlfixer.ignore_emphasis = False
            htmlfixer.body_width = 1000
            htmlfixer.unicode_snob = True
            htmlfixer.ul_item_mark = '-' # Default of "*" likely to bold things, etc...
            return htmlfixer.handle(item[field])
        else:
            return ''

def build_message(FEED,item):
    message=''
    # Extract fields in order
    for field in FEED.get('fields','id,description').split(','):
        logger.debug(feed+':item:build_message:'+field+':added to message')
        message+=process_field(field,item,FEED)+"\n"

    # Try to strip any remaining HTML out. Not "safe", but simple and should catch most stuff:
    message = re.sub('<[^<]+?>', '', message)

    # Naked spaces are terrible:
    message = re.sub(' +\n','\n',message)
    message = re.sub('\n +','\n',message)

    # squash newlines down to single ones, and do that last... 
    message = re.sub("(\n)+","\n",message)

    if len(message) > 1800:
        message = message[:1800] + "\n... post truncated ..."
    return message


@asyncio.coroutine
def background_check_feed(feed):
    logger.info(feed+': Starting up background_check_feed')
    yield from client.wait_until_ready()
    # make sure debug output has this check run in the right order...
    yield from asyncio.sleep(1)

    FEED=config[feed]

    feed_url = FEED.get('feed_url')
    rss_refresh_time = FEED.getint('rss_refresh_time',3600)
    max_age = FEED.getint('max_age',86400)
    channels = []
    for key in FEED.get('channels').split(','):
        logger.debug(feed+': adding channel '+key)
        channels.append(discord.Object(id=config['CHANNELS'][key]))

    while not client.is_closed:
        try:
            logger.info(feed+': processing feed')
            for channel in channels:
                yield from client.send_typing(channel)

            http_headers = {}
            http_headers['User-Agent'] = MAIN.get('UserAgent','feed2discord/1.0')

            ### Download the actual feed, if changed since last fetch
            cursor = conn.cursor()
            cursor.execute("select lastmodified,etag from feed_info where feed=? OR url=?",[feed,feed_url])
            data=cursor.fetchone()
            if data is None:
                logger.info(feed+':looks like updated version. saving info')
                cursor.execute("REPLACE INTO feed_info (feed,url) VALUES (?,?)",[feed,feed_url])
                conn.commit()
                logger.debug(feed+':feed info saved')
            else:
                logger.debug(feed+':setting up extra headers for HTTP request.')
                logger.debug(data)
                lastmodified = data[0]
                etag = data[1]
                if lastmodified is not None and len(lastmodified):
                    logger.debug(feed+':adding header If-Modified-Since: '+lastmodified)
                    http_headers['If-Modified-Since'] = lastmodified
                else:
                    logger.debug(feed+':no stored lastmodified')
                if etag is not None and len(etag):
                    logger.debug(feed+':adding header ETag: '+etag)
                    http_headers['ETag'] = etag
                else:
                    logger.debug(feed+':no stored ETag')
            logger.debug(feed+':sending http request for '+feed_url)
            http_response = yield from aiohttp.request('GET', feed_url, headers=http_headers)
            logger.debug(http_response)
            if http_response.status == 304:
                logger.debug(feed+':data is old; moving on')
                raise HTTPNotModified()
            elif http_response.status != 200:
                logger.debug(feed+':HTTP error: '+http_response.status)
                raise HTTPError()
            else:
                logger.debug(feed+':HTTP success')


            logger.debug(feed+':reading http response')
            http_data = yield from http_response.read()

            logger.debug(feed+':parsing http data')
            feed_data = feedparser.parse(http_data)
            logger.debug(feed+':done fetching')            


            if 'ETAG' in http_response.headers:
                etag = http_response.headers['ETAG']
                logger.debug(feed+':saving etag: '+etag)
                cursor.execute("UPDATE feed_info SET etag=? where feed=? or url=?",[etag,feed,feed_url])
                conn.commit()
                logger.debug(feed+':etag saved')
            else:
                logger.debug(feed+':no etag')

            if 'LAST-MODIFIED' in http_response.headers:
                modified = http_response.headers['LAST-MODIFIED']
                logger.debug(feed+':saving lastmodified: '+modified)
                cursor.execute("UPDATE feed_info SET lastmodified=? where feed=? or url=?",[modified,feed,feed_url])
                conn.commit()
                logger.debug(feed+':saved lastmodified')
            else:
                logger.debug(feed+':no last modified date')

            logger.debug(feed+':processing entries')
            for item in feed_data.entries:
                logger.debug(feed+':item:processing this entry')
                # logger.debug(item) # can be very noisy
                id = ''
                if 'id' in item:
                    id=item.id
                elif 'guid' in item:
                    id=item.guid
                else:
                    logger.error(feed+':item:no id, skipping')
                    continue
                pubDateDict = extract_best_item_date(item)
                pubDate = pubDateDict['date']
                pubDate_parsed = pubDateDict['date_parsed']
                logger.debug(feed+':item:checking database history for this item')
                cursor.execute("SELECT published,title,url,reposted FROM feed_items WHERE id=?",[id])
                data=cursor.fetchone()
                if data is None:
                    logger.info(feed+':item '+id+' unseen, processing:')
                    cursor.execute("INSERT INTO feed_items (id,published) VALUES (?,?)",[id,pubDate])
                    conn.commit()
                    if time.mktime(pubDate_parsed) > (time.time() - max_age):
                        logger.info(feed+':item:fresh and ready for parsing')
                        logger.debug(feed+':item:building message')
                        message = build_message(FEED,item)
                        for channel in channels:
                            logger.debug(feed+':item:sending message')
                            yield from client.send_message(channel,message)
                    else:
                        logger.info(feed+':too old; skipping')
                else:
                    logger.debug(feed+':item:'+id+' seen before, skipping')
        except HTTPNotModified:
            logger.debug(feed+':Headers indicate feed unchanged since last time fetched: '+sys.exc_info()[0])
        except HTTPError:
            logger.debug(feed+':Unexpected HTTP error: '+sys.exc_info()[0])
            logger.debug(feed+':Assuming error is transient and trying again later')
        except sqlite3.Error as sqlerr:
            logger.debug(feed+':sqlite3 error: ')
            logger.debug(sqlerr)
            raise
        except:
            logger.debug(feed+':Unexpected error: '+sys.exc_info()[0])
            logger.debug(feed+':giving up')
            raise
        finally:
            # No matter what goes wrong, wait same time and try again
            logger.debug(feed+':sleeping for '+str(rss_refresh_time)+' seconds')
            yield from asyncio.sleep(rss_refresh_time)
        
@client.async_event
def on_ready():
    logger.info('Logged in as '+client.user.name+'('+client.user.id+')')
    gameplayed=MAIN.get('gameplayed','github/freiheit/discord_rss_bot')
    yield from client.change_status(game=discord.Game(name=gameplayed))

if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    try:
        for feed in feeds:
            loop.create_task(background_check_feed(feed))
        loop.run_until_complete(client.login(MAIN.get('login_email'), MAIN.get('login_password')))
        loop.run_until_complete(client.connect())
    except Exception:
        loop.run_until_complete(client.close())
    finally:
        loop.close()

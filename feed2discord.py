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

@asyncio.coroutine
def background_check_feed(feed):
    logger.info('Starting up background_check_feed for '+feed)
    yield from client.wait_until_ready()
    # make sure debug output has this check run in the right order...
    yield from asyncio.sleep(1)

    FEED=config[feed]

    feed_url = FEED.get('feed_url')
    item_url_base = FEED.get('item_url_base',None)
    rss_refresh_time = FEED.getint('rss_refresh_time',3600)
    max_age = FEED.getint('max_age',86400)
    channels = []
    for key in FEED.get('channels').split(','):
        logger.debug('adding channel '+key+' to '+feed)
        channels.append(discord.Object(id=config['CHANNELS'][key]))

    while not client.is_closed:
        try:
            logger.info('processing feed:'+feed)

            http_headers = {}

            cursor = conn.cursor()
            cursor.execute("select lastmodified,etag from feed_info where feed=? OR url=?",[feed,feed_url])
            data=cursor.fetchone()
            if data is None:
                logger.info('feed is new to us? saving info')
                cursor.execute("REPLACE INTO feed_info (feed,url) VALUES (?,?)",[feed,feed_url])
                conn.commit()
                logger.debug('feed info saved')
            else:
                logger.debug('setting up extra headers for HTTP request.')
                logger.debug(data)
                lastmodified = data[0]
                etag = data[1]
                if lastmodified is not None and len(lastmodified):
                    logger.debug('adding header If-Modified-Since: '+lastmodified)
                    http_headers['If-Modified-Since'] = lastmodified
                else:
                    logger.debug('no stored lastmodified')
                if etag is not None and len(etag):
                    logger.debug('adding header ETag: '+etag)
                    http_headers['ETag'] = etag
                else:
                    logger.debug('no stored ETag')

            logger.debug('sending http request for '+feed_url)
            http_response = yield from aiohttp.request('GET', feed_url, headers=http_headers)
            logger.debug(http_response)
            logger.debug(http_response.headers)
            if http_response.status == 304:
                logger.debug('data is old; moving on')
                raise HTTPNotModified()
            elif http_response.status != 200:
                logger.debug('HTTP error: '+http_response.status)
                raise HTTPError()
            else:
                logger.debug('HTTP success')

            logger.debug('reading http response')
            http_data = yield from http_response.read()

            logger.debug('parsing http data')
            feed_data = feedparser.parse(http_data)
            logger.debug('done fetching')            


            if 'ETAG' in http_response.headers:
                etag = http_response.headers['ETAG']
                logger.debug('saving etag: '+etag)
                cursor.execute("UPDATE feed_info SET etag=? where feed=? or url=?",[etag,feed,feed_url])
                conn.commit()
                logger.debug('etag saved')
            else:
                logger.debug('no etag')

            if 'LAST-MODIFIED' in http_response.headers:
                modified = http_response.headers['LAST-MODIFIED']
                logger.debug('saving lastmodified: '+modified)
                cursor.execute("UPDATE feed_info SET lastmodified=? where feed=? or url=?",[modified,feed,feed_url])
                conn.commit()
                logger.debug('saved lastmodified')
            else:
                logger.debug('no last modified date')

            logger.debug('processing entries')
            for item in feed_data.entries:
                id=item.id
                pubDate=item.published
                title=item.title
                original_description=item.description
                url = item_url_base + id

                cursor.execute("SELECT published,title,url,reposted FROM feed_items WHERE id=? or url=?",[id,url])

                data=cursor.fetchone()
                if data is None:
                    logger.info('item '+id+' unseen, processing:')
                    cursor.execute("INSERT INTO feed_items (id,published,title,url) VALUES (?,?,?,?)",[id,pubDate,title,url])
                    conn.commit()
                    if time.mktime(item.published_parsed) > (time.time() - max_age):
                        logger.info(' fresh and ready for parsing')
                        description = re.sub('<br */>',"\n",original_description)
                        description = re.sub("\n+","\n",description)
                        if len(description) > 1800:
                          description = description[:1000] + "\n..."
                        logger.debug(' published: '+pubDate)
                        logger.debug(' title: '+title)
                        logger.debug(' url: '+url)
                        for channel in channels:
                            logger.debug('sending message to '+channel.name()+' on '+channel.server())
                            yield from client.send_message(channel,
                               url+"\n"+
                               "**"+title+"**\n"+
                               "*"+pubDate+"*\n"+
                               description)
                    else:
                        logger.info(' too old; skipping')
                else:
                    logger.debug('item '+id+' seen before, skipping')
        except HTTPNotModified:
            logger.debug('Headers indicate feed unchanged since last time fetched: '+sys.exc_info()[0])
        except HTTPError:
            logger.debug('Unexpected HTTP error: '+sys.exc_info()[0])
            logger.debug('Assuming error is transient and trying again later')
        except sqlite3.Error as sqlerr:
            logger.debug('sqlite3 error: ')
            logger.debug(sqlerr)
            raise
        except:
            logger.debug('Unexpected error: '+sys.exc_info()[0])
            logger.debug(feed,' is giving up')
            raise
        finally:
            # No matter what goes wrong, wait same time and try again
            logger.debug('sleeping '+feed+' for '+str(rss_refresh_time)+' seconds')
            yield from asyncio.sleep(rss_refresh_time)
        
@client.async_event
def on_ready():
    logger.info('Logged in as '+client.user.name+'('+client.user.id+')')

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

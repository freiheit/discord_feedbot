#!/usr/bin/python3

# We do the config stuff very first, so that we can pull debug from there
import configparser

import os

config = configparser.ConfigParser()
for inifile in [os.path.expanduser('~')+'/.feed2discord.ini','feed2discord.local.ini','feed2discord.ini']:
  if os.path.isfile(inifile):
    config.read(inifile)
    break # First config file wins

MAIN = config['MAIN']

debug = MAIN.getint('debug',0)

if debug:
    os.environ['PYTHONASYNCIODEBUG'] = '1' # needs to be set before asyncio is pulled in

import feedparser
import discord
import asyncio
import aiohttp
import sqlite3
import re
import time
import logging
import warnings

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

client = discord.Client()

@asyncio.coroutine
def background_check_feed(feed):
    logger.info('Starting up background_check_feed for '+feed)
    yield from client.wait_until_ready()
    # make sure debug output has this check run in the right order...
    yield from asyncio.sleep(1)

    feed_url = config[feed]['feed_url']
    item_url_base = config[feed]['item_url_base']
    rss_refresh_time = config.getint(feed,'rss_refresh_time')
    max_age = config.getint(feed,'max_age')
    channels = []
    for key in config[feed]['channels'].split(','):
        logger.debug(' adding '+key+' to '+feed)
        channels.append(discord.Object(id=config['CHANNELS'][key]))

    while not client.is_closed:
        try:
            logger.info('processing feed:'+feed)
            http_response = yield from aiohttp.request('GET', feed_url)
            http_data = yield from http_response.read()
            feed_data = feedparser.parse(http_data)
            logger.debug('done fetching')
            for item in feed_data.entries:
                id=item.id
                pubDate=item.published
                title=item.title
                original_description=item.description
                url = item_url_base + id

                cursor = conn.cursor()
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
        except Exception as exc:
            logger.error('Unexpected error: '+exc)
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

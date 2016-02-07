#!/usr/bin/python3

# We do the config stuff very first, so that we can pull debug from there
import configparser

import os

config = configparser.ConfigParser()
for inifile in [os.path.expanduser('~')+'/.feed2discord.ini','feed2discord.ini']:
  if os.path.isfile(inifile):
    config.read(inifile)
    break # First config file wins

MAIN = config['MAIN']

debug = MAIN.get('debug',0)

if debug:
    os.environ['PYTHONASYNCIODEBUG'] = '1' # needs to be set before asyncio is pulled in

import feedparser
import discord
import asyncio
import sqlite3
import re
import time
import logging
import warnings

if debug:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

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
    logging.info('Starting up background_check_feed for '+feed)
    yield from client.wait_until_ready()

    feed_url = config[feed]['feed_url']
    item_url_base = config[feed]['item_url_base']
    rss_refresh_time = config.getint(feed,'rss_refresh_time')
    max_age = config.getint(feed,'max_age')
    channels = []
    for key in config[feed]['channels'].split(','):
        logging.debug(' adding '+key+' to '+feed)
        channels.append(discord.Object(id=config['CHANNELS'][key]))

    while not client.is_closed:
        logging.info('processing feed:'+feed)
        feed_data = feedparser.parse(feed_url)
        logging.debug('done fetching')
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
                logging.info('item '+id+' unseen, processing:')
                cursor.execute("INSERT INTO feed_items (id,published,title,url) VALUES (?,?,?,?)",[id,pubDate,title,url])
                conn.commit()
                if time.mktime(item.published_parsed) > (time.time() - max_age):
                    logging.info(' fresh and ready for parsing')
                    description = re.sub('<br */>',"\n",original_description)
                    description = re.sub("\n+","\n",description)
                    if len(description) > 1800:
                      description = description[:1000] + "\n..."
                    logging.debug(' published: '+pubDate)
                    logging.debug(' title: '+title)
                    logging.debug(' url: '+url)
                    for channel in channels:
                        logging.debug('sending message to '+channel.str())
                        yield from client.send_message(channel,
                           url+"\n"+
                           "**"+title+"**\n"+
                           "*"+pubDate+"*\n"+
                           description)
                else:
                    logging.info(' too old; skipping')
            else:
                logging.debug('item '+id+' seen before, skipping')
                
        logging.debug('sleeping '+feed+' for '+rss_refresh_time+' seconds')
        yield from asyncio.sleep(rss_refresh_time)
        
@client.async_event
def on_ready():
    logging.info('Logged in as '+client.user.name+'('+client.user.id+')')

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

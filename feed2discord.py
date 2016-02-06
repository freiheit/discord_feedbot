#!/usr/bin/python3

debug = None

import os

if debug:
    os.environ['PYTHONASYNCIODEBUG'] = '1' # needs to be set before asyncio is pulled in

import feedparser
import discord
import asyncio
import sqlite3
import re
import configparser
import logging
import warnings
import time

if debug:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

warnings.resetwarnings()

config = configparser.ConfigParser()
for inifile in [os.path.expanduser('~')+'/.feed2discord.ini','feed2discord.ini']:
  if os.path.isfile(inifile):
    config.read(inifile)
    break # First config file wins

login_email = config['MAIN']['login_email']
login_password = config['MAIN']['login_password']
db_path = config['MAIN']['db_path']

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
    print('Starting up background_check_feed for '+feed)
    yield from client.wait_until_ready()

    feed_url = config[feed]['feed_url']
    item_url_base = config[feed]['item_url_base']
    rss_refresh_time = config.getint(feed,'rss_refresh_time')
    max_age = config.getint(feed,'max_age')
    channels = []
    for key in config[feed]['channels'].split(','):
        print(' adding '+key+' to '+feed)
        channels.append(discord.Object(id=config['CHANNELS'][key]))

    while not client.is_closed:
        print('fetching and parsing '+feed_url)
        feed_data = feedparser.parse(feed_url)
        print('done fetching')
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
                print('item '+id+' unseen, processing:')
                cursor.execute("INSERT INTO feed_items (id,published,title,url) VALUES (?,?,?,?)",[id,pubDate,title,url])
                conn.commit()
                if time.mktime(item.published_parsed) > (time.time() - max_age):
                    print(' fresh and ready for parsing')
                    description = re.sub('<br */>',"\n",original_description)
                    description = re.sub("\n+","\n",description)
                    if len(description) > 1800:
                      description = description[:1000] + "\n..."
                    print(' published: '+pubDate)
                    print(' title: '+title)
                    print(' url: '+url)
                    for channel in channels:
                        yield from client.send_message(channel,
                           url+"\n"+
                           "**"+title+"**\n"+
                           "*"+pubDate+"*\n"+
                           description)
                else:
                    print(' too old; skipping')
            else:
                print('item '+id+' seen before, skipping')
                
        yield from asyncio.sleep(rss_refresh_time)
        
@client.async_event
def on_ready():
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)
    print('------')

if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    try:
        for feed in feeds:
            loop.create_task(background_check_feed(feed))
        loop.run_until_complete(client.login(login_email, login_password))
        loop.run_until_complete(client.connect())
    except Exception:
        loop.run_until_complete(client.close())
    finally:
        loop.close()

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
feed_url = config['MAIN']['feed_url']
item_url_base = config['MAIN']['item_url_base']
db_path = config['MAIN']['db_path']
channel_id = config['MAIN']['channel_id']
rss_refresh_time = config.getint('MAIN','rss_refresh_time')

# Crazy workaround for a bug with parsing that doesn't apply on all pythons:
feedparser.PREFERRED_XML_PARSERS.remove('drv_libxml2')

conn = sqlite3.connect(db_path)

conn.execute('''CREATE TABLE IF NOT EXISTS feed_items
              (id text PRIMARY KEY,published text,title text,url text,reposted text)''')

client = discord.Client()

@asyncio.coroutine
def background_check_feed():
    yield from client.wait_until_ready()
    channel = discord.Object(id=channel_id)
    while not client.is_closed:
        yield from client.send_typing(channel) # indicate we might be working on something
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
                description = re.sub('<br */>',"\n",original_description)
                print(' published: '+pubDate)
                print(' title: '+title)
                print(' url: '+url)
#                yield from client.send_message(channel,
#                   url+"\n"+
#                   "**"+title+"**\n"+
#                   "*"+pubDate+"*\n"+
#                   description)
            else:
                print('item '+id+' seen before, skipping')
                
        yield from asyncio.sleep(rss_refresh_time)
        
@client.async_event
def on_ready():
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)
    print('------')

loop = asyncio.get_event_loop()

try:
    loop.create_task(background_check_feed())
    loop.run_until_complete(client.login(login_email, login_password))
    loop.run_until_complete(client.connect())
except Exception:
    loop.run_until_complete(client.close())
finally:
    loop.close()

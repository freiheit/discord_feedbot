#!/usr/bin/python3

import feedparser
import discord
import asyncio
import sqlite3
import re

login_email = 'blah@example.com'
login_password = 'PASSWORD HERE'
feed_url = 'https://community.elitedangerous.com/galnet-rss'
item_url_base = 'https://community.elitedangerous.com/galnet/uid/'
db_path = 'feed2discord.db'
channel_id = 'MAGIC CHANNEL ID HERE'
rss_refresh_time = 300

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
        feed_data = feedparser.parse(feed_url)
        for item in feed_data.entries:
            id=item.id
            pubDate=item.published
            title=item.title
            original_description=item.description
            url = item_url_base + id

            cursor = conn.cursor()
            cursor.execute("SELECT published,title,url,reposted FROM feed_items WHERE id=?",[id])

            data=cursor.fetchone()
            if data is None:
                description = re.sub('<br */>',"\n",original_description)
                conn.execute("INSERT INTO feed_items (id,published,title,url) VALUES (?,?,?,?)",[id,pubDate,title,url])
                yield from client.send_message(channel,
                   url+"\n"+
                   "**"+title+"**\n"+
                   "*"+pubDate+"*\n"+
                   description)
                
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

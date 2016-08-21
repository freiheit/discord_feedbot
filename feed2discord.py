#!/usr/bin/env python
# Copyright (c) 2016 Eric Eisenhart
# This software is released under an MIT-style license.
# See LICENSE.md for full details.

# We do the config stuff very first, so that we can pull debug from there
import configparser
import logging
import os
import re
import sqlite3
import warnings
import discord
import feedparser
import pytz
from datetime import datetime
from dateutil.parser import parse as parse_datetime
from html2text import HTML2Text

# Parse the config and stick in global "config" var.
config = configparser.ConfigParser()
for inifile in [
	os.path.expanduser("~") + "/.feed2discord.ini",
	"feed2discord.local.ini",
	"feed2discord.ini",
]:
	if os.path.isfile(inifile):
		config.read(inifile)
		break  # First config file wins

# Make main config area global, since used everywhere/anywhere
MAIN = config["MAIN"]

# set global debug verbosity level.
debug = MAIN.getint("debug", 0)

# If debug is on, turn on the asyncio debug
if debug:
	# needs to be set before asyncio is pulled in
	os.environ["PYTHONASYNCIODEBUG"] = "1"

# import the rest of my modules
import aiohttp
import asyncio
from aiohttp.web_exceptions import HTTPError, HTTPNotModified

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
# More complicated way to set the timezone stuff, but works on windows and unix.
tzstr = MAIN.get("timezone", "utc")
try:
	timezone = pytz.timezone(tzstr)
except Exception as e:
	timezone = pytz.utc

db_path = MAIN.get("db_path", "feed2discord.db")

# Parse out total list of feeds
feeds = config.sections()
# these are non-feed sections:
feeds.remove("MAIN")
feeds.remove("CHANNELS")

# Crazy workaround for a bug with parsing that doesn't apply on all pythons:
feedparser.PREFERRED_XML_PARSERS.remove("drv_libxml2")

# set up a single http client for everything to use.
httpclient = aiohttp.ClientSession()

# global database thing
conn = sqlite3.connect(db_path)

# If our two tables don"t exist, create them.
conn.execute("""
CREATE TABLE IF NOT EXISTS feed_items
(id text PRIMARY KEY,published text,title text,url text,reposted text)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS feed_info
(feed text PRIMARY KEY,url text UNIQUE,lastmodified text,etag text)
""")

# global discord client object
client = discord.Client()

# This function loops through all the common date fields for an item in a feed, and
# extracts the "best" one. Falls back to "now" if nothing is found.
DATE_FIELDS = ("published", "pubDate", "date", "created", "updated")


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

				# Standardize stored time based on the timezone in the ini file
				result["date"] = date_obj.strftime("%a %b %d %H:%M:%S %Z %Y")
				result["date_parsed"] = date_obj

				return result
			except Exception as e:
				pass

	# No potentials found, default to current timezone"s "now"
	curtime = timezone.localize(datetime.now())
	result["date"] = curtime.strftime("%a %b %d %H:%M:%S %Z %Y")
	result["date_parsed"] = curtime

	return result


# This looks at the field from the config, and returns the processed string
# naked item in fields: return that field from the feed item
# *, **, _, ~, `, ```: markup the field and return it from the feed item
# " around the field: string literal
def process_field(field, item, FEED):
	logger.debug("process_field:%s: started", field)

	item_url_base = FEED.get("item_url_base")
	if field == "guid" and item_url_base is not None:
		if "guid" in item:
			return item_url_base + item["guid"]
		else:
			logger.error("process_field:guid:no such field")
			return ""

	logger.debug("process_field:%s: checking against regexes", field)
	stringmatch = re.match('^"(.+?)"$', field)
	highlightmatch = re.match("^([*_~]+)(.+?)([*_~]+)$", field)
	bigcodematch = re.match("^```(.+)$", field)
	codematch = re.match("^`(.+)`$", field)
	if stringmatch is not None:
		# Return an actual string literal from config:
		logger.debug("process_field:%s: field is string", field)
		return stringmatch.group(1)  # string from config
	elif highlightmatch is not None:
		logger.debug("process_field:%s: field is highlight", field)
		# If there"s any markdown on the field, return field with that markup on it:
		field = highlightmatch.group(2)
		if field in item:
			return highlightmatch.group(1) + item[field] + highlightmatch.group(3)
		else:
			logger.error("process_field:%s: No such field", field)
			return ""
	elif bigcodematch is not None:
		logger.debug("process_field:%s: field is codeblock", field)
		# Code blocks are a bit different, with a newline and stuff:
		field = bigcodematch.group(1)
		if field in item:
			return "```\n" + item[field]
		else:
			logger.error("process_field:%s: No such field", field)
			return ""
	elif codematch is not None:
		logger.debug("%s:process_field:%s: field is code", field)
		# Since code chunk can"t have other highlights, also do them separately:
		field = codematch.group(1)
		if field in item:
			return "`%s`" % (item[field])
		else:
			logger.error("process_field:%s: No such field", field)
			return ""
	else:
		logger.debug("process_field:%s: field is plain", field)
		# Otherwise, just return the plain field:
		if field in item:
			htmlfixer = HTML2Text()
			logger.debug(htmlfixer)
			htmlfixer.ignore_links = True
			htmlfixer.ignore_images = True
			htmlfixer.ignore_emphasis = False
			htmlfixer.body_width = 1000
			htmlfixer.unicode_snob = True
			htmlfixer.ul_item_mark = "-"  # Default of "*" likely to bold things, etc...
			markdownfield = htmlfixer.handle(item[field])
			# Try to strip any remaining HTML out.
			# Not "safe", but simple and should catch most stuff:
			markdownfield = re.sub("<[^<]+?>", "", markdownfield)
			return markdownfield
	return ""


# This builds a message.
# Pulls the fields (trying for channel_name.fields in FEED, then fields in FEED,
# then fields in DEFAULT, then "id,description".
# fields in config is comma separate string, so pull into array.
# then just adds things, separated by newlines.
# truncates if too long.
def build_message(FEED, item, channel):
	message = ""
	name = channel["name"] + ".fields"
	fieldlist = FEED.get(name, FEED.get("fields", "id, description")).split(",")
	# Extract fields in order
	for field in fieldlist:
		logger.debug("item:build_message:%s: added to message", field)
		message += process_field(field, item, FEED) + "\n"

	# Naked spaces are terrible:
	message = re.sub(" +\n", "\n", message)
	message = re.sub("\n +", "\n", message)

	# squash newlines down to single ones, and do that last...
	message = re.sub("(\n)+", "\n", message)

	if len(message) > 1800:
		message = message[:1800] + "\n... post truncated ..."
	return message


@asyncio.coroutine
def send_message_wrapper(asyncioloop, FEED, feed, channel, client, message):
	name = channel["name"] + ".delay"
	delay = FEED.getint(name, FEED.getint("delay", 0))
	logger.debug("%s:%s: Scheduling message with delay %i", feed, channel["name"], delay)
	yield from client.send_message(channel["object"], message)


# The main work loop
# One of these is run for each feed.
# It"s an asyncio thing. "yield from" (sleep or I/O) returns to main loop
# and gives other feeds a chance to run.
@asyncio.coroutine
def background_check_feed(feed, asyncioloop):
	global timezone
	logger.info("%s: Starting up background_check_feed", feed)

	# Try to wait until Discord client has connected, etc:
	yield from client.wait_until_ready()
	# make sure debug output has this check run in the right order...
	yield from asyncio.sleep(1)

	# just a bit easier to use...
	FEED = config[feed]

	# pull config for this feed out:
	feed_url = FEED.get("feed_url")
	rss_refresh_time = FEED.getint("rss_refresh_time", 3600)
	max_age = FEED.getint("max_age", 86400)

	# loop through all the channels this feed is configured to send to
	channels = []
	for key in FEED.get("channels").split(","):
		logger.debug("%s: adding channel %r", feed, key)
		# stick a dict in the channels array so we have more to work with
		channels.append({
			"object": discord.Object(id=config["CHANNELS"][key]),
			"name": key,
			"id": config["CHANNELS"][key],
		})

	# Basically run forever
	while not client.is_closed:
		# And tries to catch all the exceptions and just keep going
		# (but see list of except/finally stuff below)
		try:
			logger.info("%s: processing feed", feed)

			http_headers = {
				"User-Agent": MAIN.get("UserAgent", "feed2discord/1.0"),
			}

			# Download the actual feed, if changed since last fetch

			# pull data about history of this *feed* from DB:
			cursor = conn.cursor()
			cursor.execute(
				"select lastmodified,etag from feed_info where feed=? OR url=?", [feed, feed_url]
			)
			data = cursor.fetchone()

			# If we"ve handled this feed before,
			# and we have etag from last run, add etag to headers.
			# and if we have a last modified time from last run, add "If-Modified-Since" to headers.
			if data is None:  # never handled this feed before...
				logger.info("%s:Looks like updated version. Saving info.", feed)
				cursor.execute("REPLACE INTO feed_info (feed,url) VALUES (?,?)", [feed, feed_url])
				conn.commit()
				logger.debug("%s:feed info saved", feed)
			else:
				lastmodified = data[0]
				etag = data[1]
				if lastmodified is not None and len(lastmodified):
					logger.debug("%s:adding header If-Modified-Since: %r", feed, lastmodified)
					http_headers["If-Modified-Since"] = lastmodified
				if etag is not None and len(etag):
					logger.debug("%s:adding header ETag: %r", feed, etag)
					http_headers["ETag"] = etag

			logger.debug("%s:sending http request for %r", feed, feed_url)
			# Send actual request. yield from can yield control to another instance.
			http_response = yield from httpclient.request("GET", feed_url, headers=http_headers)
			logger.debug(http_response)

			# Some feeds are smart enough to use that if-modified-since or etag info,
			# which gives us a 304 status. If that happens, assume no new items,
			# fall through rest of this and try again later.
			if http_response.status == 304:
				logger.debug("%s:data is old; moving on", feed)
				http_response.close()
				raise HTTPNotModified()
			elif http_response.status is None:
				raise HTTPError("HTTP response code is NONE")
			# If we get anything but a 200, that"s a problem and we don"t have good data,
			# so give up and try later.
			# Mostly handled different than 304/not-modified to make logging clearer.
			elif http_response.status != 200:
				raise HTTPError(str(http_response.status))

			# pull data out of the http response
			logger.debug("%s: Reading http response", feed)
			http_data = yield from http_response.read()

			# parse the data from the http response with feedparser
			feed_data = feedparser.parse(http_data)

			# If we got an ETAG back in headers, store that, so we can include on next fetch
			if "ETAG" in http_response.headers:
				etag = http_response.headers["ETAG"]
				logger.debug("%s:saving etag: %r", feed, etag)
				cursor.execute(
					"UPDATE feed_info SET etag=? where feed=? or url=?",
					[etag, feed, feed_url]
				)
				conn.commit()

			# If we got a Last-Modified header back, store that, so we can include on next fetch
			if "LAST-MODIFIED" in http_response.headers:
				modified = http_response.headers["LAST-MODIFIED"]
				logger.debug("%s:saving lastmodified: %r", feed, modified)
				cursor.execute(
					"UPDATE feed_info SET lastmodified=? where feed=? or url=?",
					[modified, feed, feed_url]
				)
				conn.commit()

			http_response.close()

			# Process all of the entries in the feed
			logger.debug("%s:processing entries", feed)
			for item in feed_data.entries:
				logger.debug("%s:item:processing this entry", feed)
				# logger.debug(item) # can be very noisy

				# Pull out the unique id, or just give up on this item.
				if "id" in item:
					id = item.id
				elif "guid" in item:
					id = item.guid
				else:
					logger.error("%s:item:no id, skipping", feed)
					continue

				# Get our best date out, in both raw and parsed form
				pubDateDict = extract_best_item_date(item)
				pubDate = pubDateDict["date"]
				pubDate_parsed = pubDateDict["date_parsed"]

				logger.debug("%s:item:id: %r", feed, id)
				# Check DB for this item
				cursor.execute(
					"SELECT published, title, url, reposted FROM feed_items WHERE id=?", [id]
				)
				data = cursor.fetchone()

				# If we"ve never seen it before, then actually processing this:
				if data is None:
					logger.info("%s: item %r not seen, processing", feed, id)

					# Store info about this item, so next time we skip it:
					cursor.execute("INSERT INTO feed_items (id, published) VALUES (?,?)", [id, pubDate])
					conn.commit()

					# Doing some crazy date math stuff...
					# max_age is mostly so that first run doesn't spew too much
					# stuff into a room, but is also a useful safety measure in
					# case a feed suddenly reverts to something ancient or other
					# weird problems...
					astz = pubDate_parsed.astimezone(timezone)
					now_tz = timezone.localize(datetime.now())
					if abs(astz - now_tz).seconds < max_age:
						# Loop over all channels for this particular feed and process appropriately:
						for channel in channels:
							logger.debug("%s:item:building message for %r", feed, channel["name"])
							message = build_message(FEED, item, channel)
							yield from send_message_wrapper(asyncioloop, FEED, feed, channel, client, message)
		except HTTPNotModified:
			# This is completely expected behavior for a well-behaved feed
			pass
		except HTTPError as e:
			# Many feeds have random periodic problems that shouldn't cause a crash
			logger.exception("%s: HTTP Error: %s", feed, e)
		# Ideally we'd remove the specific channel or something...
		# But I guess just throw an error into the log and try again later...
		# No matter what goes wrong, wait same time and try again
		finally:
			logger.debug("%s: sleeping for %i seconds", feed, rss_refresh_time)
			yield from asyncio.sleep(rss_refresh_time)


# When client is "ready", set gameplayed and log that...
@client.async_event
def on_ready():
	logger.info("Logged in as %r (%r)" % (client.user.name, client.user.id))
	gameplayed = MAIN.get("gameplayed", "github/freiheit/discord_rss_bot")
	yield from client.change_status(game=discord.Game(name=gameplayed))
	avatar_file_name = MAIN.get("avatarfile")
	if avatar_file_name:
		with open(avatar_file_name, "rb") as f:
			avatar = f.read()
		yield from client.edit_profile(avatar=avatar)


def main():
	loop = asyncio.get_event_loop()

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

# Initial Setup

These instructions are for running in a "native" OS, especially Linux.

Docker is less tested, but please see [DOCKER.md](DOCKER.md) for info on
running it via Docker (or other compatible container systems).

1. Make sure the time on your system is correct. (NTP strongly recommended)
2. Go here: https://discordapp.com/developers/applications/me#top
3. Register an "application" and create an "app bot user".
4. Replace "APP_CLIENT_ID" with the App's Client ID in this URL:
   https://discordapp.com/oauth2/authorize?&client_id=APP_CLIENT_ID&scope=bot&permissions=157696
   - Use https://discordapi.com/permissions.html to generate alt URLs
     - Send messages: absolutely required
     - Embed links: highly recommended, so that links get a preview
       (use `<link>` instead of `link` in config to avoid preview)
     - Mention @everyone, etc: only needed if intend to have bot ping users
     - Manage messages: needed if you want the bot to "publish" messages in an
       "announcement" channel so that other server owners can "Follow" and get
       those messages in their channels)
5. Give that URL to the Discord server/guild owner and have them authorize
   the bot.
6. `git clone` this repo.
7. Copy feed2discord.ini to feed2discord.local.ini
8. Put the App Bot User's 'Token' in the .ini file.
   (optional: copy just [MAIN] and that token field to feed2discord.auth.ini and
    leave it out of feed2discord.local.ini so that config is more shareable)
9. Set the timezone in the .ini file to the same as the timezone on your server.
10. Get all the channel IDs (Turn on "Developer Mode" in Settings/Appearance, then right-click channel)
11. Figure out your feeds.
   - You'll need to figure out what fields by examining what's in an item in
     your feeds. You can use `show_sample_entry.py` to help.
12. configure feeds in feed2discord.local.ini
    (anything that's not MAIN, CHANNELS or DEFAULT is assumed to be a feed)
13. Run the bot.
14. Recommended: set up as a "service" that automatically runs.
    - Look at tools/feedbot.service for example for Linux with systemd option

# Requirements
(see also requirements.txt)
- Python 3.8+ (discord.py requires 3.8+)
- sqlite3 -- Usually comes with python
- [discord.py](https://github.com/Rapptz/discord.py)
- [feedparser-rs](https://pypi.org/project/feedparser-rs/)
- [html2text](https://pypi.python.org/pypi/html2text)
- [in_place](https://pypi.org/project/in-place/) (only used by newfeed.py; otherwise optional)

## How do I figure out my timezone?
On Windows, check settings/time for the timezone or run "tzutil /g".

On Unix/Linux systems, this is more complicated than it should be.
Things to check:
- `echo $TZ`
- `ls -l /etc/localtime`
- `cat /etc/timezone`
- `find /usr/share/zoneinfo/ -type f| xargs md5sum | grep $(md5sum /etc/localtime  | cut -d' ' -f1)`
- `date +%Z` # careful, may give you a timezone only useful half of the year

## It looks like it's working, but nothing posts, and logs say `too old, skipping` for everything.
The bot compares all dates in UTC. If your system clock is wrong, brand-new posts
can appear too old to post when max_age is under 24 hours (86400).

Set `debug = 4` in your .ini (VERBOSE level) and look for output like this:
```
VERBOSE:__main__:reddit:too old, skipping
VERBOSE:__main__:reddit:now:now:1467657389.0983927
VERBOSE:__main__:reddit:now:gmtime:time.struct_time(tm_year=2016, tm_mon=7, tm_mday=4, tm_hour=18, tm_min=36, tm_sec=29, tm_wday=0, tm_yday=186, tm_isdst=0)
VERBOSE:__main__:reddit:now:localtime:time.struct_time(tm_year=2016, tm_mon=7, tm_mday=4, tm_hour=11, tm_min=36, tm_sec=29, tm_wday=0, tm_yday=186, tm_isdst=1)
VERBOSE:__main__:reddit:pubDate:datetime.datetime(2016, 7, 4, 14, 4, 44, tzinfo=datetime.timezone.utc)
```

If `now:gmtime` doesn't look like current UTC time, your system clock is wrong.
The `timezone` setting in the .ini file does not affect date comparison — all
dates are normalized to UTC internally.

Workarounds:
- Set max_age to 86400 (24 hours) or higher.
  The max_age setting is mostly just there to keep the first run on a feed from
  spamming your channels. You can run once with output only to a test channel,
  then stop, reconfigure for real channels, and run again.
# Frequently Asked Questions

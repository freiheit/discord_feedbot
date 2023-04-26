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
- Python 3.8+ (discord.py requires 3.8+. 3.9 is what I'm running it with)
See requirements.txt for the rest.

`pip3 install --user requirements.txt` should do the trick.

## How do I figure out my timezone?
On Windows, check settings/time for the timezone or run "tzutil /g".

On Unix/Linux systems, this is more complicated than it should be.
Things to check:
- `echo $TZ`
- `ls -l /etc/localtime`
- `cat /etc/timezone`
- `find /usr/share/zoneinfo/ -type f| xargs md5sum | grep $(md5sum /etc/localtime  | cut -d' ' -f1)`
- `date +%Z` # careful, may give you a timezone only useful half of the year

## It looks like it's working, but nothing posts, and logs say `too old; skipping` for everything.
Double-check your timezone settings! If the timezone isn't right, and max_age 
is under 24 hours (86400), then it's possible for brand-new posts to look too
old to post.

If it all looks right, try turning on debug in .ini. Then look for output like this:
```
DEBUG:__main__:reddit:now:1467657389.0983927
DEBUG:__main__:reddit:now:gmtime:time.struct_time(tm_year=2016, tm_mon=7, tm_mday=4, tm_hour=18, tm_min=36, tm_sec=29, tm_wday=0, tm_yday=186, tm_isdst=0)
DEBUG:__main__:reddit:now:localtime:time.struct_time(tm_year=2016, tm_mon=7, tm_mday=4, tm_hour=11, tm_min=36, tm_sec=29, tm_wday=0, tm_yday=186, tm_isdst=1)
DEBUG:__main__:reddit:timezone.localize(datetime.now()):2016-07-04 11:36:29.098784-07:00
DEBUG:__main__:reddit:pubDate:Mon Jul 04 14:04:44 UTC 2016
DEBUG:__main__:reddit:pubDate_parsed:2016-07-04 14:04:44+00:00
DEBUG:__main__:reddit:pubDate_parsed.astimezome(timezone):2016-07-04 07:04:44-07:00
```

If `now:localtime` doesn't look like your current time and/or `now:gmtime`
doesn't look like current UTC time, that's your problem. 
`now:gmtime` being off indicates your system clock is wrong.
If `now:gmtime` is right, but `now:localtime` is not, you probably have the 
timezone set wrong in your .ini file.

Workarounds:
- set timezone to "UTC" and also run the script with the `TZ` environment
  variable set to `UTC`, in order to run this one program in UTC.
- Set max_age to 86400 (24 hours) or higher.
  The max_age setting is mostly just there to keep the first run on a feed from
  spamming your channels. You can run once with output only to a test channel,
  then stop, reconfigure for real channels, and run again.
# Frequently Asked Questions

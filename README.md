# [Discord RSS Bot](https://github.com/freiheit/discord_rss_bot)

![Feed Bot](avatars/avatar-angry-small.png)

Bot for taking in an RSS or Atom feed and sharing it into a Discord channel.

Designed to be very configurable.

It should be possible to use as a library in another bot.

Since this bot doesn't *react* to anything in channels or messages, you could always
have it sign into the same account as another bot, and externally it should appear to
be a single bot.

# Elite: Dangerous related Discord?

If your Discord server is related to Elite: Dangerous, you may be able to use my feedbot instance instead of running your own copy. Read more here: https://github.com/freiheit/discord_feedbot/wiki/Getting-my-feedbot-on-your-server

## Initial Setup

1. Make sure the time on your system is correct. (NTP strongly recommended)
2. Go here: https://discordapp.com/developers/applications/me#top
3. Register an "application" and create an "app bot user".
4. Replace "APP_CLIENT_ID" with the App's Client ID in this URL:
   https://discordapp.com/oauth2/authorize?&client_id=APP_CLIENT_ID&scope=bot&permissions=153600
5. Give that URL to the Discord server/guild owner and have them authorize
   the bot.
6. `git clone` this repo.
7. Copy feed2discord.ini to feed2discord.local.ini
8. Put the App Bot User's 'Token' in the .ini file.
9. Set the timezone in the .ini file to the same as the timezone your system is set to.
10. Get all the channel IDs (Turn on "Developer Mode" in Settings/Appearance, then right-click channel)
11. Figure out your feeds.
   - You'll need to figure out what fields by examining what's in an item in your feeds. You can use `show_sample_entry.py` to help.
12. configure feeds in feed2discord.local.ini (anything that's not MAIN, CHANNELS or DEFAULT is assumed to be a feed)
13. Run the bot.

## Use as Library

I have not tested at all, but I have tried to make it possible to plug this
in as a library. Probably needs work. You'll need to replace your simple
"client.run()" with the more complicated stuff inside the if __name__ block
instead (in order to insert the background task coroutines into async)

If you want to do live changes, you should be able to change the CONFIG variable.

I'm guessing a few changes will need to be made in order to run as a
library, like maybe changing how client= gets set, to something with a
setup function that takes an optional client argument, maybe? If you figure
it out, please give me a pull request.

## Requirements
(see also requirements.txt)
- Python 3.4.2+
- [discord.py](https://github.com/Rapptz/discord.py)
- [feedparser](https://pypi.python.org/pypi/feedparser)
- [html2text](https://pypi.python.org/pypi/html2text)

## Frequently Asked Questions
### Can I have a feed ping a specific person or role?
Yes. Add a string with their ping text to the fields.

### How do I figure out what fields are in a feed? or I get "no such field" errors.
Use `show_sample_entry.py http://example.com/your_feed/thing.rss`. This
dumps out the data structure that our feed parsing library produces.

### How do I figure out my timezone?
On Windows, check settings/time for the timezone or run "tzutil /g".

On Unix/Linux systems, this is more complicated than it should be. Things to check:
- `echo $TZ`
- `ls -l /etc/localtime`
- `cat /etc/timezone`
- `find /usr/share/zoneinfo/ -type f| xargs md5sum | grep $(md5sum /etc/localtime  | cut -d' ' -f1)`
- `date +%Z` # careful, may give you a timezone only useful half of the year

### It looks like it's working, but nothing posts, and logs say `too old; skipping` for everything.
Double-check your timezone settings! If the timezone isn't right, and max_age is under 24 hours (86400), then it's possible for
brand-new posts to look too old to post.

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

If `now:localtime` doesn't look like your current time and/or `now:gmtime` doesn't look like current UTC time, that's your problem. `now:gmtime` being off indicates your system clock is wrong. If `now:gmtime` is right, but `now:localtime` is not, you probably have the timezone set wrong in your .ini file.

Workarounds:
- set timezone to "UTC" and also run the script with the `TZ` environment variable set to `UTC`, in order to run this one program in UTC.
- Set max_age to 86400 (24 hours) or higher. The max_age setting is mostly just there to keep the first run on a feed from spamming your channels. You can run once with output only to a test channel, then stop, reconfigure for real channels, and run again.

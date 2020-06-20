[![Project Status: Active – The project has reached a stable, usable state and is being actively developed.](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active)
[![Discord server invite](https://discord.com/api/guilds/720538433965129769/embed.png)](https://discord.gg/5HK2yQj)
[![pipeline status](https://gitlab.com/ffreiheit/discord_feedbot/badges/main/pipeline.svg)](https://gitlab.com/ffreiheit/discord_feedbot/-/pipelines)

# [Discord RSS Bot](https://gitlab.com/ffreiheit/discord_feedbot)

Primary location is https://gitlab.com/ffreiheit/discord_feedbot -- 
github.com/freiheit/discord_feedbot is a mirror and all others are forks.

![Feed Bot](avatars/avatar-angry-small.png)

Bot for taking in an RSS or Atom feed and sharing it into a Discord channel.

Designed to be very configurable.

It should be possible to use as a library in another bot.

Since this bot doesn't *react* to anything in channels or messages, you can
have it sign into the same account as another bot, and externally it should 
appear to be a single bot.

# Elite: Dangerous related Discord?

If your Discord server is related to Elite: Dangerous, you may be able to use
my feedbot instance instead of running your own copy.
Head to https://discord.gg/5HK2yQj and "Follow" the rooms you want.

(I have some other feeds, but E:D is the most thoroughly covered)

## Initial Setup

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

## Adding a feed
I have a utility, "newfeed.py" that helps you add a feed.

### newfeed.py setup:
1. You _must_ use feed2discord.local.ini in the current directory
2. Recommend that you also move the login_token into feed2discord.auth.ini
   (in a `[MAIN]` section)
3. In your discord, set up a "default" room and assign it all the right
   permissions for feedbot to be able to post
4. Get that channel ID and put it into `[CHANNELS]` section like
   `default = 12345678901234`

### newfeed.py usage:
1. Find the feed URL
2. Run `./newfeed.py https://example.com/blog/feed.xml` with your feed URL
3. Read what it says
4. Restart afterwards

Alternately, customize newfeed.sh to match your configuration for where the
config files are, whether or not to git commit stuff, how to restart your
feedbot, and use `./newfeed.sh https://example.com/blog/feed.xml`
(If you want to match my configuration, use linux, run everything as "bots", put
feed2discord.local.ini into /home/bots/feedbot-config/ as a private git
repository, and symlink feed2discord.local.ini to appear in feedbot's
directory)

## My configuration
The configuration of my instance of feedbot (minus auth token) is here: https://gitlab.com/ffreiheit/feedbot-config

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

If you don't need _live_ changes, since this bot is read-only, it can easily
be running alongside another bot using same account to appear as a single
user.

## Requirements
(see also requirements.txt)
- Python 3.6+ (might work with recent python 3.5.x if you modify the version 
  check, but 3.6 is what I'm running it with)
- sqlite3 -- Usually comes with python
- [discord.py](https://github.com/Rapptz/discord.py)
- [feedparser](https://pypi.python.org/pypi/feedparser)
- [html2text](https://pypi.python.org/pypi/html2text)
- [in_place](https://pypi.org/project/in-place/) (only used by newfeed.py; otherwise optional)

## Frequently Asked Questions
### Can I have a feed ping a specific person or role?
Yes. Add a string with their ping text to the fields.

### How do I figure out what fields are in a feed? or I get "no such field" errors.
Use `show_sample_entry.py http://example.com/your_feed/thing.rss`. This
dumps out the data structure that our feed parsing library produces.

### How do I figure out my timezone?
On Windows, check settings/time for the timezone or run "tzutil /g".

On Unix/Linux systems, this is more complicated than it should be.
Things to check:
- `echo $TZ`
- `ls -l /etc/localtime`
- `cat /etc/timezone`
- `find /usr/share/zoneinfo/ -type f| xargs md5sum | grep $(md5sum /etc/localtime  | cut -d' ' -f1)`
- `date +%Z` # careful, may give you a timezone only useful half of the year

### It looks like it's working, but nothing posts, and logs say `too old; skipping` for everything.
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

## Financial Support
I have a very few costs associated with this project.
I'm happy to donate my time.
But if you want to thank me financially:
[![ko-fi](https://www.ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/V7V21T7Y9)

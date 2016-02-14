# discord_rss_bot

Bot for taking in an RSS feed and spitting it over into a Discord channel.

## Setup

1.  Create a new bot account on Discord at https://discordapp.com/register
    (may require separate browser, Incognito window, private browsing mode, or
    something like that ...)
2. Invite your bot account to all the channels it needs to be in.
3. As the bot account, accept all those invites.
4. `git clone` this repo.
5. Copy feed2discord.ini to feed2discord.local.ini
6. Edit feed2discord.local.ini to include your bot's credentials and channel IDs (the last number).
7. Figure out your feeds.
   - You'll need to figure out what fields by examining what's in an item in your feeds.
8. configure feeds in feed2discord.ini (anything that's not MAIN, CHANNELS or DEFAULT is assumed to be a feed)
9. Run the bot.

## Use as Library

I have not tested at all, but I have tried to make it possible to plug this
in as a library. Probably needs work. You'll need to replace your simple
"client.run()" with the more complicated stuff inside the if __name__ block
instead (in order to insert the background task coroutines into async)

I'm guessing a few changes will need to be made in order to run as a
library, like maybe changing how client= gets set, to something with a
setup function that takes an optional client argument, maybe? If you figure
it out, please give me a pull request.

## Requirements
- Python 3.4.2+
- [discord.py](https://github.com/Rapptz/discord.py)
- [feedparser](https://pypi.python.org/pypi/feedparser)
- [html2text](https://pypi.python.org/pypi/html2text)

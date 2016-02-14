# discord_rss_bot

Bot for taking in an RSS feed and spitting it over into a Discord channel.

## Setup

1.  Create a new bot account on Discord at https://discordapp.com/register
    (may require separate browser, Incognito window, private browsing mode, or
    something like that ...)
2. Invite your bot account to all the channels it needs to be in.
3. As the bot account, accept all those invites.
4. `git clone` this repo.
5. Edit feed2discord.ini to include your bot's credentials and channel IDs (the last number).
6. Run the bot.

## Requirements
- Python 3.4.2+
- [discord.py](https://github.com/Rapptz/discord.py)
- [feedparser](https://pypi.python.org/pypi/feedparser)
- [html2text](https://pypi.python.org/pypi/html2text)

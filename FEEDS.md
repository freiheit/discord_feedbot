# Adding a feed
I have a utility, "newfeed.py" that helps you add a feed.

## newfeed.py setup:
1. You _must_ use feed2discord.local.ini in the current directory
2. Recommend that you also move the login_token into feed2discord.auth.ini
   (in a `[MAIN]` section)
3. In your discord, set up a "default" room and assign it all the right
   permissions for feedbot to be able to post
4. Get that channel ID and put it into `[CHANNELS]` section like
   `default = 12345678901234`

## newfeed.py usage:
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

# Frequently Asked Questions (FAQ)
## Can I have a feed ping a specific person or role?
Yes. Add a string with their ping text to the fields. Like `<@12345678910112>`

## How do I figure out what fields are in a feed? or I get "no such field" errors.
Use `show_sample_entry.py http://example.com/your_feed/thing.rss`. This
dumps out the data structure that our feed parsing library produces.

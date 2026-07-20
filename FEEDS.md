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
Use `show_sample_entry.py http://example.com/your_feed/thing.rss`. It prints one
`=== token ===` block per reachable field, where the `token` is exactly what you
put in a `fields = ...` line — including dotted names (see below) — followed by
the value. Copy the tokens you want; no need to read the raw feed.

## How do I access podcast / namespaced / attribute fields (itunes, enclosure)?
Feed data that lives inside a sub-object is reached with a **dotted** name:

- `itunes.duration`, `itunes.explicit`, `itunes.author` — the `<itunes:*>` tags.
- `enclosures.href` — the media (mp3) URL; also `enclosures.length`,
  `enclosures.type`. (feedparser stores the enclosure `url=` attribute as `href`.)
- `image.href`, `links.href`, `tags.term` — likewise.

Dotted names compose with all the usual markup, e.g. `<enclosures.href>` posts
the media URL with Discord's link preview suppressed, `` `itunes.duration` ``
renders it as inline code.

When the base is a **list** (enclosures, links, tags), a dotted name gives the
**first** element. To join **all** of them, use the `[delim]field.key` form —
e.g. `[; ]tags.term` joins every tag's term with `"; "`. Because the `fields`
line is itself comma-separated, the delimiter **can't contain a comma** (use
`; `, ` / `, or similar). `show_sample_entry.py` flags list-backed fields so you
know when this applies.

## How do I post the full article body, not just the summary?
Use the `content` field. Atom `<content>`, RSS `<content:encoded>`, and JSON
Feed `content_html` all parse into `content`, which renders like any other text
field — e.g. `fields = ##title,link,content` (or `>content` to blockquote it).
`show_sample_entry.py` now prints the `content` field so you can preview it.

## The full content is longer than one Discord message. What happens?
Long posts are automatically split across multiple messages (Discord caps a
single message at 2000 characters). Split messages get a small subtext marker
(`... continuing in next message` / `... continued from previous message`) so
readers can tell. To cap how many messages one item may produce, set
`max_messages` on the feed (or `channelname.max_messages` per channel). The
default is `0` = unlimited; a positive value keeps that many messages and marks
the last one `... post truncated`.

## In what order are new entries posted?
Oldest first, newest last. When a poll finds several new entries (including on a
feed's first run), they're sorted by their published date and sent in that order,
one at a time, with a pause between each. The pause — `send_interval`, default 3
seconds — keeps Discord from reordering a fast burst, so the order sent matches
the order shown. Set `send_interval` on a feed (or `channelname.send_interval`
per channel); `0` disables the pause. This is separate from `delay`, which just
offsets when a channel's batch starts.

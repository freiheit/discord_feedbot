[![Discord server](https://discord.com/api/guilds/910747606722965555/embed.png)](https://discord.com/servers/feedbot-910747606722965555)
[![Latest Release](https://gitlab.com/ffreiheit/discord_feedbot/-/badges/release.svg)](https://gitlab.com/ffreiheit/discord_feedbot/-/releases)
[![CII Best Practices](https://bestpractices.coreinfrastructure.org/projects/6176/badge)](https://bestpractices.coreinfrastructure.org/projects/6176)
[![Project Status: Active – The project has reached a stable, usable state and is being actively developed.](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active)
[![CodeQL](https://github.com/freiheit/discord_feedbot/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/freiheit/discord_feedbot/actions/workflows/codeql-analysis.yml)
[![Dependency Review](https://github.com/freiheit/discord_feedbot/actions/workflows/dependency-review.yml/badge.svg)](https://github.com/freiheit/discord_feedbot/actions/workflows/dependency-review.yml)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](code_of_conduct.md)

# [Discord RSS Bot](https://gitlab.com/ffreiheit/discord_feedbot)

Primary location is https://gitlab.com/ffreiheit/discord_feedbot -- 
github.com/freiheit/discord_feedbot is a mirror and all others are forks.

![Feed Bot](avatars/avatar-angry-small.png)

[[_TOC_]]

Bot for taking in an RSS or Atom feed and sharing it into a Discord channel.

Designed to be very configurable.

It should be possible to use as a library in another bot. (But can simply
run with same tokens instead, since it's write-only to discord)

Since this bot doesn't *react* to anything in channels or messages, you can
have it sign into the same account as another bot, and externally it should 
appear to be a single bot.


# Elite: Dangerous related Discord?

If your Discord server is related to Elite: Dangerous, you may be able to use
my feedbot instance instead of running your own copy.
Head to https://discord.gg/s97tH5Bsw6 and "Follow" the rooms you want.

(I have some other feeds, but E:D is the most thoroughly covered)

# Docker usage 

For an **easy startup and configuration** you can try it with docker

See [DOCKER.md](DOCKER.md)

# Installation / Initial Setup

See [INSTALLING.md](INSTALLING.md)

# Adding feeds

See [FEEDS.md](INSTALLING.md)


# My configuration
The configuration of my instance of feedbot (minus auth token) is here: https://gitlab.com/ffreiheit/feedbot-config

# Use as Library
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

# Frequently Asked Questions (FAQ)
FAQ items are in 
[INSTALLING.md](INSTALLING.md) and 
[FEEDS.md](FEEDS.md)

# Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Chat with me
Come to https://discord.gg/s97tH5Bsw6 and talk in the #dev room.

## Bug reports
Preferred avenue for bug reports is an Issue filed at https://gitlab.com/ffreiheit/discord_feedbot/-/issues

### Security Bugs
Check the "confidential" checkbox when submitting an Issue at https://gitlab.com/ffreiheit/discord_feedbot/-/issues

# Financial Support
I have a very few costs associated with this project. (under $10/month)
I'm happy to donate my time.
But if you want to thank me financially:
[![ko-fi](https://www.ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/V7V21T7Y9)
Or: https://discord.com/servers/feedbot-910747606722965555


# [Discord RSS Bot, now with whales!](https://github.com/freiheit/discord_feedbot)

![Angry docker avatar](avatars/docker-avatar.png)

Bot for taking in RSS or Atom feeds and sharing them to Discord channels, now stuffed (without grace, mind you) into a tiny container.

## Introduction

This file **is not** intended as an introduction to Discord Feed Bot, nor as an introduction to Docker.
 * If you are unfamiliar with Discord Feed Bot, consult the [ReadMe](README.md).
 * If you are unfamiliar with Docker, check out the [Introduction to Docker](https://training.docker.com/introduction-to-docker) webinar, or consult your favorite search engine.

On the other hand, this file **is** intended to:
 * Take your already operational Discord Feed Bot...
 * And stick it into a ~~shoe box~~ container so that you can ~~hide it under your bed~~ put it on to the back of whale/cargo ship/freight train/lorry, and never worry about it again.

## Build the image

Simply run the following command from the Discord Feed Bot source directory to build your new image
```
docker build -t feedbot .
```

## Simple running command
Simply run this command
```
docker run -d -v $(pwd)/config.json:/config/config.json --name=feedbot feedbot
```
And `config.json` ***MUST*** be with the bellow format. You can add as much config as needed in the `feeds` list
```
{
    "token": "OTg1MzgZE3cwNDQyMTF67TU5.GFl0nX.WeyI8vqX3yO6kqh8Oia6cDpgEkZ1zH6eNHN9w8",
    "feeds": [
        {
            "name": "CERT-FR",
            "channel": "984379931012256123",
            "url": "https://www.cert.ssi.gouv.fr/alerte/feed/",
            "fields": "guid,**title**,_published_,description"
        }
    ]
}
```
## Full configurations usage
```
docker run \ 
-d -v $(pwd)/config.json:/config/config.json \
-e DEBUG=2 \
-e TIMEZONE='utc' \
-e PUBLISH=0 \
-e SKEW_MIN=1 \
-e REFRESH_TIME=900 \
-e MAX_AGE=86400 \
--name=feedbot \
feedbot
```

### Allow variables
You can configurate the bot using [environnement variables](https://docs.docker.com/engine/reference/run/#env-environment-variables). 
You can avoid using json config file by simply adding token and feeds through environnement variables
| Command | Explanation | Default value |
|----|----| ----|
| `TOKEN` | Only if `config.json` is not set. Your bot token, it's **mandatory** variable. | "" |
| `FEEDS` | Only if `config.json` is not set. The feeds you want to be used by the bot. | "" |
| `DEBUG` | Debug mode number. <ul><li>`0` just prints some basic info as thing runs <li>`1` prints debug stuff for this bot</li><li>`2` and above start printing debug stuff for libraries, etc</li><li>`4` and above includes full parsed items, which can be quite verbose... </li> | 2 |
| `TIMEZONE` | Your timezone in string  | "utc"
| `PUBLISH` | Feature to post on channel followed on multiple server. | 0 |
| `SKEW_MIN` | Minimum sleep time at startup, in second | 1 |
| `REFRESH_TIME` | Time between refreshes of a feed, in second | 900 |
| `MAX_AGE` | Maximum age of a post before it's discarded, in second | 86400 |

You should now (hopefully) notice something similar to this in your discord window:
![phroggbot "Playing with containers"](https://i.imgur.com/lF7kMW0.png)

**Note:** If you've previously run a container named feedbot, this command will fail. In that instance, you can use:
```
docker stop feedbot
```

## Monitor

The Docker model for feedbot will output logging to STDOUT. This means that you can monitor a container's output with: `docker logs feedbot` The value set in your configuration file for debug will determine how much information is output to this log, just as if you were running it manually.
To determine whether feedbot is even running, consult the output from either `docker ps` (to show only running containers) or `docker ps -a` (to show all configured containers).

## Test/Debug

The testing utilities (`show_all_entries.py` and `show_sample_entry.py`) are included inside of the image we [built](#build) earlier. You can access them using the following syntax:
```
docker run --rm feedbot python /opt/show_sample_entry.py https://github.com/freiheit/discord_feedbot/releases.atom
docker run --rm feedbot python /opt/show_all_entries.py https://github.com/freiheit/discord_feedbot/releases.atom
```

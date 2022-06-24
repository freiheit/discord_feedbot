# [Discord RSS Bot, now with whales!](https://github.com/freiheit/discord_feedbot)

![Angry docker avatar](avatars/docker-avatar.png)

Bot for taking in RSS or Atom feeds and sharing them to Discord channels, now stuffed (without grace, mind you) into a tiny container.

# Introduction

This file **is not** intended as an introduction to Discord Feed Bot, nor as an introduction to Docker.
 * If you are unfamiliar with Discord Feed Bot, consult the [ReadMe](README.md).
 * If you are unfamiliar with Docker, check out the [Introduction to Docker](https://training.docker.com/introduction-to-docker) webinar, or consult your favorite search engine.

On the other hand, this file **is** intended to:
 * Take your already operational Discord Feed Bot...
 * And stick it into a ~~shoe box~~ container so that you can ~~hide it under your bed~~ put it on to the back of whale/cargo ship/freight train/lorry, and never worry about it again.

## Starting Out

You should already have:
 * A fully operational Discord Feed Bot, whether virtualized or bare metal. If you don't, go and [learn how to do this](README.md), and then come back here.
 * Docker installed and operational on at least one node.
 * Reliable time synchronization on your docker node(s), e.g. NTP. This is a fairly common thing to already have built-in to an operating system, whether or not you're aware of it. Still, you should verify that it is functioning.
 * A copy of the Discord Feed Bot source, whether from a [tarball or zip file](https://github.com/freiheit/discord_feedbot/releases), or a local git clone.
 * Access to your existing Discord Feed Bot configuration file, and any avatars that you have been or will be using.

Important vocabulary:
 * Docker Image: a compiled "picture" of an operating system. You `build` an Image.
 * Docker Container: an instance of a particular Docker Image that retains some configuration details. You `run` an Image to turn it into a Container.

## "But I want it running now, and you mince many words!"

Ok, fine. Really can't blame you. Try:
```
docker build -t feedbot .
docker run -v $PWD:/home/feedbot -d feedbot
```
**But you really, really,  should not do that if you care about your sanity or security**. Please read on for the *right way* to do it.

## Build

Simply run the following command from the Discord Feed Bot source directory to build and tag your new image:
```
docker build -t feedbot .
docker tag feedbot feedbot:latest
docker tag feedbot feedbot:0.0.1
```
Assuming that it succeded, you should now have a new docker image named `feedbot`. Yay! Wait, why am I getting excited; it doesn't even do anything, yet! Shame on you for getting me all worked up. Tsk tsk.

For future builds, you should update the `0.0.1` version tag appropriately. This helps you if you ever want to migrate a stable image onto another node, or just want to revert back to an older image when the newest one doesn't work out as well as you had hoped.

## Configure

What you likely want to do is create a new folder inside your home directory (preferably outside of the source repository, but that's not mandatory; just good practice) and copy your existing configuration file, database, and avatar(s) over to it. In other words, *something* like the following:
```
mkdir ~/dockerfeedbot
cp feed2discord.local.ini feed2discord.db ~/dockerfeedbot/
cp avatars/my_cool_avatar.png ~/dockerfeedbot/
```
To make it perfectly clear: Docker will not be able to access files outside of the compiled image and those contained within this folder. So just copy any needed files into your new folder, and update the configuration file (`avatarfile` and `db_path`) if you need to. It's suggested to just use the filename in your .local.ini file, with no path information, but relative paths under this folder are fine (i.e. `avatarfile = avatars/bob.png` when the file on-disk is at `~/dockerfeedbot/avatars/bob.png`; same for `db_path`).

## Run

To create and start the container, the following should work. Depending on your operating system, docker version, and user group/privileges, you may need to preface this command with `sudo`:
```
docker run -e TZ='UTC' -v ~/dockerfeedbot:/home/feedbot --restart on-failure:10 --name feedbot -d feedbot
```
To break this command down:

| Command | Explanation |
|----|----|
| `docker run` | Start a new instance of a docker container. |
| `-e TZ='UTC'` | Set the "system" timezone to UTC (you may want to change this, depending on a number of factors, but UTC *should* work best). |
| `-v ~/dockerfeedbot:/home/feedbot` | Use the configuration files from `~/dockerfeedbot` to *mirror* into the container's `/home/feedbot` folder. |
| `--restart on-failure:10` | Docker will automatically (re-)start feedbot, up to ten times, if any of the following occur: <ul><li>feedbot crashes.</li><li>the docker daemon restarts.</li><li>the host reboots (on a supported operating system).</li></ul>
| `--name feedbot` | Name the container `feedbot`. |
| `-d` | Run the container detached (without a console). |
| `feedbot` | Construct the container from the image most recently tagged as `feedbot`. You can append a version number, if desired, such as `feedbot:0.0.1`. |

You should now (hopefully) notice something similar to this in your discord window:
![phroggbot "Playing with containers"](https://i.imgur.com/lF7kMW0.png)

**Note:** If you've previously run a container named feedbot, this command will fail. In that instance, you can use:
```
docker stop feedbot
docker rename feedbot feedbot.1
```
Followed by the full `docker run` command. If you're not interested in keeping the old docker container, and are positive that the new version is 100% successful and without issues, it's even easier:
```
docker stop feedbot
docker rm feedbot
```
Again, followed by the full `docker run` command.

## Stop

Easy! Simply run `docker stop feedbot` to stop the container named feedbot.

## Monitor

The Docker model for feedbot will output logging to STDOUT. This means that you can monitor a container's output with: `docker logs feedbot` The value set in your configuration file for debug will determine how much information is output to this log, just as if you were running it manually.
To determine whether feedbot is even running, consult the output from either `docker ps` (to show only running containers) or `docker ps -a` (to show all configured containers).

## Test/Debug

The testing utilities (`show_all_entries.py` and `show_sample_entry.py`) are included inside of the image we [built](#build) earlier. You can access them using the following syntax:
```
docker run --rm feedbot show_sample_entry.py https://github.com/freiheit/discord_feedbot/releases.atom
docker run --rm feedbot show_all_entries.py https://github.com/freiheit/discord_feedbot/releases.atom
```

| Command | Explanation |
|----|----|
| `docker run` | Start a new instance of a docker container. |
| `--rm` | Delete the container once it is done executing (since it is extremely short-lived and would only waste resources). |
| `feedbot` | Construct the container from the image most recently tagged as `feedbot`. |
| <ul><li>`show_sample_entry.py`</li><li>`show_all_entries.py`</li></ul> | The command to run inside of the container. |
| `https://github.com/freiheit/discord_feedbot/releases.atom` | Any parameters for the command provided above. For the included `show_X.py` tools, this is the URL of a feed to debug, such as the release feed for this project. |

You can also directly spawn an ash shell inside of the contianer to take a peek around with the following, but this is ultimately not very useful in practice:
```
docker run --rm -it -e TZ='UTC' -v ~/dockerfeedbot:/home/feedbot feedbot ash
```
`-it` means run with an interactive terminal, and the rest should all be familiar to you by now.

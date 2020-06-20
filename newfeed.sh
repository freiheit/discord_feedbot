#!/bin/sh
(
  cd /home/bots/feedbot-config
  git pull
  git commit -am "autoCommit before newfeed.py"
  git push
)

./newfeed.py "$@"

(
  cd /home/bots/feedbot-config
  git pull
  git commit -am "autoCommit after newfeed.py: $@"
  git push
)

sudo /bin/systemctl restart feedbot

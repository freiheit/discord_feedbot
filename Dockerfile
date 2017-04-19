FROM alpine:3.5
LABEL maintainer "Eric Eisenhart <discord-feedbot-docker@eric.eisenhart.name>"

# Base image setup and important dependencies
RUN apk add --update --no-cache \
		ca-certificates \
		libressl2.4-libssl \
		python3 && \
	python3 -m ensurepip && \
	rm -r /usr/lib/python*/ensurepip && \
	pip3 install --upgrade \
		pip \
		setuptools && \
	pip3 install \
		aiohttp \
		discord.py \
		feedparser \
		html2text \
		python-dateutil \
		pytz \
		requests \
		websockets \
		ws4py \
		&& \
	rm -r /root/.cache

# discord_feedbot setup follows
COPY *.py /usr/local/bin/

RUN chmod 0755 /usr/local/bin/* && \
	adduser -D feedbot

# Note that the feedbot user will end up as 1000.1000, meaning that a
# Docker breakout exploit will still need to ecalate to exploit more.
# This also means that the config files will be owned by 1000.1000 on
# the host, making for easy editing back and forth by the default user.
USER feedbot
VOLUME ["/home/feedbot"]
WORKDIR /home/feedbot

ENV PATH="/usr/local/bin:${PATH}"
CMD ["feed2discord.py"]

FROM python:3.11.3-alpine3.16
LABEL maintainer "Eric Eisenhart <discord-feedbot-docker@eric.eisenhart.name>"

# Base image setup and important dependencies
RUN apk add --update --no-cache \
		ca-certificates \
		libressl-dev

# Create user feedbot for security purpose and switch to it
RUN adduser -D -u 1000 feedbot
USER feedbot
WORKDIR /home/feedbot

# Add path for pip modules
ENV PATH="~/.local/bin:${PATH}"

COPY *.py requirements.txt /opt/
COPY templates/* /opt/templates/

RUN python -m pip install --no-cache-dir --upgrade pip && \
	python -m pip install --no-cache-dir -r /opt/requirements.txt

# Note that the feedbot user will end up as 1000.1000, meaning that a
# Docker breakout exploit will still need to ecalate to exploit more.
# This also means that the config files will be owned by 1000.1000 on
# the host, making for easy editing back and forth by the default user.

# Generate feed2discord config file based on env vars set by user
CMD python /opt/DockerConfigBuilder.py &&\
	python /opt/feed2discord.py

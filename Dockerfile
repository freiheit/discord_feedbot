FROM python:3.14-slim
LABEL maintainer="Eric Eisenhart <discord-feedbot-docker@eric.eisenhart.name>"

# Install runtime dependencies from the same list a bare-metal install uses.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
	rm /tmp/requirements.txt

# discord_feedbot setup follows
COPY *.py /usr/local/bin/

# Note that the feedbot user will end up as 1000.1000, meaning that a
# Docker breakout exploit will still need to escalate to exploit more.
# This also means that the config files will be owned by 1000.1000 on
# the host, making for easy editing back and forth by the default user.
RUN chmod 0755 /usr/local/bin/*.py && \
	useradd --create-home --uid 1000 feedbot

USER feedbot
VOLUME ["/home/feedbot"]
WORKDIR /home/feedbot

CMD ["feed2discord.py"]

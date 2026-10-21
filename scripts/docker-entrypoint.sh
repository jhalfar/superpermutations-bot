#!/bin/sh
# Starts the bot. The token must come from the environment (compose passes it from .env).
set -eu

if [ -z "${DISCORD_TOKEN:-}" ]; then
    echo "DISCORD_TOKEN is not set: put it in .env (see .env.example)" >&2
    exit 1
fi

# the table lives in the /data volume; seed it from the bundled copy on the first start
records="${SUPERPERM_RECORDS:-/data/records.json}"
if [ ! -f "$records" ]; then
    mkdir -p "$(dirname "$records")"
    cp /app/bot/records.json "$records"
    echo "seeded $records from the bundled records.json"
fi

exec python -I /app/bot/discord_bot.py

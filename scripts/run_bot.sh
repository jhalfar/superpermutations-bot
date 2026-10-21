#!/bin/sh
# run_bot.sh - start the bot on Linux or macOS without Docker, with the settings from .env in the repository root
# (the parent of scripts/).
# Only DISCORD_TOKEN and SUPERPERM_* keys are read (KEY=VALUE, no quotes); the file is parsed, not executed, and
# no value is printed. SUPERPERM_PYTHON chooses the Python (default python3). Stop with Ctrl+C.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)     # the repository root
[ -f "$here/.env" ] || { echo "No .env in $here. Copy .env.example to .env and fill it in."; exit 1; }
keys=""
while IFS= read -r line || [ -n "$line" ]; do
    line=$(printf '%s' "$line" | tr -d '\r')
    case "$line" in ''|'#'*) continue;; esac
    key=${line%%=*}; val=${line#*=}
    case "$key" in DISCORD_TOKEN|SUPERPERM_[A-Z_]*) ;; *) echo "ignored unknown key $key"; continue;; esac
    [ -n "$val" ] || continue
    export "$key=$val"; keys="$keys $key"
done < "$here/.env"
if [ -z "${DISCORD_TOKEN:-}" ]; then
    printf 'Discord token (not shown): '; stty -echo; read -r DISCORD_TOKEN; stty echo; echo; export DISCORD_TOKEN
fi
echo "settings from .env:$keys"
py=${SUPERPERM_PYTHON:-python3}
cd "$here"
[ -f bin/BUILD.json ] || "$py" -I checker/build.py
exec "$py" -I bot/discord_bot.py

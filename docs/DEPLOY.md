# Deploying superpermutations-bot on a Linux host with Docker

You need a Linux machine with Docker and the compose plugin, and a Discord account that can add a bot to your server.
Memory decides which n the bot can check: see "Memory sizing" below.

## 1. Create the Discord application and the bot

1. Open https://discord.com/developers/applications and press "New Application". Name it.
2. In the "Bot" tab press "Reset Token" and copy the token. You see it once. Treat it like a password.
3. Leave all three "Privileged Gateway Intents" off. The bot does not need them.
4. Keep "Public Bot" off if only you should be able to add it to servers.

## 2. Invite the bot to your server

The bot needs these permissions (they are the ones in docs/REFERENCE.md and in `/about`; the thread permissions of the first
test version are no longer needed):

| permission | bit | value |
|---|---|---|
| Send Messages | 1 << 11 | 2,048 |
| Embed Links | 1 << 14 | 16,384 |
| Read Message History | 1 << 16 | 65,536 |
| Manage Roles (only for the Contributor role) | 1 << 28 | 268,435,456 |

Sum without Manage Roles: 2,048 + 16,384 = 18,432; 18,432 + 65,536 = 83,968.
Sum with Manage Roles: 83,968 + 268,435,456 = 268,519,424.

Invite URL (replace `APPLICATION_ID` with the "Application ID" of the General Information tab; this is the version with
Manage Roles, for the Contributor role; without that role use `permissions=83968`):

```
https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot%20applications.commands&permissions=268519424
```

`/panel` needs no extra permission of the bot: the slash command is limited to members with Manage Server (the server can
change that under Server Settings, Integrations).

The bot also needs to see the channel it works in (View Channel). A normal channel gives that through `@everyone`. In a
private channel add the bot's role to the channel.

## 3. Get the channel and role ids

In Discord open User Settings, Advanced, and turn on Developer Mode. Then right-click the channel for "new record"
announcements and choose "Copy Channel ID". Right-click the Contributor role (Server Settings, Roles) and choose
"Copy Role ID". Both are optional. If you use the role, drag the bot's own role above it in the role list, otherwise the
bot cannot give it. The bot never removes a role.

## 4. Configure and start

```
git clone https://github.com/jhalfar/superpermutations-bot.git
cd superpermutations-bot
cp .env.example .env
chmod 600 .env
nano .env                       # DISCORD_TOKEN, SUPERPERM_CHANNEL, SUPERPERM_CONTRIBUTOR_ROLE, SUPERPERM_MEM_GB
docker compose up -d --build
docker compose logs -f
```

The first build takes a few minutes (it installs gcc and compiles the checkers). The entrypoint refuses to start without
`DISCORD_TOKEN`. On the first start it copies the bundled `bot/records.json` into the `bot-data` volume.

Check that it works: in Discord type `/about`. The bot answers with a card with its limits and permissions. `/records` shows the table
as a card with a select for the details of one n.
Slash commands can take a minute to appear after the first start.

## Update

```
git pull
docker compose up -d --build
```

The volumes stay. A restart drops the queue (see below).

## Backup and restore of records.json

```
docker compose cp bot:/data/records.json ./records.backup.json      # backup
docker compose cp ./records.backup.json bot:/data/records.json      # restore (the bot reads the file at each use)
```

`docker compose cp` into a running container writes as root. If the bot then cannot save, run
`docker compose exec -u root bot chown 10001:10001 /data/records.json`. The file is written atomically by the bot.
If the container is stopped, `docker compose cp` still works for backup.

## Rotate the token

1. In the Developer Portal press "Reset Token" and copy the new one. The old token stops working at once.
2. Edit `.env` and replace `DISCORD_TOKEN`.
3. `docker compose up -d` (compose sees the changed environment and recreates the container).

If the token was ever posted anywhere public, reset it right away.

## Memory sizing

The checker keeps a bit table of n!/8 bytes.

* n = 14 unsharded needs about 12 GB at peak (11.8 GB for the table and buffers, 0.6 GB more for a recipe's generator).
  Size the host for 16 GB.
* With less memory the bot checks in k passes over ranges of the permutations (about 4 GB for k = 4), which takes k times
  as long. `SUPERPERM_MEM_GB` is the limit of one pass; the planner picks k from it. It refuses a job that would need more
  than `SUPERPERM_MAX_SHARDS` passes or more than `SUPERPERM_MAX_HOURS` hours.
* `SUPERPERM_MEM_GB` must stay about 1.5 GB below `mem_limit` in `compose.yaml` (the container also holds the bot
  process). The defaults are 14 and 16g. For an 8 GB host use for example `SUPERPERM_MEM_GB=6.5` and `mem_limit: 8g`,
  and n = 14 will be done in 2 passes.
* n <= 12 needs well under 1 GB. n = 13 needs about 1.7 GB in one pass.

## What is lost at a restart

The queue and the job numbers are in memory. When the bot or the host restarts, waiting and running checks are dropped
and users must send them again. The table (`records.json`) is in the volume and survives.

## GitHub Action from another repository

The folder `action/` is a GitHub Action that checks the superpermutation files in a repository. It builds and runs the
checkers from `checker/` and `gens/` of this repository (GitHub fetches the whole repository for `uses:`):

```yaml
- uses: actions/checkout@v4
- uses: jhalfar/superpermutations-bot/action@<tag>
```

See the README (section "GitHub Action") for the inputs and the output.

# superpermutations-bot

A Discord bot that checks superpermutations and keeps a table of the shortest verified word for each n.

You send a word, and the bot streams it through a checker and reports whether every permutation of its n symbols
occurs, together with the length and the SHA-256 of the letters. A valid word can go into the records table, and a new
record is announced in a channel. The bot accepts n up to 14 in three forms:

- a plain word: `.txt` or `.txt.xz`;
- a word in Theo H.'s SPRA format: `.spra` or `.spra.xz` ([github.com/Tholsh/SPRA](https://github.com/Tholsh/SPRA));
- a recipe for very long words: `.splan.json.xz` ([docs/RECIPE.md](docs/RECIPE.md)). The n = 14 word has 94 GB, while
  its recipe is about 0.6 MB.

The checkers are small C programs that the bot builds from `gens/`. Nothing a user sends is run as code.

Related work and data: https://github.com/jhalfar/superpermutations

## Quick start

With Docker (recommended):

```
cp .env.example .env            # fill in DISCORD_TOKEN and, if you want them, the channel and role ids
chmod 600 .env
docker compose up -d --build
docker compose logs -f
```

Without Docker (Python 3.10+ and gcc):

```
pip install -r requirements.txt
cp .env.example .env
./scripts/run_bot.sh                                          # Linux, macOS
powershell -ExecutionPolicy Bypass -File scripts\run_bot.ps1  # Windows
```

The scripts read only the `DISCORD_TOKEN` and `SUPERPERM_*` lines of `.env` (they parse it and never execute it). They
build `bin/` on the first start. [docs/DEPLOY.md](docs/DEPLOY.md) covers creating the Discord application, the invite
link, updates, backups and memory sizing.

## Commands

| command | what it does |
|---|---|
| `/check [attachment\|url] [submit] [visibility]` | checks a word or a recipe: an upload up to 64 MB, or a link on github.com / gitlab.com; `submit: true` adds a valid word to the table |
| `/records` | the table: the shortest verified word and the shortest published claim for each n |
| `/queue`, `/cancel` | the two lanes (n <= 12 and n = 13, 14) and their jobs; cancel your own job |
| `/history` | your past checks, with delete and rename |
| `/settings` | your default visibility: public or private |
| `/about`, `/panel` | limits and links; a message with a "Check a word" button (Manage Server) |

Each check gets one card that shows the queue position, then a progress bar, then the result, with *Cancel*, *Add to
records* (only the sender) and *Details* buttons. A **public** check is posted in the channel. A **private** check goes to
the sender by direct message, and the channel gets only a short note with no file name, length or result. Running
`/check` in a direct message with the bot leaves no trace on the server at all. Moderators (Manage Messages) can cancel
any job and see that a private check exists, but not its content. Administrators see everything, and an optional audit
channel keeps a copy of every private check.

## Settings

Environment variables; Docker reads them from `.env` (see [.env.example](.env.example)).

| variable | default | meaning |
|---|---|---|
| `DISCORD_TOKEN` | required | the bot token |
| `SUPERPERM_CHANNEL` | none | channel for new-record announcements |
| `SUPERPERM_CONTRIBUTOR_ROLE` | none | role given to the sender of a new record (the bot needs Manage Roles, and its role must be above this one) |
| `SUPERPERM_SUBMIT_ROLE` | none | if set, only members with this role can add words to the table; anyone can still check |
| `SUPERPERM_AUDIT_CHANNEL` | none | channel for a copy of every private check (give it to admins only) |
| `SUPERPERM_MEM_GB` | 14 | memory for one pass of a check; with less, long checks are split into several passes |
| `SUPERPERM_MAX_SHARDS`, `SUPERPERM_MAX_HOURS` | 8, 6 | refuse a check that needs more passes or more time than this |
| `SUPERPERM_MAX_N`, `SUPERPERM_QUEUE` | 14, 6 | the largest n; the number of waiting jobs per lane |
| `SUPERPERM_MBPS` | 30 | assumed speed in million letters per second, used for the time estimates |
| `SUPERPERM_RECORDS` | `bot/records.json` (Docker: `/data/records.json`) | the table; `settings.json` and `history.sqlite` are kept next to it |
| `SUPERPERM_HISTORY_DAYS` | 180 | how long the history of checks is kept (it never stores the word) |

The bot asks for no privileged intents. It needs Send Messages, Embed Links and Read Message History, and Manage Roles
only for the Contributor role. All variables, the permission integers and the behaviour of every card are in
[docs/REFERENCE.md](docs/REFERENCE.md).

## How long a check takes

| n | letters | memory | time |
|---:|---:|---:|---:|
| 12 | 0.52 G | 60 MB | 25 s |
| 13 | 6.7 G | 1.7 GB | 4 min |
| 14 (recipe) | 93.9 G | 11.8 GB | about 1 h |

Two lanes, one for n <= 12 and one for n = 13 and 14, keep long checks from blocking short ones. With a smaller
`SUPERPERM_MEM_GB` the bot splits a check into passes over ranges of the permutations: less memory, more time.

## Safety

- The bot treats every file as data. A recipe chooses its generator by id from `checker/registry.py`, and the generator
  is built from C sources in this repository whose SHA-256 the recipe must name.
- Uploads go into a temporary folder that is deleted afterwards. Downloads are limited to a few hosts, sizes and times
  are capped, and checks run with a memory limit and at low priority. Docker adds a read-only root, no capabilities and
  CPU and memory limits.
- The parsers were fuzzed before the release: the SPRA decoder, the n = 14 generator and both checkers.
- The bot never pings anyone, never removes a role and reads the token only from the environment.

Details: [docs/REFERENCE.md](docs/REFERENCE.md), section "Security model".

## GitHub Action

The same checker runs on the words in a repository on every push:

```yaml
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jhalfar/superpermutations-bot/action@v1.0
```

It finds `superpermutation-*.txt[.xz]`, `superpermutation-*.spra.xz` and `*.splan.json.xz`, checks each one, compares
n and the length in the file name with the result, and writes a table with the SHA-256 values to the job summary.

## Repository

```
bot/       the Discord bot (entry point bot/discord_bot.py) and the seed table records.json
checker/   check_any.py and the code that streams words, SPRA files and recipes into the C checkers
gens/      C sources: the n = 14 generator, two checkers, the SPRA decoder
action/    the GitHub Action
tools/     recipe writer, seed-table builder, card previews, live-test files
scripts/   start scripts and the Docker entrypoint
tests/     offline tests
docs/      DEPLOY, REFERENCE, RECIPE, UI_DESIGN, LIVE_TEST, DEVNOTES
```

Command line use and tests, from the repository root:

```
python -I checker/build.py                 # builds bin/
python -I checker/check_any.py WORD.txt.xz # one JSON line; exit code 0 if the word is valid
python -I tests/test_check.py              # also test_bot, test_ui, test_spra, test_history
python -I tools/preview_cards.py           # every card of the bot as plain text
```

Some tests need words that are not in this repository; they skip themselves and say which variable to set
([docs/REFERENCE.md](docs/REFERENCE.md), "Test data").

## Known limits

- A restart of the bot drops the jobs that are waiting or running; users send them again. Job numbers and the history
  carry over.
- Recipes exist only for n = 14, because the shorter words are small enough to send as they are (the n = 13 word is
  about 1.3 MB as `.spra.xz`). The recipe format `splan/1` is fixed ([docs/RECIPE.md](docs/RECIPE.md)).
- By default anyone on the server can add a verified word to the table. Set `SUPERPERM_SUBMIT_ROLE` to limit it to a
  role. Moderators can remove or rename entries.

## License

Apache 2.0 ([LICENSE](LICENSE)).

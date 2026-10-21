# Reference

The details behind README.md: what each card and command does, limits, sharding, the security model, the SPRA decoder, resources per n, and the variables of the tests.

## Files

| file | what |
|---|---|
| `bot/discord_bot.py` | the entry point: builds the Discord client, registers the slash commands and buttons, runs |
| `bot/commands.py` | the handlers of `/check` (and its form), `/queue`, `/cancel`, `/records`, `/about`, `/settings`, `/panel` and of the job card buttons |
| `bot/jobs.py` | plan of a job (limits, refusals), the two lanes, the worker (`checker/check_any.py` in a child process, its progress, timeouts) |
| `bot/config.py`, `bot/state.py` | the settings from the environment; what the bot keeps in memory |
| `bot/cards.py` | the job card message (posted once, then edited), its refresh, replies, permissions |
| `bot/submit.py`, `bot/manage.py` | Add to records, the Contributor role, announcements; `/history` and the management of entries |
| `bot/downloads.py` | links: allowed hosts, redirects, size cap, deadlines |
| `bot/ui.py` | the Discord cards (Components V2) as pure functions: job, queue, records, about, refusals, announcement, panel |
| `bot/records.py`, `bot/records.json` | the table; its seed for a new install |
| `bot/settings.py` | per-user settings (default visibility), `settings.json` next to the table |
| `bot/history.py` | the history of checks in SQLite (`history.sqlite` next to the table), retention, paging queries |
| `bot/magic.py` | the first-bytes check of uploaded files (clear refusals before any decoding) |
| `checker/check_any.py` | word or recipe to one JSON line (`ok`, n, length, SHA-256, footer comparison); `--shards K` passes; planner for shards, memory and time estimates |
| `checker/core.py` | stream bytes into the C checker (optionally one shard) and hash them on the way; progress callback per chunk |
| `checker/decode.py` | recipe to byte stream (validates, checks seed and plan hashes, runs the registry's generator) |
| `checker/spra.py` | SPRA words: reads the header, feeds the body to the C decoder |
| `checker/registry.py` | the fixed list of programs and accepted source hashes; `checker/limits.py` memory limit and low priority |
| `checker/build.py` | builds `gens/` into `bin/` (both at the repository root) |
| `checker/guard.py` | Windows only: runs a command in a job object with a memory limit (Linux uses `RLIMIT_AS`) |
| `gens/` | C sources: `gen14.c` (generator), `vfast.c`, `verify_stream.c` (checkers, both with `--shard i/k`), `spra.c` (SPRA decoder) |
| `action/` | GitHub Action: `action.yml` and `scan.py`; it builds and runs the repository's own `checker/` and `gens/` |
| `tools/` | `make_recipe.py` (writes recipes, imports Echols's `superperm-2lift/1` files as data), `seed_records.py` (builds the seed table), `preview_cards.py` (every card as plain text), `make_live_files.py` (files for docs/LIVE_TEST.md), `run_n14.py` (full n = 14 check of one recipe) |
| `tests/` | `test_check.py`, `test_bot.py`, `test_ui.py`, `test_spra.py`, `test_history.py` (offline), slow n = 13 shard test |
| `scripts/` | `run_bot.sh`, `run_bot.ps1` (start with `.env`), `bot_ctl.ps1` (background start and stop on Windows), `docker-entrypoint.sh` |
| `Dockerfile`, `compose.yaml`, `.env.example` | container setup |
| `docs/` | `DEPLOY.md` (host guide), `RECIPE.md` (recipe format), `DEVNOTES.md`, `LIVE_TEST.md`, `UI_DESIGN.md`; `CHANGELOG.md` is at the root |

## Cards, buttons and visibility

**What the user sees.** Messages are Components V2 cards (`discord.ui.LayoutView`: a coloured container with text,
separators and buttons; a card message cannot carry plain content or embeds). The colour and one emoji show the state:
queued grey, running blue, valid green, not valid red, refused or cancelled dark grey, new record gold. Numbers have
thousands separators, a SHA-256 is in code format, the submitter is a mention that shows the display name and never pings
(`allowed_mentions` is none on every message and edit).

* **One live card per job.** An accepted `/check` posts one normal channel message with the bot token (so it can be
  edited for hours; an interaction token lives 15 minutes). The card shows the queue position, then a progress bar,
  percent, pass i of k, letters read, ETA and elapsed time (refreshed every `progress_s` = 30 s, a message is never edited
  more often than every 5 s), then the result. The person who ran the command only gets a short private (ephemeral) reply
  with a link to the card. If the card message is deleted the bot posts a new one once; a failed edit is logged
  (`logging.warning`, logger `superperm`) and the check goes on.
* **Buttons on the card.** *Cancel* while it waits or runs (the submitter, or members with Manage Messages or
  Administrator). After a valid result *Add to records* (only the submitter; only if the word is shorter than the verified
  record of its n or the first verified entry; it does what `submit: true` does: table, Contributor role, announcement; the
  button then reads "Added" and is disabled) and after any result *Details* (a private message with every key of the
  result and the full SHA-256). The buttons are `DynamicItem`s with the job number in their `custom_id` (`sp:cancel:12`),
  so after a restart of the bot an old button answers "Job #12 is gone" instead of failing.
* **Visibility and privacy.** *Public* (the default for a user who never chose): the card is posted in the channel with the file
  name, length, SHA-256, result and who sent it; the file itself is never reposted. *Private* (`/check visibility: private`, or
  the user's saved default from `/settings`): the full card goes to the submitter by direct message (edited in place like a
  public card) and the channel gets only a small grey note, "Private check by @user, n = 13, queued / running / finished",
  edited at the state changes, with no file name, length, SHA-256, verdict or buttons. The bot opens the DM before it queues
  the job; if DMs are closed it refuses ("I cannot send you a direct message. Allow direct messages from server members
  (Server menu > Privacy settings), or run the check as public.") and queues nothing. *No trace at all*: run `/check` in a
  direct message with the bot (the commands `/check`, `/queue`, `/cancel`, `/records`, `/about`, `/settings` work there;
  `/panel` needs a server). The card is that conversation; limits and lanes apply per user as usual. In a DM the Contributor
  role is given only if the bot finds the user on the server of `SUPERPERM_CHANNEL` (`fetch_member`); otherwise the card says
  "Contributor role not given: use the server".
  *Who sees what in `/queue`:* everybody else sees a private check only as "private check, n = 13". The submitter sees it in
  full, with a link to the DM card. Staff, i.e. members whose permissions (as Discord sends them with the interaction) include
  **Manage Messages** (or Administrator), see that it exists, the user (a mention, no ping), n, size and progress, and get a
  *Cancel* button per private job, but not the file name, length, SHA-256 or result. Administrators (**Administrator**
  permission) also see the file name. There are no role ids in the configuration: "moderators are whoever has Manage
  Messages". If `SUPERPERM_AUDIT_CHANNEL` is set, every private check (also those run in a DM) is mirrored there as a card
  that is edited at the start and at the result, with a *Details* button that answers only Administrators; who can read the
  channel is up to the server's channel permissions. The host of the bot and Discord can see all checks.
  *Add to records* on a private result asks first ("This makes the result public: entry in the table and an announcement in
  #records", *Publish* / *Keep private*); only *Publish* does it, and the announcement has no link to the private card.
  Log lines never contain a word; file names of private jobs are logged only at debug level.
* **`/settings`.** A private card with a select "Default visibility of my checks" (Public / Private); the choice is saved in
  `settings.json` and confirmed.
* **Form.** `/check` without options, or the button *Check a word* on a panel message (`/panel`, Manage Server only, posts
  it in the current channel; the button is persistent), opens a form with a file upload, a text field for a public link
  (exactly one of the two) and a *Visibility* select (preset to the user's default).
* **Refusals** before a job exists (wrong ending, host, n above the limit, too long, queue full, one job per lane) are
  private grey cards with a title, one line of reason and one line of what to do.

## Commands

* `/check [attachment|url] [submit] [visibility]`: attachment up to 64 MB with a name ending `.splan.json.xz`, `.splan.json`,
  `.spra.xz`, `.spra`, `.txt.xz`, `.txt` (Discord's own upload limit applies first), or a URL on `github.com`,
  `raw.githubusercontent.com`, `gitlab.com` (https, no login, no port; `/blob/` pages become raw links; redirects only
  to those hosts and GitHub's release-asset hosts; 64 MB cap, 20 s per read, 120 s in all). With `submit: true` a
  valid word is entered into the table; a new record is announced in `SUPERPERM_CHANNEL` (a gold card with a button to
  the job card) and can give the Contributor role. Letters are counted without white space (a 153-letter `.txt` is 153
  letters, not 154 bytes); a stored submitter is the display name, with the Discord user id kept next to it
  (`submitter_id`). If `SUPERPERM_SUBMIT_ROLE` is set, `submit` and *Add to records* work only for members with that role
  (also for administrators); the check itself runs for everyone and the card says why nothing was added.
* `/queue`: both lanes, the running job with a mini progress bar and a link to its card, the waiting jobs with positions
  (visible only to you).
* `/cancel job_id`: cancel your own waiting or running job (the number is in the card title, "Check #12"). Users with
  Manage Messages or Administrator may cancel any job. A running job is killed with its child processes and the card says so.
* `/history [user]`: a private card with your own checks, newest first, 10 per page (*Previous* / *Next*): time as a Discord
  timestamp (shown in your own time zone), n, file name, result emoji, length and job id. A select opens one entry (all
  fields, the full SHA-256) with *Delete from history* and *Rename* (a form with one text field, 1 to 80 characters, cleaned
  like a file name; it changes only the shown name). Moderators (Manage Messages) can use `/history user:@someone`: private
  and DM checks of others are masked as in `/queue` (no file name, length, SHA-256, verdict), administrators see them in full;
  moderators and administrators may delete any entry, only the owner and administrators rename. Every delete or rename by
  somebody other than the owner is written to the audit channel ("@mod deleted history entry #12 of @user"). The history
  keeps rows for `SUPERPERM_HISTORY_DAYS` days and never the file: the temporary folder of a check is deleted when the
  check ends.
* `/records` management. The per-n details card (the select under `/records`) gets a select *Manage entry* with the entries
  that the viewer may change. The current record of an n (its best verified entry) can be removed only by an administrator,
  after a confirmation card, and the removal is written to the audit channel. Any other entry can be removed by its
  submitter, a moderator or an administrator (a confirmation card too); its shown name (`label`) can be renamed by its
  submitter or an administrator. Ownership is the stored `submitter_id`; entries without one (the seed claims) can be
  changed only by administrators. New entries get a `time` (unix seconds, UTC) next to the old `date`; entries and history
  rows show it as a Discord timestamp, old entries show their date.
* `/records`: a card with an aligned monospace block (n, length, holder, status; "verified" = checked by this bot, "claim" =
  published, not checked here) and a select "Details for n = ..." that answers privately with the entries of that n.
  `/about`: a card with limits, lanes, permissions and links. `/panel`: see above.

## Permissions

Create the application and invite the bot with the `bot` and `applications.commands` scopes. No privileged intents
(the client uses `Intents.default()`; the tests check that members, presences and message content are off).

**Permissions the bot needs** (invite link or channel overrides):

* Send Messages, Embed Links, Read Message History. Together they are the integer 83,968 (2,048 + 16,384 + 65,536).
  The thread permissions of the first test version are not needed any more: there are no threads.
* Manage Roles, only if `SUPERPERM_CONTRIBUTOR_ROLE` is set, and the bot's own role must be above the Contributor role
  in the server's role list. With it the integer is 268,519,424 (83,968 + 268,435,456; the arithmetic is in
  docs/DEPLOY.md). If this is wrong the bot says so on the card and carries on. It never removes a role.

## Limits and queue

Limits and queue (in `bot/jobs.py` and `bot/commands.py`, tested in `tests/test_bot.py`):

* n above `SUPERPERM_MAX_N` is refused before any checking. A job whose estimated time (letters, from the recipe header
  or the `.txt` size, for `.txt.xz` from n; times the number of passes, at `SUPERPERM_MBPS`) is above
  `SUPERPERM_MAX_HOURS` is refused. A pass is killed at 1.5 x its estimate plus 30 s of start-up, a whole job at
  `SUPERPERM_MAX_HOURS`; the card reports the timeout. An xz word that is much longer than the formula for n gets
  stopped this way too.
* Two lanes with one worker each: **fast** (n <= 12) and **slow** (n = 13, 14), so a 30-minute n = 14 does not block small
  checks. Per user at most one job waiting or running in each lane; `SUPERPERM_QUEUE` waiting per lane.
* **The queue is in memory.** A restart of the bot drops waiting and running jobs (their history rows become "error") and users send them again (`/about` says so); job numbers go on from the last number in the history.
* Worker: `checker/check_any.py PATH --shards K --progress` in a child process under a memory limit (Windows: job object
  through `checker/guard.py` or `SUPERPERM_GUARD`; Linux: `RLIMIT_AS` and nice 19). The bot
  reads its `#P` progress lines; cancel and timeouts kill the guard process, whose job object closes and takes the
  checker and generator with it (checked on a real n = 12 check: killed after 4 s, no `verify_stream` left).
* The card is sent with `channel.send` (the bot token), not through the interaction's own follow-up token, which Discord
  expires after 15 minutes: checks last hours.
* The handlers take any object with `response.defer`, `response.send_message`, `response.send_modal`, `followup.send`, `user`,
  `channel`, `guild`; `tests/test_bot.py` calls them with fakes that record every message and edit. All cards come from the
  pure functions of `bot/ui.py`; `tests/test_ui.py` builds each one and checks text, colour, buttons and Discord's limits (40
  components, 4,000 characters per message), `tools/preview_cards.py` prints them.

## Sharded checking (memory for time)

The checkers have `--shard i/k`: only the permutations whose rank (Lehmer code, symbols in the order of the alphabet)
lies in `[floor(i*n!/k), floor((i+1)*n!/k))` are tracked, in a bit table of that range (memory about n!/(8k)). A pass
reports `missing` for its range. `check_any.py --shards K` runs K passes (a recipe is expanded again for each pass; the
generators are deterministic) and calls the word valid iff every pass is ok **and** all passes saw the same length and the
same SHA-256 (a mismatch is reported as invalid). The planner (`check_any.plan_shards`) takes the smallest K with
`n!/(8K) + 1.0 GB (+ 0.6 GB for a recipe's generator) <= SUPERPERM_MEM_GB` and refuses if K would exceed
`SUPERPERM_MAX_SHARDS`. K = 1 is the old single pass (same output, same exit code; verified byte for byte against the
previous binaries on a small word). A pass over a range costs the same reading time as a full pass, so time is K times
longer.

## Resources per n

Memory is per pass: bit table n!/(8k) plus 1.0 GB (checker buffers, Python) plus 0.6 GB for a recipe's generator. Time
is k x the time of one pass; the bot estimates one pass at `SUPERPERM_MBPS` (30 M letters/s, from the n = 14 checks on a
loaded desktop; an idle one is about twice as fast).

| n | letters | k = 1 | k = 2 | k = 4 | k = 8 |
|---:|---:|---|---|---|---|
| 10 | 4.0 M | 1 MB table, 1 s | | | |
| 11 | 43.9 M | 5 MB, 2 s | | | |
| 12 | 0.52 G | 60 MB, 25 s | | | |
| 13 | 6.7 G | 1.7 GB, 4 min | 1.4 GB, 8 min | 1.2 GB, 15 min | 1.1 GB, 30 min |
| 14 (recipe) | 93.9 G | 11.8 GB, 52 min | 6.7 GB, 1.7 h | 4.1 GB, 3.5 h | 2.9 GB, 7 h: over the 6 h default, refused |

With the defaults (`SUPERPERM_MEM_GB` 14) every n <= 14 runs in one pass; k > 1 appears when `SUPERPERM_MEM_GB` is smaller,
e.g. 8 GB gives k = 2 for n = 14, 4 GB gives k = 4 (hours of work in the slow lane). n = 14 measured unsharded:
about 57 min and 11.2 GB peak (guard). Sharded runs were measured only at n <= 11 here, n = 13 in the slow test (docs/DEVNOTES.md).

A recipe for n = 14 is 0.5 to 0.6 MB; the plain word would be 94 GB (xz is not practical). Threads: a recipe check
uses three processes of one thread each (generator, Python, checker); words with n <= 12 use `verify_stream` with
`--threads`.

## Security model

The parsers of user data were fuzzed before the release: the SPRA decoder (84,000 files, compared with Theo H.'s
decoder), the recipe generator gen14 (25,000 cases under ASan and UBSan), the recipe loader (21,500 recipes) and both
checkers (2,400 streams). The one memory-safety bug found (a selection row with a character outside 0-9 in gen14) is
fixed and has a regression test.

* **Magic check first.** Before any decoding the bot looks at the first bytes of an upload and refuses with a clear message:
  `.xz` files must start with `FD 37 7A 58 5A 00`; `.spra` with "SPRA" (also after decompressing a `.spra.xz`);
  `.splan.json(.xz)` must be UTF-8 JSON, an object with a `format` field; `.txt(.xz)` must be printable ASCII plus white
  space in its first MiB. This is for clear messages, **not the security boundary**: parsing is (the decoders and checkers
  parse strictly and run with limits).
* Nothing a user sends is executed. Each file goes into a fresh temporary folder under a fixed name (`upload` + extension)
  and the folder is deleted after the check. Sizes and lengths are capped (upload 64 MB, recipe JSON 64 MiB, word 2*10^11
  letters), download hosts are limited (github.com, raw.githubusercontent.com, gitlab.com, https only), the checkers run with
  a memory limit and at low priority, a pass and a whole job have time limits. The strongest isolation is the Docker image
  (read-only root, tmpfs, no capabilities, no new privileges, memory and CPU limits; see `compose.yaml`).
* No submitted code is ever run. A file is data: a word, or a recipe whose fields are an alphabet, a seed text and a
  plan text. The program that expands a recipe is chosen by id from `registry.py`, built from C sources of this
  repository, and the recipe must name the SHA-256 of that source (otherwise it is refused). A binary whose build
  record does not name an accepted source is not started.
* Programs are started with a fixed argument list, no shell. The alphabet is checked (n distinct printable ASCII
  characters); seed and plan go through files in a private temporary directory that is removed afterwards.
* File references exist only in a recipe directory and only inside it; hash of every part is checked before use.
  Limits: decompressed JSON 64 MiB, seed 16 MiB, plan 64 MiB, word 2*10^11 letters.
* Downloads: allowed hosts only (also after each redirect), https, size and time caps, nothing is executed.
* The generator is C code that parses the seed and the plan; a malformed plan can crash it or make it run long, so it
  runs in the worker with the memory limit and the time limit. It is our own code, not audited against hostile input.
  For a public bot run the worker in a container or a throwaway VM.
* The bot's messages never ping anyone (`allowed_mentions` none on every send, response and edit; the fakes of the tests
  refuse a call without it); file names are reduced to `[A-Za-z0-9._-]`; display names are cleaned of control characters
  and backticks and shortened.
* The bot never removes a role (no `remove_roles` in the code; a test checks), asks for no privileged intent, and a failed
  role grant or card is reported, not raised; every swallowed exception is logged (never with the token).
* The token only comes from `DISCORD_TOKEN`; a test fails if a token-like string is in the code.
* `records.json` is written atomically, only for verified results, one writer (a lock in the bot process).

## SPRA words

`gens/spra.c` decodes SPRA 0.1 and 0.2 (four- and six-bit fields, repeat markers), written from the format description
in docs/reduced_binary.md of Tholsh/SPRA, not from its code; `spra.py` reads the header (n, length, labels) and feeds the
file into the decoder, decompressing a `.spra.xz` on the way. The decoder refuses a file that breaks any rule of the
format (bad header, a clean step after a repeat, a copy that makes a clean window, a marker first or past the stated
count, nonzero padding, extra bytes) instead of decoding it differently. SPRA has no checksum of its own; the bot
reports the SHA-256 of the decoded letters, and the length must equal the header's. `tests/test_spra.py` checks the
examples of the description byte for byte, round trips through an independent encoder, refusals, and (with
`SUPERPERM_SPRA_CORPUS`) a whole corpus.

## Test data

Some tests need data files that are not in this repository. They skip themselves with a message when the file is
missing. Set these variables to run them:

| variable | meaning |
|---|---|
| `SUPERPERM_WORDS` | folder with `superpermutation-10-4034855.txt.xz`, `superpermutation-11-43930578.txt.xz` (and `-13-6747802393.txt.xz` for the slow test); default `testdata/words`. The words are in the repository above |
| `SUPERPERM_SPRA_CORPUS` | folder with `.spra.xz` words (e.g. `words/` of Tholsh/SPRA); every word with n <= 12 is checked |
| `SUPERPERM_TESTDATA` | folder with `n14_echols_93904936032.splan.json.xz` (default `testdata`) |
| `SUPERPERM_N14_RECIPE`, `SUPERPERM_N14_LENGTH` | an n = 14 recipe of your own and the length it states (the planner and runner tests) |
| `SUPERPERM_ECHOLS_SPC` | path of Echols's `.spc.xz` file, for the importer test |
| `SUPERPERM_WORD8`, `SUPERPERM_WORD9` | optional extra words of n = 8 and 9 for the shard tests |
| `SUPERPERM_SLOW=1` | also run the n = 13 test |

Without any of them the tests that need no data still run (planner, limits, handlers with fakes, records, sharding on
generated words).

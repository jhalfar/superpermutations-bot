# Changelog

## v1.0 (2026-10-24), first public release, n <= 14

* Recipe format `splan/1` (docs/RECIPE.md) and its decoder; `checker/check_any.py` for words and recipes; the table of
  records; the GitHub Action in `action/`, which builds and runs the repository's own `checker/` and `gens/`.
* Layout: `bot/` (the Discord bot in small modules, `discord_bot.py` is the entry point; the table and its seed),
  `checker/` (checker, decoders, build, registry), `gens/` (C sources), `tools/`, `scripts/`, `docs/`. The seed table
  has the public records of 2026-10-24.
* Details: a short legend of the fields under the table; Details and record look-ups remove themselves after 10
  minutes (ephemeral cards, so a user's view does not fill up).
* `checker/guard.py` (Windows memory limit) prints its summary on stderr; on stdout it joined the word stream of a
  generator it wrapped. Tie names `superpermutation-N-L-HASH` are checked against their n and L by the Action.
* `splan/1` is fixed (docs/RECIPE.md "Stability"). Job numbers go on after a restart (from the history). Optional
  `SUPERPERM_SUBMIT_ROLE`: only members with that role may add words to the table. README shortened; the details moved
  to docs/REFERENCE.md.
* Discord bot with `/check`, `/records`, `/queue`, `/cancel`, `/about`, `/panel`. Replies are Components V2 cards
  (`discord.ui.LayoutView`, `bot/ui.py`): one live card per accepted check, sent with the bot token and edited in place (queue
  position, progress bar with percent, pass, ETA and elapsed time every 30 s but never more than one edit per 5 s, then
  the result), coloured by state with one emoji. Buttons on the card: Cancel (submitter, Manage Messages or Administrator),
  Add to records (submitter, only if the word would enter the table; same as `submit: true`), Details (every key of the
  result, full SHA-256). Buttons survive a restart of the bot (`DynamicItem`; an old card answers "Job #N is gone").
  `/records` is a card with an aligned monospace block and a select for the details of one n; `/queue` and `/about` are
  cards; refusals before a job exists are short private cards (title, reason, what to do); a new record is announced with a
  gold card and a button to the job card. `/check` without options, and the persistent button of `/panel` (Manage Server),
  open a form with a file upload or a link. No threads. A message that cannot be edited or posted is logged
  (`logging.warning`), and a deleted card is posted again once.
* Visibility. Public checks (default) post the card in the channel. Private checks (`/check visibility: private`, the
  `/settings` default, the form's Visibility select) send the card by direct message and leave only a grey note in the
  channel (who, n, queued / running / finished; no file name, length, SHA-256 or verdict). If DMs are closed the check is
  refused and not queued. Run `/check` in a direct message with the bot for no trace on the server (commands work in DMs
  except `/panel`). `/queue` shows other people's private checks only as "private check, n = 13"; Manage Messages sees the
  user, n, size and progress and can cancel from the queue; Administrator also sees the file name. Optional audit channel
  (`SUPERPERM_AUDIT_CHANNEL`) with a copy of every private check. *Add to records* on a private result asks for
  confirmation (Publish / Keep private) before anything becomes public. Settings in `settings.json`
  (`SUPERPERM_SETTINGS`). Logs never contain a word; file names of private jobs only at debug level.
* History of checks in SQLite (`SUPERPERM_HISTORY`, `SUPERPERM_HISTORY_DAYS`, default 180): one row per accepted job (time,
  user, visibility, file name, source, kind, n, length, SHA-256, result, seconds, added); never the word or the file.
  `/history` shows your own checks, 10 per page, with delete and rename; moderators (Manage Messages) can look at another
  user (private checks masked), administrators see everything; deletes and renames by others go to the audit channel.
* Records table management: the record of an n can be removed only by an administrator (confirmation, audit line); other
  entries by their submitter, moderators or administrators; rename by the submitter or an administrator. New entries carry
  `time` and `label`; times are shown as Discord timestamps (old entries keep their date).
* Magic check of uploads before any decoding (xz signature, SPRA header, UTF-8 JSON recipe with a format field, printable
  ASCII text) with clear refusal cards; README security section expanded.
* Small fixes from the first live test: letters are counted instead of bytes (a 153-letter `.txt` was announced as 154),
  "under a second" instead of "0 s", the display name is stored and shown (the user id is kept as `submitter_id`),
  permissions are now Send Messages, Embed Links, Read Message History (83,968) and Manage Roles for the Contributor role
  (268,519,424); the thread permissions are gone.
* Contributor role (`SUPERPERM_CONTRIBUTOR_ROLE`) for a new record or the first verified entry of an n, with `submit` or the
  Add to records button.
* Sharded checking: `--shard i/k` in both checkers, `check_any.py --shards K`, a planner that takes the smallest K that
  fits `SUPERPERM_MEM_GB`. The passes must agree on length and SHA-256.
* Limits and queue: `SUPERPERM_MAX_N`, time estimate and refusal, a kill per pass and per job, two lanes (fast n <= 12,
  slow n = 13, 14), one job per user and lane.
* SPRA words (`.spra`, `.spra.xz`, the format of Theo H.): own streaming decoder `gens/spra.c`.
* A SPRA file or recipe whose header states more than n x n! letters is refused before any work (a 24-byte SPRA file
  can claim 2^64 letters and would decode for ever).
* Fuzzing of the parsers of user data (2026-10-21): the SPRA decoder (84,000 cases, his and ours agree), gen14
  (25,000 cases with ASan and UBSan), decode.py (21,500 recipes), vfast and verify_stream (2,400 streams). Fixed: gen14
  read a selection row with a character outside 0-9 (an out-of-bounds read of the alphabet table; reachable from a
  recipe), a recipe with a non-string generator id or a lone surrogate in its text gave a traceback instead of a
  refusal; check_any.py now turns any remaining exception of a hostile file into a refusal. Regression tests in
  tests/test_check.py (TestFuzzRegressions).
* Docker image and compose file, deploy guide, CI; tests that skip when their data is missing; `tests/test_ui.py` builds
  every card and `tools/preview_cards.py` prints them without Discord.

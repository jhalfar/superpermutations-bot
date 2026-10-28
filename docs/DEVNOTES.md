# superpermutations-bot v1.0: development notes

How the parts fit together, what the tests cover and what has not been tested yet. The UI design that this version
implements is in docs/UI_DESIGN.md.

## Design

1. **One live card per job.** `handle_check` downloads or saves the file, plans it (`plan_job`: kind, n, letters, shards,
   time estimate, refusals), admits it to its lane and posts one card with `channel.send` (the bot token: the message can be
   edited for hours, an interaction token lives 15 minutes). The user gets a short ephemeral follow-up with a link to the
   card. `Card` (bot/cards.py) owns the message: a refresh from `card_loop` (every `progress_s`, default 30 s) is skipped
   when the card did not change or the last edit is younger than `min_edit_s` (5 s); a state change (running, result)
   waits its turn. The view is made by a function that runs when the edit is due, so a late edit shows the newest state.
   If the message is gone (`discord.NotFound`) a new card is posted once; any other failure is logged and the job goes on.
   Every swallowed exception is logged with `logging.warning` (logger `superperm`; never the token). There is no thread
   code: the first live test showed that `create_thread` on the interaction's webhook message has no bot HTTP state and
   failed silently.
2. **Visibility** (the last design step). `handle_check(visibility=...)`: `public` (default for a user who never chose;
   `settings.py` keeps the per-user choice in `settings.json`, one field, atomic write) posts the card in the channel.
   `private` sends the full card to the user by DM (`user.create_dm()`, `dm.send(view=...)`, then edits like any card) and
   posts a trace card (`ui.trace_card`: "Private check by @user, n = 13, queued / running / finished", no progress, no
   buttons, edited by `side_cards` at start and result only). The DM is opened and the card sent *before* the job is
   admitted (`Scheduler.check_admit` first, so the limits still refuse without a DM); `discord.HTTPException` (Forbidden:
   DMs closed) gives a refusal card and nothing is queued. A check run in a DM with the bot (`interaction.guild is None`;
   `allowed_contexts(guilds=True, dms=True, private_channels=False)` on all commands except `/panel`, which is
   `guild_only`) is private by nature: the card is `interaction.channel` itself, no trace. `SUPERPERM_AUDIT_CHANNEL` adds a
   copy (`job_card(audit=True)`: one button, Details `sp:audit:N`, answers only Administrators), posted at accept and edited
   by `side_cards`. `/queue` (`ui.queue_card(viewer_id, staff, admin)`): others see "private check, n = 13", staff see the
   user, n, size and progress and get Cancel buttons (ActionRows of 5, to stay under 40 components), admins also the file
   name, the submitter everything plus the link to the DM card. Staff = Manage Messages or Administrator in
   `user.guild_permissions` (as Discord sends it with the interaction); admin = Administrator; there are no role ids.
   Details of a private card answer only the submitter. *Add to records* on a private card asks first
   (`confirm_publish_card`, buttons `sp:publish:N` / `sp:keep:N`); the announcement has no link button. The role of a private
   check goes to the member and server saved at send time (`JobCtx.member/guild`); for a DM check `State.find_member`
   (client: `fetch_member` in the guild of `SUPERPERM_CHANNEL`) is asked, and the card says "Contributor role not given: use
   the server" if it fails. Logs never hold a word; the file name of a private job is logged at debug level only.
3. **History** (`history.py`, SQLite, one short connection per call, a lock): `hist_add` after a job is accepted (verdict
   `pending`), `hist_finish` when the result is known (verdict from `ui.verdict_of`), `mark_added` on Add to records;
   failures are logged and never stop a check. Retention: `purge` at start and once a day (`history_loop`); rows still
   `pending` from before a restart become `error`. `/history` (`handle_history`, `hist_view`): 10 rows per page, paging with
   `XButton` `hp` and `Interaction.response.edit_message`; masking and rights are re-derived from the interaction at every
   press (`viewer_info` = id, staff, admin; `hist_masked`, `hist_rights`): the owner and admins see everything, staff see
   other people's private and DM rows masked (the masked entry card is grey with a lock so the colour does not give the
   verdict away), owner / staff / admin delete, owner / admin rename (`RenameModal`, 1 to 80 characters, `clean_name`).
   `audit_say` writes the audit line when someone other than the owner deletes or renames. The new buttons and selects are
   `XButton` (`sp:x:KIND:ARG`) and `XSelect` (`sp:y:KIND:ARG`); they need different prefixes because discord.py runs every
   dynamic item whose template matches the custom_id, whatever the component type (the first version had one prefix and a
   button press also ran the select class).
   **Records management**: `records.ref/find/permissions/remove/set_label/when`; `records.permissions` is the one place with the
   rules (record: admin only; other entries: submitter, staff, admin, but only with a `submitter_id`; rename: submitter or
   admin); `ui.record_details_card(viewer=...)` offers the `rm` select, the manage card has Delete (confirmation card
   `ry`/`rn`) and Rename. Entries get `time` and `label`; `records.when` shows a Discord timestamp or the old date.
   **Magic check** (`magic.py`) runs in `handle_check` before `plan_job`.
3b. **Cards are pure functions** (`ui.py`): `job_card(job, result, ...)`, `refusal_card`, `notice_card`, `accepted_card`,
   `details_card`, `records_card`, `record_details_card`, `queue_card`, `about_card`, `announcement_card`, `panel_card`.
   Each returns a `discord.ui.LayoutView` with one `Container` (accent colour from the table `COLOURS`, one emoji from
   `EMOJI`). `check_limits(view)` re-checks Discord's limits (40 components, 4,000 characters of text per message; a
   LayoutView message cannot carry content or embeds, and `Section` needs exactly one accessory: it is used in `/queue` for
   the link button to the running job's card). `plain(view)` renders a card as the text a user would read.
4. **Buttons are `DynamicItem`s** (`JobButton`, `PanelButton`, `RecordsSelect`, `SettingsSelect`): the `custom_id` carries the job number
   (`sp:cancel:12`, `sp:add:12`, `sp:details:12`, `sp:publish:12`, `sp:keep:12`, `sp:audit:12`), `Client.add_dynamic_items` registers the three classes in `setup_hook`,
   and discord.py rebuilds the item from the message when it is pressed. So the buttons need no per-message view in memory,
   and after a restart of the bot an old button reaches `handle_job_button`, which rebuilds the job from its history row
   (`restore_ctx`: n, length, SHA-256, verdict, submitter, added; Details shows `history_card`, Add to records works as
   before, Cancel says the job is not running) and answers "Job #12 is gone" only when the history has no row (the job
   table is in memory only). `JobCtx` (per job, newest `card_keep` = 300) holds what the buttons need: the card, the
   result, the source, the submitter. Cancel: the submitter or Manage Messages or Administrator. Add to records: only the
   submitter, only a valid word that would enter the table (`records.would_enter`), the same code as `submit: true`
   (`do_submit`: table, Contributor role, announcement card with a link button to the job card).
5. **Form.** `/check` without options (and the persistent *Check a word* button of the `/panel` message) sends a `Modal`
   with a `Label` + `FileUpload`, a `Label` + `TextInput` and a `Label` + `Select` (Visibility, preset to the default); `on_submit` calls `handle_check(form=False)`, which wants
   exactly one of the two. `/panel` is guild-only, `default_permissions(manage_guild=True)`, and checks the permission again.
6. **Contributor role** (`SUPERPERM_CONTRIBUTOR_ROLE`): `member.add_roles` with a reason, for a new record or the first
   verified entry of an n, with `submit` or the *Add to records* button. A failure is reported on the card; roles are never
   removed. The submitter is stored as the display name together with the user id (`submitter_id`).
7. **Sharding**: `--shard i/k` in `vfast.c` and `verify_stream.c` (a bit table for one range of ranks; a first-letter shortcut
   skips the rank computation for windows outside the range). `check_any.py --shards K` runs K passes and compares length
   and SHA-256 (a mismatch means invalid). The planner `plan_shards` takes the smallest K that fits `SUPERPERM_MEM_GB` and
   refuses above `SUPERPERM_MAX_SHARDS`. The C sources are pinned by SHA-256 in `checker/registry.py`; the Action in
   `action/` builds from the same `checker/` and `gens/`, so there are no copies to keep in step. Progress is a byte count per 4 MiB chunk in `core.check_stream`
   (callback); `check_any.py --progress` prints `#P` lines (one per second) and the bot's supervisor reads them.
   `check_any.count_letters` gives the letters of a plain `.txt` (white space is not counted), so the plan no longer says
   154 for a 153-letter file.
8. **Limits and queue**: `SUPERPERM_MAX_N`, a time estimate and refusal (`SUPERPERM_MBPS`, `SUPERPERM_MAX_HOURS`), a kill per
   pass at 1.5 x the estimate (+ 30 s start-up) and at MAX_HOURS in all. Two lanes (fast n <= 12, slow n = 13, 14) with one
   worker each, one job per user and lane, `SUPERPERM_QUEUE` waiting per lane, `/queue`, `/cancel` (own job; Manage
   Messages or Administrator: any job). Cancel and timeouts kill the whole process tree (Windows: the job object of
   `guard.py`; Linux: the process group). The state is in memory only.
9. **Security**: no privileged intents; nothing a user sends is run as code; `allowed_mentions` none on every send, response
   and edit (the test fakes reject a call without it); file names reduced to `[A-Za-z0-9._-]`, display names cleaned. The
   permissions (Send Messages, Embed Links, Read Message History, Manage Roles only with the role) are listed in docs/REFERENCE.md,
   printed by `/about` and summed in docs/DEPLOY.md; `ui.permission_value` computes the integer and a test compares it with
   `discord.Permissions`.

## Tests (offline: no token, no network)

* `tests/test_check.py`: sharding at n = 6..11 for k = 1..7 (valid words: ok, same length and SHA in every pass;
  corrupted words with a deleted, changed or missing letter: invalid for every k, and for n <= 8 the per-shard `missing`
  equals a brute-force count of the missing ranks inside each range), boundary ranks of every shard, k = n! (one
  permutation per shard) with both checkers, bad `--shard` arguments exit 2, unsharded output unchanged, the combine
  rule, the planner, recipe prefixes.
* `tests/test_spra.py`: the SPRA decoder against an independent encoder, refusals, the bot's planner on SPRA files.
* `tests/test_bot.py`: fakes for interaction, response, followup, channel, message, member and guild; the fakes reject
  content next to a LayoutView, a missing `allowed_mentions`, and a card over a limit. Covered: the one-card flow, the
  ephemeral replies, refusals, the form (`CheckModal` with a fake attachment, a link, none, both), `/panel`, the buttons
  (Cancel with the permission check, Add to records, Details, stale job), the throttled editor with a fake clock, message
  deleted, edit and post failures (logged), lanes, per-user limits, full queue, MAX_N, time and memory refusals, roles, the
  real supervisor with a stub child (progress, pass timeout, total timeout, cancel: the child is killed), the real runner on
  the n = 10 word with 3 shards, env variables, the command list, no thread code.
* `tests/test_bot.py` also has `TestPrivate`: default public for a new user, the settings file (format, one field, atomic),
  the `visibility` option over the saved default, the form choice, the private flow (DM card, note without secrets, edits
  at state changes only), closed DMs (Forbidden and HTTPException: refusal, nothing queued, nothing posted, no file name in
  the log), `/queue` for another user / the submitter / staff / admin, staff Cancel from the queue, Details refused to
  staff, the audit copy (post, final edit, Details only for Administrators), Add to records with Publish / Keep private,
  checks run in a DM (no guild, audit copy only, limits, `/queue`, `/about`, `/records`, `/settings` work, `/panel`
  refused), the role through `fetch_member` found / not found.
* `tests/test_ui.py`: every card state (queued, running, valid, not valid, refused, cancelled, stopped, records with
  verified and claim rows, queue, about, announcement, panel, every refusal): texts, accent colours, buttons per state,
  component count and text length, formatters; and the wiring through discord.py's own `ViewStore`: a card is serialised,
  rebuilt with `LayoutView.from_message` and the right handler runs for each button and for the select.
* `tools/preview_cards.py`: prints every card as indented plain text.
* Slow: `SUPERPERM_SLOW=1 python -I tests/test_check.py TestSlowN13`, the n = 13 word, k = 1 against k = 2.

All files pass on Windows (Python 3.14, discord.py 2.7.1); earlier versions also passed on Linux (Python 3.12) and inside the
Docker image with the hardening of `compose.yaml` (read-only root, tmpfs /tmp and /work, cap_drop ALL, no-new-privileges,
16 GB, 4 CPUs, init). In the container the n = 10 word checks valid and the entrypoint refuses to start without
`DISCORD_TOKEN`. An n = 14 recipe was checked in full unsharded (about 57 min, 11.2 GB peak).

## Not tested yet (needs a live gateway or a real server)

Everything on real Discord: that a Container with a Section, Separator and ActionRow renders as in the preview (the
preview is plain text, not the client), accent colours, the progress bar characters in the client font, the aligned block
of `/records` in the client's monospace font, `channel.send(view=...)` and `message.edit(view=...)` on a real channel and
the rate of edits, the ephemeral replies with a LayoutView, the Modal with `FileUpload` (a feature of discord.py 2.7;
Discord must have it enabled for the application) and the attachment it returns (`FileUpload.values`), `Attachment.save`
for such an attachment, the persistent buttons after a real restart, the `/panel` permission gate and the command sync,
`member.add_roles` with the real role order, `guild_permissions` of a real member for Cancel, the link buttons to
`message.jump_url`, the announcement in the records channel. Sharded passes of n = 14 and the time estimate against a real
sharded run. The 1.5 x estimate kill on a heavily loaded machine (the 30 M letters/s default comes from a loaded desktop;
an idle machine is faster, so the limit is loose there; if a very loaded one trips it, lower `SUPERPERM_MBPS`).

## Not done

Persistence of the queue (a restart drops waiting and running checks; finished ones keep working from the history);
the queue position of a waiting card is refreshed only every `progress_s`; authentication of who may `submit`; n > 14;
generators for n = 12, 13 recipes.

Also untested live (history and records management): the `XSelect`/`XButton` flows on real ephemeral messages, paging with
`edit_message` on a LayoutView, `discord.User` as the `/history user` option in a DM and in a server, Discord timestamps in the
clients, the rename form, the SQLite file on the Docker volume, the audit lines.

Also untested live: `user.create_dm()` and DM edits on a real account, Forbidden when DMs are closed, commands in a DM with the
bot (`allowed_contexts`; the command sync must have run), `interaction.guild` being None there, `fetch_member` in the guild of
`SUPERPERM_CHANNEL` (needs the bot in that server), the audit channel permissions, `Select` inside a modal `Label` and its
default option, staff/admin permissions as Discord sends them.

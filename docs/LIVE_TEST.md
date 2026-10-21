# Live test on Discord

The unit tests use fakes for Discord and `tools/preview_cards.py` prints the cards as plain text. This list checks the
parts that only a real server shows: how the cards look, the buttons, the form, roles, permissions, edits, the queue. Use a
test server or a hidden channel, and a copy of the table (`SUPERPERM_RECORDS=/path/to/records_test.json`) so that `submit`
does not change your real one.

Setup: docs/DEPLOY.md (application, invite link, `.env`); then `./scripts/run_bot.sh` or `scripts\run_bot.ps1`. Test files:
`python -I tools/make_live_files.py` writes them to `live_test_files/`. For the slow lane add any public n = 13 word
as `.spra.xz` (for example from the words/ folder of github.com/Tholsh/SPRA) and, if you want, an n = 14 recipe.
Watch the console of the bot too: every failed message or edit is logged there as a warning.

| # | command | expected |
|---|---|---|
| 1 | `/about`, `/records` | cards: description, limits, permissions, link buttons; the table as an aligned block and the select |
| 2 | `/records`, pick n = 13 in the select | a private card with the entries of that n |
| 3 | `/check` with `01_ok_n5.txt` | you get a short private reply with a link; ONE green card in the channel: Valid, n = 5, 153 letters, buttons Add to records and Details |
| 4 | `02_ok_n7.txt.xz` | green card, n = 7, 5,913 |
| 5 | `03_ok_n8.spra.xz` | green card, n = 8, 46,233, line "SPRA 0.2, six-bit fields" |
| 6 | `06_ok_n6_crlf_lines.txt` | green card (white space inside the word is ignored) |
| 7 | `04_bad_n6_one_letter_deleted.txt` | red card, "Missing permutations 4 of 720", only Details |
| 8 | `11_bad_n5_truncated.txt` | red card, 43 missing |
| 9 | `05_bad_n6_foreign_char.txt` | red card (a foreign symbol; n is read as 7) |
| 10 | `07_bad_n8_broken.spra.xz` | grey card "File refused", only the reason ("extra bytes after the last token (at letter ...)") |
| 11 | `10_not_spra.spra.xz` | private grey card: file refused, "not a SPRA file" |
| 12 | `08_refused_n15.txt` | private card: n = 15 is above this bot's limit (n <= 14), nothing was run |
| 13 | `09_refused_wrong_ext.zip` | private card: file name ending, what to do |
| 14 | `/check url:` a github.com link to a public word | green card |
| 15 | `/check url:https://example.com/a.txt` | private card: host not allowed |
| 16 | `/check` with no options | a form with a file upload and a link field; submit with a file, with a link, with nothing (refused), with both (refused) |
| 17 | `/panel` as a member without Manage Server, then with it | refused; then a panel message with a "Check a word" button that opens the same form; restart the bot and press it again |
| 18 | `01_ok_n5.txt` with `submit: true` | entry for n = 5 in the table (display name and user id), the Contributor role, a gold announcement card in the records channel with a button that opens the job card |
| 19 | the same again | "already listed": table and roles unchanged |
| 20 | `02_ok_n7.txt.xz` without `submit`, press Add to records; then press it again; then as another user | "Added" (button disabled) and the card shows the table line; "Already added"; "Only the submitter" |
| 21 | Details on any finished card (also as another user) | private message with every key and the full SHA-256 |
| 22 | an n = 13 word | slow lane: grey card with position and estimate, then a blue card with a bar, percent, letters, ETA, elapsed; edited about every 30 s, never twice within 5 s |
| 23 | during 22: `/queue` | private card with the running job, its mini bar and an "Open" button that jumps to its card |
| 24 | during 22: a second n = 13 word from the same user | private card: one check per user and lane |
| 25 | during 22: `01_ok_n5.txt` | runs at the same time in the fast lane |
| 26 | Cancel button on the n = 13 card, as another user without Manage Messages first | "Not yours"; then as the submitter: the card turns grey "Cancelled by ..."; no checker process is left on the host |
| 27 | `/cancel <id>` on a second n = 13 job | same, through the command |
| 28 | an n = 14 recipe with a low `SUPERPERM_MEM_GB` | the card shows passes; starting and cancelling is enough |
| 29 | delete a card message while its job runs | the console warns and ONE new card appears with the state |
| 30 | remove Send Messages from the bot in a channel, repeat 3 there | private card "Could not start" |
| 31 | restart the bot while a job runs, then press a button of its old card | the job is gone; the button answers "Job #N is gone" instead of "interaction failed" |

Private checks (SUPERPERM_AUDIT_CHANNEL set to a channel only admins can read; use two ordinary accounts A and B, a
moderator M with Manage Messages and an administrator R):

| # | command | expected |
|---|---|---|
| 32 | A: `/settings`, choose Private | "Saved"; `/settings` again shows Private; a new user B still gets Public |
| 33 | A (DMs open): `/check` with `01_ok_n5.txt` and no visibility | private reply "the card is in your direct messages" with a button to the DM; the full card in A's DM, edited to the result; in the channel only the grey note "Private check by @A, n = 5, finished" (no file name, length, SHA, verdict); the audit channel has the copy with Details |
| 34 | A: `/check visibility: public` | the normal public card, no note, no audit copy |
| 35 | B: `/check visibility: private` with DMs closed (Privacy settings of the server) | private card: "I cannot send you a direct message. Allow direct messages ... or run the check as public."; nothing in the channel, nothing queued (`/queue` is empty) |
| 36 | A runs a private n = 13 check; during it B, M, R and A open `/queue` | B: "private check, n = 13", no name, no link; A: full line with file name and an Open button to the DM; M: user, n, size, progress, a Cancel button, no file name; R: also the file name |
| 37 | M presses Cancel in `/queue` on A's job; then Details on the audit copy as M and as R | A's DM card turns "Cancelled by M"; the note says finished; Details: M "Administrators only", R sees the result |
| 38 | A: a valid private word shorter than the record, press Add to records | confirmation card with Publish and Keep private; Keep private: nothing published; again, Publish: table entry (display name), role, announcement in the records channel without an "Open the check" button |
| 39 | A in a direct message with the bot: `/check`, `/queue`, `/records`, `/about`, `/settings`, `/panel` | all work except `/panel` (server only); the check card is in that conversation; nothing on the server; the audit channel has the copy; Add to records asks first, then the role is given (A shares a server with the bot) |
| 40 | `/about` and the `/panel` message | the Privacy section / the short privacy lines |

History, records management, magic check (audit channel set; users A and B, moderator M, administrator R):

| # | command | expected |
|---|---|---|
| 41 | A runs two checks (one public, one private), then `/history` | a private card with both rows: Discord timestamp in A's time zone, n, file name, emoji, length, job id; the select opens one entry with the full SHA-256 |
| 42 | A: Rename an entry (empty name, 81 characters, a normal name); Delete another | the first two are refused, the third changes only the shown name; the delete removes the row |
| 43 | A with more than 10 checks: Next / Previous | pages of 10, buttons disabled at the ends |
| 44 | B: `/history user:@A`; M: the same; R: the same | B refused; M sees A's private checks as "private check, n = ..." without name, length, SHA, verdict, can delete but not rename; R sees everything, can rename; M's and R's delete / rename show up in the audit channel |
| 45 | `/records`, pick n = 13 as an ordinary user, as A (owner of an entry), as M, as R | no "Manage entry" select for an ordinary user; A: only own entries; M: non-record entries that have an owner; R: all |
| 46 | R removes the current record of an n (confirm card first) | confirmation text says it is the record; after Remove the next best verified entry is the record; audit line "RECORD entry" |
| 47 | A removes a non-record entry of own; M removes someone else's; M tries a seed claim | the first two work (M's is audited); M cannot touch the seed claim |
| 48 | Rename a records entry as the submitter, as M | the submitter can (name shows in the details), M cannot |
| 49 | A new record, look at its details | the entry shows a Discord timestamp; old entries show their date |
| 50 | Upload a gzip file named `x.txt.xz`, a text file named `x.spra`, a binary named `x.txt`, a list in `x.splan.json` | each refused at once with "File refused" and a clear reason; nothing queued, no history row |
| 51 | `/about` | a History section with the days and the "never the file" sentence |

For each step note who sees the reply (only the author, or everyone), whether the card looks as in
`python -I tools/preview_cards.py` (colours, bar characters, the aligned block) and whether the text reads well.

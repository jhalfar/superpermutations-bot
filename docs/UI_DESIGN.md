# Discord UI of superpermutations-bot (v1.0 design)

The first live test worked, but every reply was plain text, each job produced several messages, `/records` printed a
Markdown table that Discord does not render, and threads never opened (the bot called `create_thread` on the
interaction's webhook message, which has no bot HTTP state; the exception was swallowed). This design replaces the
text replies with Components V2 messages (discord.py 2.6+: `discord.ui.LayoutView`, `Container`, `TextDisplay`,
`Section`, `Separator`, `ActionRow`, `Button`, `Select`, `Modal`, `Label`, `FileUpload`).

## Principles

* One live card per job. The card is a normal channel message sent with the bot token (`channel.send`), so it can be
  edited for hours (an interaction token lives 15 minutes). The interaction itself gets a short ephemeral reply.
* State is shown by colour and one emoji, the same everywhere:
  ⏳ queued (grey 0x99AAB5), 🔄 running (blue 0x3B82F6), ✅ valid (green 0x22A06B), ❌ not valid (red 0xD64545),
  ⛔ refused or cancelled (dark grey 0x5C6670), 🏆 new record (gold 0xF2B134). Accent colours come from one table.
* Numbers with thousands separators; SHA-256 in inline code; the submitter as a mention with
  `allowed_mentions=none` (shows the display name, never pings); file names cleaned.
* Short labels, no walls of text. Details on demand (buttons that answer ephemerally).
* No threads (the card replaces them).

## Mockups (text inside each Container)

Job card, running:
```
🔄 **Check #12** · n = 14 · recipe
`13_slow_n14_recipe.splan.json.xz` · by @Jakub Halfar
───────────────
▰▰▰▱▱▱▱▱▱▱  31 %   pass 1 of 3
29.1 G of 93.9 G letters · about 52 min left · 12 min elapsed
[ Cancel ]                      (red; submitter or Manage Messages/Administrator only)
```
Queued: `⏳ Check #12 · n = 14 · recipe`, then `Position 2 in the slow lane (n = 13, 14) · estimated 2 h 36 min,
3 passes`. Progress is edited every `progress_s` (default 30 s; at most one edit per 5 s per message).

Job card, finished valid:
```
✅ **Valid superpermutation** · n = 13
`12_slow_n13_6747802319.spra.xz` · by @Jakub Halfar
───────────────
**Length** 6,747,802,319        **Alphabet** `123456789ABCD`
**SHA-256** `aec1e713…d3` (full value under Details)
SPRA 0.2, six-bit fields · header length matches
Checked in 4 min 17 s · 1 pass
[ Add to records ]  [ Details ]
```
`Add to records` appears only for the submitter and only if the word would enter the table (shorter than the
verified record of its n, or the first verified entry); pressing it does what `submit: true` does now (table, role,
announcement) and changes the button to "Added ✓" (disabled). `submit: true` on `/check` keeps working.
`Details` answers ephemerally with all keys of the result (kind, n, length, windows, repeats, shards, times, full
SHA, recipe/SPRA footer checks) in a code block.

Finished not valid:
```
❌ **Not a superpermutation** · n = 6
`04_bad_n6_one_letter_deleted.txt` · by @Jakub Halfar
───────────────
**Missing permutations** 4 of 720
**Length** 872 · **Alphabet** `012345`
Checked in 0.1 s
[ Details ]
```
Refused file (decoder or recipe refused it): only the reason, never "missing 40,320" or "length differs":
`⛔ File refused · SPRA decoder: extra bytes after the last token (at letter 46,225)`.
Cancelled: `⛔ Cancelled by @X after 3 min (pass 1 of 3, 10.6 %)`.

Refusals before a job exists (wrong ending, host, n > 14, too long, queue full, one job per lane): ephemeral red
card, title plus one line of reason plus one line of what to do, e.g.
`⛔ Not accepted · n = 15 is above this bot's limit (n ≤ 14). Nothing was run.`

`/records`: one card (gold accent):
```
🏆 **Shortest known superpermutations**
```
then a monospace block with aligned columns (no Markdown table):
```
 n           length  holder              status
 5              153  Jakub Halfar        ✓ verified
 7            5,905  Teodorescu          ○ claim
 ...
14   93,904,936,032  William Echols      ✓ verified
```
`✓ verified` = checked by this bot; `○ claim` = published, not checked here. Below: a Select "Details for n = …"
answering ephemerally with that n's entries (length, SHA, source link, date, verified or claim).

`/queue`: ephemeral card, one Section per lane with the running job (mini progress bar) and the waiting jobs.

`/about`: card with four short sections (What it checks, Formats, Limits, Commands) and link buttons to the
repository and to docs/RECIPE.md / the SPRA format.

Record announcement in the records channel (`SUPERPERM_CHANNEL`):
```
🏆 **New record for n = 13**
6,747,801,994 letters · 399 shorter than the previous record (6,747,802,393, Jakub Halfar)
by @Jakub Halfar · `file name` · SHA-256 `…`
[ Open the check ]   (link button to the job card)
```

`/check` without options, or a button "Check a word" on a panel message (`/panel`, Manage Server only, posts a
persistent panel with that button in the current channel): opens a Modal with a FileUpload ("Word or recipe file")
and a TextInput ("or a public URL"). Exactly one of the two; then the same flow as `/check`.

## Behaviour kept

Limits, lanes, queue, shards, cancel rules, downloads, security model, records format, announcement channel and
role logic stay as they are. Only presentation and the message flow change. Everything a card shows comes from pure
functions (`card_for(job)`, `records_card(table)`, ...) that tests can call without Discord.

## Small fixes found in the live test

* The plan said "154 letters" for a 153-letter .txt (it counted the newline): count letters (or say "about" when
  unknown).
* "estimated 0 s" → "under a second".
* Names: store and show the display name (and keep the user id) instead of the login name.
* The permissions list: Send Messages, Embed Links, Read Message History, Manage Roles (only with the Contributor
  role); the thread permissions are no longer needed.

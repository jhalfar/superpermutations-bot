"""ui.py - the Discord cards of superpermutations-bot (Components V2), built by pure functions.

Every card is a discord.ui.LayoutView with one Container (accent colour = state). Nothing here talks to Discord: a card is
built from a job, a result dict, the records table or the scheduler, so tests/test_ui.py and tools/preview_cards.py
construct and print all of them without a gateway. commands.py and cards.py send and edit them.

Limits that matter (discord.py 2.7.1, ui/view.py, ui/text_display.py, ui/section.py, ui/action_row.py):
  * a LayoutView holds at most 40 components in all (a Container and every child, a Section counts itself, its texts and
    its accessory), and at most 4000 characters of text in all (TextDisplay);
  * a message with a LayoutView cannot carry content or embeds;
  * an ActionRow holds up to 5 buttons or one select; a Section up to 3 texts and exactly one accessory.
check_limits(view) re-checks both numbers; the tests call it on every card.

Buttons that act on a job, the panel button and the records select are DynamicItems: their custom_id carries the job
number (sp:cancel:12), discord.py rebuilds them from the message when someone presses them, so they also work after a
restart of the bot (the handler then says that the job is gone). The handlers are set by discord_bot.build_client (HANDLERS).
"""
import math
import re
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder
import discord
from discord import ui

import records

REPO_URL = "https://github.com/jhalfar/superpermutations-bot"
RECIPE_URL = REPO_URL + "/blob/main/docs/RECIPE.md"
SPRA_URL = "https://github.com/Tholsh/SPRA"

MAX_VIEW_COMPONENTS = 40
MAX_VIEW_TEXT = 4000

# one table for accent colours and one for emoji; the same everywhere
COLOURS = {
    "queued": 0x99AAB5,
    "running": 0x3B82F6,
    "valid": 0x22A06B,
    "invalid": 0xD64545,
    "refused": 0x5C6670,
    "cancelled": 0x5C6670,
    "stopped": 0x5C6670,
    "record": 0xF2B134,
    "info": 0x5865F2,
}
EMOJI = {
    "queued": "⏳",            # hourglass
    "running": "\U0001F504",       # arrows
    "valid": "✅",             # check mark
    "invalid": "❌",           # cross
    "refused": "⛔",           # no entry
    "cancelled": "⛔",
    "stopped": "⛔",
    "record": "\U0001F3C6",        # trophy
    "info": "ℹ️",
}
LOCK_EMOJI = "\U0001F512"
TICK, CIRCLE = "✓", "○"
DOT = "·"
ELLIPSIS = "…"
LE = "≤"

# permissions the bot needs (README, /about and docs/DEPLOY.md are computed from this list)
PRIVACY_TEXT = (
    'Checks are public by default: the card here shows the file name, length, SHA-256, result and who sent it; the file itself is never reposted. A private check (`/check visibility: private`, or `/settings`) leaves only a small note here (who and n, no result) and sends the card to you by direct message. For no trace at all, run `/check` in a direct message with me. Moderators (Manage Messages) see that a private check runs and can stop it, not its result. Server admins see private checks in an audit channel, and the host of the bot can see all checks.')
PANEL_PRIVACY_TEXT = (
    'Checks are public by default: the card shows the file name, length, SHA-256, result and who sent it; the file itself is never reposted. A private check leaves only a small note here and sends the card by direct message (`/settings` sets your default).')

HISTORY_TEXT = ("The bot keeps a history of checks for %d days: time, user, file name, n, length, SHA-256, result; never the "
                "file (it is deleted right after the check). `/history` shows yours; you can delete or rename your entries.")

PERMISSION_BITS = (("Send Messages", 11), ("Embed Links", 14), ("Read Message History", 16))
ROLE_PERMISSION = ("Manage Roles", 28)

KIND_LABEL = {"word": "word", "recipe": "recipe", "spra": "SPRA word"}
LANE_LABEL = {"fast": "fast lane (n %s 12)" % LE, "slow": "slow lane (n = 13, 14)"}

# handlers, set by discord_bot.build_client: name -> async function
HANDLERS = {"job": None, "panel": None, "records": None, "settings": None, "x": None}


# ----------------------------------------------------------------------------------------------- small formatters

def clean_name(name):
    """a file name reduced to [A-Za-z0-9._-], at most 80 characters."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", re.split(r"[\\/]", str(name or ""))[-1])[:80] or "file"


def safe_text(s, limit=40):
    """a name from outside (display name, source): no control characters, no backticks, shortened."""
    s = re.sub(r"[\x00-\x1f\x7f`]+", " ", str(s or "")).strip()
    return s if len(s) <= limit else s[:limit - 1] + ELLIPSIS


def fmt_int(x):
    return "{:,}".format(int(x))


def fmt_dur(s):
    """an estimate or an elapsed time: 'under a second', '45 s', '12 min', '2 h 36 min'."""
    s = float(max(0, s))
    if s < 1:
        return "under a second"
    s = int(s)
    if s < 90:
        return "%d s" % s
    if s < 5400:
        return "%d min" % round(s / 60)
    return "%d h %02d min" % (s // 3600, (s % 3600) // 60)


def fmt_took(s):
    """the time a finished check took: '0.1 s', '12 s', '4 min 17 s', '1 h 05 min'."""
    s = float(max(0, s))
    if s < 1:
        return "%.1f s" % s if s >= 0.05 else "under 0.1 s"
    if s < 60:
        return "%d s" % round(s)
    if s < 3600:
        return "%d min %02d s" % (int(s) // 60, int(s) % 60)
    return "%d h %02d min" % (int(s) // 3600, (int(s) % 3600) // 60)


def fmt_letters(x):
    for div, unit in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if x >= div:
            return "%.1f %s" % (x / div, unit)
    return str(int(x))


def plural(k, word):
    return "%d %s%s" % (k, word, "" if k == 1 else "es" if word.endswith("s") else "s")


def bar(frac, width=10):
    frac = 0.0 if frac is None else min(1.0, max(0.0, frac))
    full = int(frac * width + 1e-9)
    return "▰" * full + "▱" * (width - full)


def short_sha(sha, head=8, tail=8):
    sha = str(sha or "")
    return sha if len(sha) <= head + tail + 1 else sha[:head] + ELLIPSIS + sha[-tail:]


def who(user_id, name):
    """the submitter: a mention (shown as the display name, never pings because allowed_mentions is none) or the name."""
    return "<@%d>" % int(user_id) if user_id else safe_text(name, 40) or "unknown"


def mention_ids(view):
    """user ids that a card mentions (for tests: allowed_mentions none means none of them is notified)."""
    return {int(m) for t in texts(view) for m in re.findall(r"<@(\d+)>", t)}


# ----------------------------------------------------------------------------------------------- building blocks

def _t(s, limit=1900):
    s = str(s)
    return ui.TextDisplay(s if len(s) <= limit else s[:limit - 3] + "...")


def _sep():
    return ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)


def _view(colour, *children):
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*children, accent_colour=colour))
    return view


def _row(*buttons):
    row = ui.ActionRow()
    for b in buttons:
        row.add_item(b)
    return row


def link_button(label, url):
    return ui.Button(style=discord.ButtonStyle.link, label=label, url=url)


def walk(view):
    return list(view.walk_children())


def texts(view):
    """the raw content of every TextDisplay of the card, in order."""
    return [i.content for i in walk(view) if isinstance(i, ui.TextDisplay)]


def buttons(view):
    """every button of the card (a DynamicItem is unwrapped)."""
    out = []
    for i in walk(view):
        b = i.item if isinstance(i, ui.DynamicItem) else i
        if isinstance(b, ui.Button):
            out.append(b)
    return out


def accent(view):
    for i in view.children:
        if isinstance(i, ui.Container):
            return i.accent_colour
    return None


def signature(view):
    """what a card shows, as a comparable value: the texts and the state of the buttons (to skip edits that change nothing)."""
    return (tuple(texts(view)), tuple((b.label, b.disabled) for b in buttons(view)))


def component_count(view):
    return len(walk(view))


def text_length(view):
    return sum(len(t) for t in texts(view))


def check_limits(view):
    """raise ValueError if the card breaks a Discord limit (40 components, 4000 characters of text, button labels)."""
    if component_count(view) > MAX_VIEW_COMPONENTS:
        raise ValueError("%d components (limit %d)" % (component_count(view), MAX_VIEW_COMPONENTS))
    if text_length(view) > MAX_VIEW_TEXT:
        raise ValueError("%d characters of text (limit %d)" % (text_length(view), MAX_VIEW_TEXT))
    for b in buttons(view):
        if b.label and len(b.label) > 80:
            raise ValueError("button label longer than 80")
        if b.custom_id and len(b.custom_id) > 100:
            raise ValueError("custom_id longer than 100")
    return True


# ----------------------------------------------------------------------------------------------- dynamic items

class JobButton(ui.DynamicItem[ui.Button], template=r"sp:(?P<act>cancel|add|details|publish|keep|audit):(?P<jid>[0-9]+)"):
    """Cancel / Add to records / Details of the card of job `jid`."""

    def __init__(self, act, jid, label, style, disabled=False):
        super().__init__(ui.Button(style=style, label=label, disabled=disabled, custom_id="sp:%s:%d" % (act, jid)))
        self.act, self.jid = act, int(jid)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["act"], int(match["jid"]), getattr(item, "label", None) or "", item.style)

    async def callback(self, interaction):
        await HANDLERS["job"](self.act, self.jid, interaction)


class PanelButton(ui.DynamicItem[ui.Button], template=r"sp:panel:check"):
    """'Check a word' on the panel message: opens the modal."""

    def __init__(self):
        super().__init__(ui.Button(style=discord.ButtonStyle.primary, label="Check a word", custom_id="sp:panel:check"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction):
        await HANDLERS["panel"](interaction)


class SettingsSelect(ui.DynamicItem[ui.Select], template=r"sp:settings:vis"):
    """'Default visibility of my checks' on the /settings card."""

    def __init__(self, current="private"):
        sel = ui.Select(custom_id="sp:settings:vis", placeholder="Default visibility of my checks", min_values=1, max_values=1)
        sel.add_option(label="Private", value="private", description="the card comes by direct message", default=current == "private")
        sel.add_option(label="Public", value="public", description="the card is posted in the channel", default=current == "public")
        super().__init__(sel)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction):
        await HANDLERS["settings"](self.item.values[0], interaction)


class RecordsSelect(ui.DynamicItem[ui.Select], template=r"sp:records:n"):
    """'Details for n = ...' under the records card."""

    def __init__(self, ns=()):
        sel = ui.Select(custom_id="sp:records:n", placeholder="Details for n = %s" % ELLIPSIS, min_values=1, max_values=1)
        for n in list(ns)[:25]:
            sel.add_option(label="n = %s" % n, value=str(n))
        super().__init__(sel)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction):
        await HANDLERS["records"](self.item.values[0], interaction)


# ----------------------------------------------------------------------------------------------- job cards

def result_state(res):
    """valid | invalid | refused | cancelled | stopped, from the result dict of the checker or the scheduler."""
    if res.get("cancelled"):
        return "cancelled"
    if res.get("timeout"):
        return "stopped"
    if is_refused(res) or not res.get("n"):
        return "refused"
    return "valid" if res.get("ok") else "invalid"


def is_refused(res):
    err = str(res.get("error") or "")
    return bool(err) and ("refused" in err or err.startswith("generator ended"))


def refusal_reason(err):
    """the decoder's or the planner's message without the plumbing: 'SPRA decoder: extra bytes ... (at letter 46,225)'."""
    err = str(err or "").strip()
    err = re.sub(r"^SPRA decoder refused the file: (?:spra: )?", "SPRA decoder: ", err)
    err = re.sub(r"^SPRA file refused: (?:spra: )?", "SPRA file: ", err)
    err = re.sub(r"^recipe refused: ", "recipe: ", err)
    err = re.sub(r"^generator ended with code (\d+): ", r"generator ended with code \1: ", err)
    err = re.sub(r"\(at letter (\d+)(?:, tuple \d+)?\)", lambda m: "(at letter %s)" % fmt_int(m.group(1)), err)
    return err[:400]


def _progress_numbers(job, now):
    """pass, passes, letters, length of a pass (or None), whether that length is only an estimate."""
    p, k, letters = job.progress
    p = max(1, p)
    k = max(1, k)
    length = job.plan.length
    est = False
    if not length:
        length, est = job.plan.est_len, True
    return p, k, letters, length, est


def running_lines(job, now=None):
    now = now or time.time()
    p, k, letters, length, est = _progress_numbers(job, now)
    el = now - (job.t_start or now)
    frac = min(0.99 if est else 1.0, letters / length) if length else 0.0
    done = (p - 1) * length + letters if length else 0
    total = k * length if length else 0
    pct = round(frac * 100)
    if pct >= 100 and p >= k:
        pct = 99                                  # the verdict is not in yet
    head = "%s  %s%d %%" % (bar(frac), "~" if est else "", pct)
    if k > 1:
        head += "   pass %d of %d" % (p, k)
        if total:
            head += " %s overall %d %%" % (DOT, int(min(1.0, done / total) * 100))
    parts = ["%s of %s%s letters" % (fmt_letters(letters), "about " if est else "", fmt_letters(length))]
    if total and done > 0 and el > 5:
        left = el * max(0, total - done) / done
        parts.append("about %s left" % fmt_dur(left))
    parts.append("%s elapsed" % fmt_dur(el))
    lines = [head, (" %s " % DOT).join(parts)]
    lines.append("-# updated <t:%d:R>" % int(now))     # Discord shows this as a live "x seconds ago"
    if est:
        lines.append("-# The length of a compressed word is not stated, so the percentage is estimated.")
    return lines


def job_head(job, state, title, n=True, finished=False):
    kind = KIND_LABEL.get(getattr(job.plan, "kind", None), "")
    h = "%s **%s**" % (EMOJI[state], title)
    if n and job.n:
        h += " %s n = %d" % (DOT, job.n)
    if kind and not finished:
        h += " %s %s" % (DOT, kind)
    if getattr(job, "private", False):
        h += " %s %s private" % (DOT, LOCK_EMOJI)
    name_line = "`%s` %s by %s" % (clean_name(job.name), DOT, who(job.user_id, job.user_name))
    if finished:
        name_line += " %s job #%d" % (DOT, job.id)
    return h + "\n" + name_line


def cancel_button(job):
    return JobButton("cancel", job.id, "Cancel", discord.ButtonStyle.danger)


def job_buttons(job, finished, can_add, added):
    out = []
    if not finished:
        out.append(cancel_button(job))
    else:
        if added:
            out.append(JobButton("add", job.id, "Added %s" % TICK, discord.ButtonStyle.secondary, disabled=True))
        elif can_add:
            out.append(JobButton("add", job.id, "Add to records", discord.ButtonStyle.success))
        out.append(JobButton("details", job.id, "Details", discord.ButtonStyle.secondary))
    return out


def _job_row(job, finished, can_add, added, audit):
    """the buttons of a job card; the audit copy has Details only (it answers only an administrator)."""
    if audit:
        return _row(JobButton("audit", job.id, "Details", discord.ButtonStyle.secondary))
    return _row(*job_buttons(job, finished, can_add, added))


def job_card(job, result=None, *, now=None, position=None, extra=(), can_add=False, added=False, audit=False):
    """the live card of a job. result=None: queued (position: place among the waiting, 0/None = next) or running
    (job.status == 'running'); a result dict: the final card. extra: more lines under the result (table, role)."""
    now = now or time.time()
    plan = job.plan
    body = []
    if result is None:
        if job.status == "running":
            state = "running"
            head = job_head(job, state, "Check #%d" % job.id)
            body = running_lines(job, now)
        else:
            state = "queued"
            head = job_head(job, state, "Check #%d" % job.id)
            lane = LANE_LABEL[job.lane]
            if job.status == "starting" or not position:
                where = "Starting now in the %s" % lane
            else:
                where = "Position %d in the %s" % (position, lane)
            body = ["%s %s estimated %s, %s" % (where, DOT, fmt_dur(plan.est_total_s), plural(job.shards, "pass"))]
            body.append("%s letters" % (fmt_int(plan.length) if plan.length else "About %s" % fmt_int(plan.est_len)))
            body.append("-# Waiting checks are lost if the bot restarts.")
        view = _view(COLOURS[state], _t(head), _sep(), _t("\n".join(body)), _job_row(job, False, False, False, audit))
        return view

    res = result
    state = result_state(res)
    el = res.get("seconds")
    took = "Checked in %s" % fmt_took(el) if el is not None else None
    if state == "cancelled":
        head = job_head(job, state, "Cancelled", n=False, finished=True)
        by = job.cancelled_by or "the owner"
        if job.t_start:
            p, k, letters = job.progress
            length = plan.length
            part = "pass %d of %d" % (max(1, p), max(1, k))
            if length:
                part += ", %.1f %%" % min(100.0, 100.0 * letters / length)
            body = ["Cancelled by %s after %s (%s)." % (safe_text(by), fmt_dur(now - job.t_start), part)]
        else:
            body = ["Cancelled by %s before it started." % safe_text(by)]
    elif state == "stopped":
        head = job_head(job, state, "Stopped, no verdict", n=False, finished=True)
        body = [refusal_reason(res.get("error"))]
        if took:
            body.append(took)
    elif state == "refused":
        if is_refused(res):
            head = job_head(job, state, "File refused", n=False, finished=True)
        else:
            head = job_head(job, state, "Not checked", n=False, finished=True)
        body = [refusal_reason(res.get("error")) or "no reason given"]
        if took:
            body.append(took)
    elif state == "valid":
        head = job_head(job, state, "Valid superpermutation", finished=True)
        body = ["**Length** %s %s **Alphabet** `%s`" % (fmt_int(res.get("length", 0)), DOT, safe_text(res.get("alphabet"), 20)),
                "**SHA-256** `%s` (full value under Details)" % short_sha(res.get("sha256"))]
        body += footer_lines(res)
        if took:
            body.append(took)
    else:
        mismatch = bool(res.get("valid")) and not res.get("ok")
        head = job_head(job, state, "Word and header disagree" if mismatch else "Not a superpermutation", finished=True)
        if res.get("missing") is not None and not mismatch:
            body.append("**Missing permutations** %s of %s" % (fmt_int(res["missing"]), fmt_int(math.factorial(job.n))))
        if res.get("length") is not None:
            body.append("**Length** %s %s **Alphabet** `%s`" % (fmt_int(res["length"]), DOT, safe_text(res.get("alphabet"), 20)))
        if res.get("foreign_bytes"):
            body.append("**Foreign bytes** %s" % fmt_int(res["foreign_bytes"]))
        if res.get("shards", 1) > 1:
            body.append(shard_line(res))
        body += footer_lines(res, with_shards=False)
        if res.get("error"):
            body.append(refusal_reason(res["error"]))
        if took:
            body.append(took)
    body += [str(x) for x in extra]
    return _view(COLOURS[state], _t(head), _sep(), _t("\n".join(body)),
                 _job_row(job, True, can_add and state == "valid", added, audit))


def shard_line(res):
    k = res.get("shards", 1)
    if res.get("pass_mismatch"):
        return "%d passes; they saw different data" % k
    return "%d passes; all saw the same length and SHA-256" % k


def footer_lines(res, with_shards=True):
    """what the format of the file adds: SPRA header, recipe generator and hash, passes."""
    out = []
    if res.get("kind") == "spra":
        out.append("SPRA %s, %s-bit fields %s header length %s" % (
            {1: "0.1", 2: "0.2"}.get(res.get("spra_version"), "?"),
            {4: "four", 6: "six"}.get(res.get("spra_bits"), res.get("spra_bits")), DOT,
            "matches" if res.get("length_matches") else "DIFFERS (%s)" % fmt_int(res.get("spra_length", 0))))
    elif res.get("kind") == "recipe":
        out.append("Recipe: generator `%s` %s length %s %s word hash %s" % (
            safe_text(res.get("generator"), 30), DOT, "matches" if res.get("length_matches") else
            "DIFFERS from the recipe's %s" % fmt_int(res.get("recipe_length", 0)), DOT,
            {True: "matches", False: "DIFFERS", None: "not stated"}[res.get("sha_matches")]))
    if with_shards and res.get("shards", 1) > 1:
        out.append(shard_line(res))
    return out


def accepted_card(job, url=None, position=None):
    """the short ephemeral reply to the person who started the check."""
    lane = LANE_LABEL[job.lane]
    where = ("%s, position %d" % (lane, position)) if position else lane
    private = getattr(job, "private", False)
    items = [_t("%s **Check #%d accepted** %s %s\n%s" % (
        EMOJI["queued"], job.id, DOT, where,
        "%s Private: the card is in your direct messages." % LOCK_EMOJI if private else
        "Follow it on the card in the channel."))]
    if url:
        items.append(_row(link_button("Open the direct message" if private else "Open the card", url)))
    return _view(COLOURS["queued"], *items)


def confirm_publish_card(job, where):
    """ephemeral, from Add to records on a private check: making the result public needs a second yes."""
    return _view(COLOURS["record"],
                 _t("%s **Make this result public?**\nThis makes the result public: entry in the table and an announcement in %s."
                    % (EMOJI["record"], where)),
                 _row(JobButton("publish", job.id, "Publish", discord.ButtonStyle.success),
                      JobButton("keep", job.id, "Keep private", discord.ButtonStyle.secondary)))


def trace_card(job, phase):
    """the small note a private check leaves in the channel: who and n, queued / running / finished. No file name, length,
    SHA-256, verdict or progress, no buttons."""
    return _view(COLOURS["queued"], _t("%s Private check by %s %s n = %d %s %s" % (
        LOCK_EMOJI, who(job.user_id, job.user_name), DOT, job.n, DOT, phase)))


def settings_card(current):
    """ephemeral: the select for the default visibility of the user's checks."""
    return _view(COLOURS["info"],
                 _t("%s **Your settings**\nDefault visibility of my checks: **%s**. `/check visibility:` changes one check."
                    % (EMOJI["info"], current)),
                 _row(SettingsSelect(current)))


def notice_card(title, text, state="refused"):
    """a short ephemeral answer: emoji, title, a line or two."""
    return _view(COLOURS[state], _t("%s **%s**\n%s" % (EMOJI[state], title, text)))


def refusal_card(reason, hint=None, title="Not accepted"):
    """a request that was refused before a job existed: title, one line of reason, one line of what to do."""
    lines = [str(reason)[:500]]
    if hint:
        lines.append(str(hint)[:300])
    return notice_card(title, "\n".join(lines), "refused")


DETAIL_ORDER = ("kind", "n", "valid", "ok", "length", "alphabet", "sha256", "missing", "foreign_bytes", "first_occurrences",
                "repeats", "distinct_seen", "shards", "shard_missing", "shard_valid", "shard_seconds", "pass_mismatch",
                "seconds", "generator", "recipe_length", "length_matches", "sha_matches", "recipe_word_sha256",
                "spra_version", "spra_bits", "spra_length", "error")


def _detail_value(v):
    if isinstance(v, bool) or v is None:
        return str(v).lower() if isinstance(v, bool) else "null"
    if isinstance(v, int):
        return fmt_int(v)
    if isinstance(v, (list, tuple)):
        s = "[" + ", ".join(_detail_value(x) for x in v) + "]"
        return s if len(s) <= 200 else s[:197] + "..."
    return str(v).replace("`", "'").replace("\n", " ")[:300]


# one short line per field of a result, shown under the Details table for the fields that are present
FIELD_HELP = {
    "kind": "what was checked: a word, a SPRA file or a recipe",
    "n": "number of symbols",
    "valid": "every permutation occurs and the file holds nothing else",
    "ok": "valid, and every other check below passed (recipe footer, passes)",
    "length": "letters of the word (white space not counted)",
    "alphabet": "the n symbols of the word, sorted",
    "sha256": "SHA-256 of the letters, without line feeds",
    "missing": "permutations that never occur (0 for a superpermutation)",
    "foreign_bytes": "bytes that are not letters of the alphabet",
    "first_occurrences": "windows where a permutation is seen for the first time (n! when valid)",
    "repeats": "windows whose permutation was seen before (wasted windows)",
    "distinct_seen": "different permutations seen",
    "windows": "windows of n consecutive letters read",
    "expected_windows": "windows the length implies (length - n + 1)",
    "permutation_windows": "windows whose n letters are all different",
    "shards": "passes over parts of the permutations (less memory, more time)",
    "shard_missing": "missing permutations found by each pass",
    "pass_mismatch": "the passes saw different lengths or hashes",
    "seconds": "time of the check",
    "generator": "program that expanded the recipe",
    "recipe_length": "length the recipe states",
    "length_matches": "the word has the stated length",
    "recipe_word_sha256": "SHA-256 the recipe states",
    "sha_matches": "the word has the stated SHA-256",
    "spra_version": "SPRA format version of the file",
    "spra_bits": "bits per SPRA field",
    "spra_length": "length in the SPRA header",
    "letters_read": "letters the checker read",
    "checker_rc": "exit code of the checker (0: it read to the end)",
    "gen_rc": "exit code of the recipe generator (0: it wrote the whole word)",
    "trailing_newlines": "line feeds at the end (allowed, not counted)",
    "error": "why the check stopped",
}


def details_card(job, result):
    """ephemeral: every key of the result, full SHA-256, in a code block, and a short legend of the fields."""
    keys = [k for k in DETAIL_ORDER if k in result] + sorted(k for k in result if k not in DETAIL_ORDER)
    w = max([len(k) for k in keys] or [1])
    lines = ["%-*s  %s" % (w, k, _detail_value(result[k])) for k in keys]
    code = "\n".join(lines)
    if len(code) > 3000:
        code = code[:2997] + "..."
    state = result_state(result)
    head = "%s **Details** %s job #%d %s `%s`" % (EMOJI[state], DOT, job.id, DOT, clean_name(job.name))
    legend = "\n".join("-# `%s` %s" % (k, FIELD_HELP[k]) for k in keys if k in FIELD_HELP)
    room = 3900 - len(head) - len(code) - 20      # one message holds 4,000 characters of text
    if len(legend) > room:
        legend = legend[:max(0, room - 3)].rsplit("\n", 1)[0] + "\n-# ..." if room > 40 else ""
    parts = [_t(head), _sep(), _t("```\n%s\n```" % code, limit=3300)]
    if legend:
        parts += [_sep(), _t(legend + "\n-# This card closes by itself after 10 minutes.", limit=max(60, room + 60))]
    return _view(COLOURS[state], *parts)


# ----------------------------------------------------------------------------------------------- records

def record_rows(table):
    """(n, entry) rows of the table: per n the shortest verified entry, and above it a shorter unverified claim if there is one."""
    rows = []
    for n in sorted(table["records"], key=int):
        v, k = records.best_verified(table, n), records.best_known(table, n)
        if k is not None and k is not v:
            rows.append((n, k))
            if v is not None:
                rows.append((None, v))
        elif v is not None:
            rows.append((n, v))
    return rows


def records_block(table):
    """a monospace block with aligned columns (no Markdown table: Discord does not render those)."""
    rows = record_rows(table)
    lines = [" n  %15s  %-18s  %s" % ("length", "holder", "status")]
    for n, e in rows:
        status = "%s verified" % TICK if e["verified"] else "%s claim" % CIRCLE
        lines.append("%2s  %15s  %-18s  %s" % (n if n is not None else "", fmt_int(e["length"]),
                                               safe_text(e["submitter"], 18), status))
    return "\n".join(lines)


def records_card(table):
    ns = sorted(table["records"], key=int)
    items = [_t("%s **Shortest known superpermutations**" % EMOJI["record"]),
             _t("```\n%s\n```" % records_block(table), limit=3000),
             _t("%s verified = checked by this bot %s %s claim = published, not checked here" % (TICK, DOT, CIRCLE))]
    if ns:
        items.append(_row(RecordsSelect(ns)))
    return _view(COLOURS["record"], *items)


def record_details_card(table, n, viewer=None):
    """ephemeral: every entry of one n. viewer = (user_id, staff, admin) adds a select 'Manage entry' for the entries that
    this viewer may change (records.permissions)."""
    es = records.entries(table, n)
    if not es:
        return notice_card("No entries", "The table has nothing for n = %s." % n, "refused")
    lines = []
    for e in es[:8]:
        mark = TICK + " verified" if e["verified"] else CIRCLE + " claim"
        who_ = safe_text(e["submitter"], 40)
        if e.get("submitter_id"):
            who_ = "<@%s>" % e["submitter_id"]
        lines.append("**%s letters** %s %s %s %s %s" % (fmt_int(e["length"]), DOT, mark, DOT, who_, DOT + " " + records.when(e)))
        if e.get("label"):
            lines.append("name `%s`" % clean_name(e["label"]))
        if e.get("sha256"):
            lines.append("SHA-256 `%s`" % e["sha256"])
        if e.get("checked_by") or e.get("checked_by_id"):
            lines.append("checked by %s" % who(int(e["checked_by_id"]) if e.get("checked_by_id") else None,
                                               e.get("checked_by")))
        src = str(e.get("source") or "")
        if src.startswith("https://") and " " not in src:
            lines.append("[source](<%s>)" % src[:300])
        elif src:
            lines.append("source: %s" % safe_text(src, 120))
    if len(es) > 8:
        lines.append("%s and %d more" % (ELLIPSIS, len(es) - 8))
    items = [_t("%s **n = %s**" % (EMOJI["record"], n)), _sep(), _t("\n".join(lines), limit=3500)]
    opts = record_manage_options(table, n, viewer) if viewer else []
    if opts:
        items.append(_row(XSelect("rm", n, "Manage entry", opts)))
    return _view(COLOURS["record"], *items)


def announcement_card(n, length, previous, user_id, submitter, filename, sha, url=None, role_id=None, holder=None):
    """the card in the records channel (gold). role_id: the role of this n (SUPERPERM_N_ROLES), mentioned on the first
    line; the sender allows exactly that role to be pinged. holder = (name, user_id or None): the word is somebody else's
    (a verified claim, or the holder option of /check); user_id/submitter are then the person who ran the check."""
    if previous:
        diff = "%s shorter than the previous record (%s, %s)" % (
            fmt_int(previous["length"] - length), fmt_int(previous["length"]), safe_text(previous["submitter"], 30))
    else:
        diff = "the first verified word for this n"
    if holder:
        hname, hid = holder
        by = "by %s %s checked by %s" % (who(int(hid) if hid else None, hname), DOT, who(user_id, submitter))
        title = "Record for n = %d verified" % n
    else:
        by, title = "by %s" % who(user_id, submitter), "New record for n = %d" % n
    items = [_t("%s **%s**\n%s letters %s %s\n%s %s `%s` %s SHA-256 `%s`" % (
        EMOJI["record"], title, fmt_int(length), DOT, diff, by, DOT, clean_name(filename), DOT, sha))]
    if role_id:
        items.insert(0, _t("<@&%d>" % int(role_id)))
    if url:
        items.append(_row(link_button("Open the check", url)))
    return _view(COLOURS["record"], *items)


# ----------------------------------------------------------------------------------------------- queue, about, panel

def queue_card(sched, now=None, urls=None, viewer_id=None, staff=False, admin=False):
    """ephemeral: both lanes; the running job with a mini bar (and a link button to its card), the waiting jobs.
    A private job is shown to others only as 'private check, n = 13'. The submitter sees it in full (file name, link button
    to the direct-message card). Staff (Manage Messages) see the user, n, size and progress and get a Cancel button, not the
    file name; an administrator also sees the file name. Nobody but the submitter gets the link."""
    now = now or time.time()
    urls = urls or {}
    items = [_t("%s **Queue**" % EMOJI["info"])]

    def private(j):
        return getattr(j, "private", False)

    def own(j):
        return viewer_id is not None and j.user_id == viewer_id

    def masked(j):
        return private(j) and not (own(j) or staff or admin)

    def lock(j):
        return " " + LOCK_EMOJI if private(j) else ""

    def size(j):
        return " %s %s letters" % (DOT, fmt_letters(j.plan.length or j.plan.est_len)) if private(j) else ""

    def fname(j):
        return " %s `%s`" % (DOT, clean_name(j.name)) if private(j) and (own(j) or admin) else ""

    for name in ("fast", "slow"):
        lane = sched.lanes[name]
        label = "**%s**" % LANE_LABEL[name]
        if lane.running is None and not lane.waiting:
            items += [_sep(), _t("%s: idle" % label)]
            continue
        lines = [label]
        cancels = []
        j = lane.running
        if j is not None:
            if masked(j):
                lines.append("%s private check %s n = %d" % (LOCK_EMOJI, DOT, j.n))
            elif j.status == "running":
                p, k, letters, length, est = _progress_numbers(j, now)
                frac = min(1.0, letters / length) if length else 0.0
                lines.append("%s #%d%s %s n = %d %s %s %s %s%d %%%s%s%s" % (
                    EMOJI["running"], j.id, lock(j), DOT, j.n, DOT, who(j.user_id, j.user_name), DOT, "~" if est else "",
                    round(frac * 100), " " + DOT + " pass %d of %d" % (p, k) if k > 1 else "", size(j), fname(j)))
                lines.append(bar(frac))
            else:
                lines.append("%s #%d%s %s n = %d %s %s %s starting%s%s" % (
                    EMOJI["running"], j.id, lock(j), DOT, j.n, DOT, who(j.user_id, j.user_name), DOT, size(j), fname(j)))
        waiting = []
        for i, w in enumerate(lane.waiting, 1):
            if masked(w):
                waiting.append("%d. %s private check %s n = %d" % (i, LOCK_EMOJI, DOT, w.n))
            else:
                waiting.append("%s %d. #%d%s %s n = %d %s %s %s about %s, %s%s%s" % (
                    EMOJI["queued"], i, w.id, lock(w), DOT, w.n, DOT, who(w.user_id, w.user_name), DOT,
                    fmt_dur(w.plan.est_total_s), plural(w.shards, "pass"), size(w), fname(w)))
        for q in ([j] if j is not None else []) + list(lane.waiting):
            if private(q) and (staff or admin) and not own(q):
                cancels.append(JobButton("cancel", q.id, "Cancel #%d" % q.id, discord.ButtonStyle.danger))
        items.append(_sep())
        link = None
        if j is not None and urls.get(j.id) and (not private(j) or own(j)):
            link = urls[j.id]
        if link:
            items.append(ui.Section(_t("\n".join(lines)), accessory=link_button("Open", link)))
        else:
            items.append(_t("\n".join(lines)))
        if waiting:
            items.append(_t("\n".join(waiting)))
        for k5 in range(0, len(cancels), 5):
            items.append(_row(*cancels[k5:k5 + 5]))
    items.append(_t("-# The queue is kept in memory: a restart of the bot drops it."))
    return _view(COLOURS["info"], *items)


def permission_value(with_roles):
    """the permissions integer for the invite link (computed from PERMISSION_BITS)."""
    v = sum(1 << b for _, b in PERMISSION_BITS)
    return v + (1 << ROLE_PERMISSION[1]) if with_roles else v


def permission_text():
    return "%s (and %s, only with the Contributor role)" % (
        ", ".join(n for n, _ in PERMISSION_BITS), ROLE_PERMISSION[0])


def about_card(cfg, version):
    sections = [
        ("What it checks", "Whether a word contains every permutation of its n symbols. It keeps the table of the shortest "
         "verified words per n (`/records`)."),
        ("Formats", "`.txt`, `.txt.xz` plain word %s `.spra`, `.spra.xz` SPRA word (Theo H.) %s `.splan.json.xz` recipe, a small "
         "file that the bot's own decoder expands into the word. Nothing you send is run as code." % (DOT, DOT)),
        ("Limits", "n %s %d %s a check is refused if its estimate is over %g h (at %g M letters/s) and a pass is stopped at "
         "%g %s its estimate %s memory %g GB per pass, in up to %d passes %s two lanes, fast (n %s 12) and slow (n = 13, 14), "
         "one worker each, one check per user and lane, %d waiting per lane %s the queue is in memory, a restart drops it."
         % (LE, cfg.max_n, DOT, cfg.max_hours, cfg.mbps, cfg.pass_factor, "×", DOT, cfg.mem_gb, cfg.max_shards, DOT, LE,
            cfg.queue_len, DOT)),
        ("Commands", "`/check` (without options it opens a form for a file or a link) %s `/queue` %s `/cancel` %s `/records` %s "
         "`/about` %s `/settings` %s `/panel` (Manage Server) posts a button for the form. `submit: true` or the **Add to records** button "
         "enters a valid word that is shorter than the verified record; a new record can give the Contributor role."
         % (DOT, DOT, DOT, DOT, DOT, DOT)),
        ("Privacy", PRIVACY_TEXT),
        ("History", HISTORY_TEXT % cfg.history_days),
    ]
    items = [_t("%s **superpermutations-bot v%s**" % (EMOJI["info"], version))]
    for title, text in sections:
        items += [_sep(), _t("**%s**\n%s" % (title, text))]
    items += [_t("-# Needs: %s." % permission_text()),
              _row(link_button("Repository", REPO_URL), link_button("Recipe format", RECIPE_URL),
                   link_button("SPRA format", SPRA_URL))]
    return _view(COLOURS["info"], *items)


def panel_card():
    """the persistent message that /panel posts: a button that opens the check form."""
    return _view(COLOURS["info"],
                 _t("%s **Check a superpermutation**\nPress the button, attach a word or recipe file or give a public link. "
                    "The result comes as a card.\n%s" % (EMOJI["info"], PANEL_PRIVACY_TEXT)),
                 _row(PanelButton()))



# ----------------------------------------------------------------------------------------------- history, records management

VERDICT_STATE = {"valid": "valid", "invalid": "invalid", "refused": "refused", "cancelled": "cancelled", "stopped": "stopped",
                 "error": "refused", "pending": "queued"}
HISTORY_PAGE = 10


def verdict_of(res):
    """valid | invalid | refused | cancelled | stopped | error, the word stored in the history."""
    s = result_state(res)
    if s == "refused" and not is_refused(res):
        return "error"
    return s


class XButton(ui.DynamicItem[ui.Button], template=r"sp:x:(?P<kind>[a-z]+):(?P<arg>[0-9a-z:\-]+)"):
    """a button of the history and records cards: custom_id sp:x:KIND:ARG (hp page, hd delete, hr rename, rd/rr/ry/rn records)."""

    def __init__(self, kind, arg, label, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(ui.Button(style=style, label=label, disabled=disabled, custom_id="sp:x:%s:%s" % (kind, arg)))
        self.kind, self.arg = kind, str(arg)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["kind"], match["arg"], getattr(item, "label", None) or "", item.style)

    async def callback(self, interaction):
        await HANDLERS["x"](self.kind, self.arg, None, interaction)


class XSelect(ui.DynamicItem[ui.Select], template=r"sp:y:(?P<kind>[a-z]+):(?P<arg>[0-9a-z:\-]+)"):
    """a select of the history and records cards (hs open a history entry, rm manage a records entry)."""

    def __init__(self, kind, arg, placeholder="Choose", options=()):
        sel = ui.Select(custom_id="sp:y:%s:%s" % (kind, arg), placeholder=placeholder[:150], min_values=1, max_values=1)
        for label, value, desc in list(options)[:25]:
            sel.add_option(label=label[:100], value=str(value), description=(desc or None) and desc[:100])
        super().__init__(sel)
        self.kind, self.arg = kind, str(arg)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["kind"], match["arg"])

    async def callback(self, interaction):
        await HANDLERS["x"](self.kind, self.arg, self.item.values[0], interaction)


class XUserSelect(ui.DynamicItem[ui.UserSelect], template=r"sp:u:(?P<kind>[a-z]+):(?P<arg>[0-9a-z:\-]+)"):
    """a member select of the records cards (rh: set the holder of an entry); the value passed on is the Member."""

    def __init__(self, kind, arg, placeholder="Choose a member"):
        super().__init__(ui.UserSelect(custom_id="sp:u:%s:%s" % (kind, arg), placeholder=placeholder[:150],
                                       min_values=1, max_values=1))
        self.kind, self.arg = kind, str(arg)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["kind"], match["arg"])

    async def callback(self, interaction):
        await HANDLERS["x"](self.kind, self.arg, self.item.values[0], interaction)


def when_unix(t):
    return "<t:%d:f>" % int(t)


def history_line(row, masked):
    """one history row. masked (staff looking at somebody else's private check): time, n and a lock only."""
    t = when_unix(row["t"])
    if masked:
        return "%s %s %s private check %s n = %s %s #%s" % (t, DOT, LOCK_EMOJI, DOT, row["n"] or "?", DOT, row["job_id"])
    lock = " " + LOCK_EMOJI if row["visibility"] in ("private", "dm") else ""
    bits = [t]
    if row["n"]:
        bits.append("n = %d" % row["n"])
    bits.append("`%s`" % clean_name(row["file_name"]))
    bits.append(EMOJI[VERDICT_STATE.get(row["verdict"], "refused")] if row["verdict"] != "error" else EMOJI["refused"])
    if row["length"]:
        bits.append("%s letters" % fmt_int(row["length"]))
    bits.append("#%s%s" % (row["job_id"], lock))
    return (" %s " % DOT).join(bits)


def history_card(rows, masks, page, pages, total, target_id, days):
    """ephemeral: one page (10 rows) of somebody's checks, newest first; a select opens one entry; Previous / Next."""
    head = "%s **Check history** %s <@%s> %s %d check%s" % (EMOJI["info"], DOT, target_id, DOT, total, "" if total == 1 else "s")
    if pages > 1:
        head += " %s page %d of %d" % (DOT, page + 1, pages)
    items = [_t(head)]
    if not rows:
        items += [_sep(), _t("Nothing here yet.")]
    else:
        items += [_sep(), _t("\n".join(history_line(r, m) for r, m in zip(rows, masks)))]
        opts = []
        for r, m in zip(rows, masks):
            label = "#%s %s private check" % (r["job_id"], LOCK_EMOJI) if m else "#%s %s n = %s %s %s" % (
                r["job_id"], DOT, r["n"] or "?", DOT, clean_name(r["file_name"]))
            opts.append((label, r["hid"], r["verdict"] if not m else None))
        items.append(_row(XSelect("hs", target_id, "Open one entry", opts)))
    if pages > 1:
        items.append(_row(XButton("hp", "%s:%d" % (target_id, max(0, page - 1)), "Previous", disabled=page <= 0),
                          XButton("hp", "%s:%d" % (target_id, min(pages - 1, page + 1)), "Next", disabled=page >= pages - 1)))
    items.append(_t("-# Kept for %d days: time, user, file name, n, length, SHA-256 and result; never the file itself." % days))
    return _view(COLOURS["info"], *items)


def history_entry_card(row, masked, can_delete, can_rename):
    """ephemeral: every field of one history entry (full SHA-256); masked: time, user, visibility and n only."""
    state = "queued" if masked else VERDICT_STATE.get(row["verdict"], "refused")      # a masked card must not show the verdict
    lines = ["**Time** %s" % when_unix(row["t"]), "**User** <@%s>" % row["user_id"], "**Visibility** %s" % row["visibility"]]
    if masked:
        lines.append("**n** %s" % (row["n"] or "?"))
        lines.append("-# This is a private check of somebody else: the rest is hidden.")
    else:
        lines += ["**File name** `%s`" % clean_name(row["file_name"]), "**Source** %s" % safe_text(row["source"], 200),
                  "**Kind** %s" % (row["kind"] or "?"), "**n** %s" % (row["n"] or "?"),
                  "**Length** %s" % (fmt_int(row["length"]) if row["length"] else "?"),
                  "**SHA-256** `%s`" % (row["sha256"] or "none"), "**Result** %s %s" % (EMOJI[state], row["verdict"]),
                  "**Seconds** %s" % (row["seconds"] if row["seconds"] is not None else "?"),
                  "**Added to the records** %s" % ("yes" if row["added"] else "no")]
    btns = []
    if can_delete:
        btns.append(XButton("hd", row["hid"], "Delete from history", discord.ButtonStyle.danger))
    if can_rename:
        btns.append(XButton("hr", row["hid"], "Rename"))
    items = [_t("%s **History entry** %s job #%s" % (LOCK_EMOJI if masked else EMOJI[state], DOT, row["job_id"])), _sep(), _t("\n".join(lines))]
    if btns:
        items.append(_row(*btns))
    return _view(COLOURS[state], *items)


def entry_ref_arg(n, e):
    return "%s:%s" % (n, records.ref(e))


def record_manage_options(table, n, viewer):
    """(label, value, description) for the entries of n that the viewer may change. viewer = (user_id, staff, admin)."""
    uid, staff, admin = viewer
    out = []
    for e in records.entries(table, n)[:25]:
        d, r, is_record = records.permissions(table, n, e, uid, staff, admin)
        if d or r:
            out.append(("%s letters %s %s" % (fmt_int(e["length"]), DOT, safe_text(e["submitter"], 30)), records.ref(e),
                        "the record" if is_record else ("verified" if e["verified"] else "claim")))
    return out


def record_manage_card(table, n, e, viewer):
    uid, staff, admin = viewer
    d, r, is_record = records.permissions(table, n, e, uid, staff, admin)
    mark = TICK + " verified" if e["verified"] else CIRCLE + " claim"
    lines = ["**%s letters** %s %s%s" % (fmt_int(e["length"]), DOT, mark, " " + DOT + " the record" if is_record else ""),
             "**By** %s %s %s" % ("<@%s>" % e["submitter_id"] if e.get("submitter_id") else safe_text(e["submitter"], 40), DOT,
                                  records.when(e))]
    if e.get("label"):
        lines.append("**Name** `%s`" % clean_name(e["label"]))
    if e.get("sha256"):
        lines.append("SHA-256 `%s`" % e["sha256"])
    if e.get("checked_by") or e.get("checked_by_id"):
        lines.append("**Checked by** %s" % who(int(e["checked_by_id"]) if e.get("checked_by_id") else None,
                                               e.get("checked_by")))
    btns = []
    if d:
        btns.append(XButton("rd", entry_ref_arg(n, e), "Delete", discord.ButtonStyle.danger))
    if r:
        btns.append(XButton("rr", entry_ref_arg(n, e), "Rename"))
    items = [_t("%s **Manage entry** %s n = %s" % (EMOJI["record"], DOT, n)), _sep(), _t("\n".join(lines))]
    if btns:
        items.append(_row(*btns))
    if admin and e["verified"]:                   # the holder joined the server: make them the holder of the entry
        items.append(_row(XUserSelect("rh", entry_ref_arg(n, e), "Set holder: choose the member")))
    return _view(COLOURS["record"], *items)


def record_confirm_card(n, e, is_record):
    text = "Remove this entry of n = %s (%s letters, %s) from the records table?" % (n, fmt_int(e["length"]), safe_text(e["submitter"], 40))
    if is_record:
        text += ("\nIt is the current record. After removing it the next best verified entry becomes the record (or n has none)."
                 " This is written to the audit channel if one is set.")
    return _view(COLOURS["invalid"], _t("%s **Remove from the records?**\n%s" % (EMOJI["invalid"], text)),
                 _row(XButton("ry", entry_ref_arg(n, e), "Remove", discord.ButtonStyle.danger),
                      XButton("rn", entry_ref_arg(n, e), "Keep")))


# ----------------------------------------------------------------------------------------------- plain-text preview

_MENTION = re.compile(r"<@(\d+)>")


def plain(view, names=None):
    """what a user would read: indented plain text (markdown marks removed, mentions as @name). For previews and tests."""
    names = names or {}

    def clean(s):
        s = _MENTION.sub(lambda m: "@" + names.get(int(m.group(1)), "user %s" % m.group(1)), s)
        s = s.replace("**", "").replace("`", "")
        s = re.sub(r"^-# ", "  ", s, flags=re.M)
        s = re.sub(r"\[([^\]]+)\]\(<[^>]*>\)", r"\1 (link)", s)
        return s

    def button_text(b):
        flags = {discord.ButtonStyle.danger: "red", discord.ButtonStyle.success: "green", discord.ButtonStyle.primary: "blue",
                 discord.ButtonStyle.link: "link"}.get(b.style)
        if b.disabled:
            flags = "disabled"
        return "[ %s ]%s" % (b.label, " (%s)" % flags if flags else "")

    def render(item, out, depth):
        pad = "    " * depth
        if isinstance(item, ui.Container):
            colour = item.accent_colour
            out.append("%s+-- accent #%06X" % (pad, colour if isinstance(colour, int) else colour.value))
            for c in item.children:
                render(c, out, depth + 1)
        elif isinstance(item, ui.TextDisplay):
            for line in item.content.splitlines():
                if line != "```":
                    out.append(pad + clean(line))
        elif isinstance(item, ui.Separator):
            out.append(pad + "-" * 40)
        elif isinstance(item, ui.Section):
            lines = []
            for c in item.children:
                render(c, lines, 0)
            lines[-1] += "    " + button_text(item.accessory)
            out.extend(pad + x for x in lines)
        elif isinstance(item, ui.ActionRow):
            parts = []
            for c in item.children:
                c = c.item if isinstance(c, ui.DynamicItem) else c
                if isinstance(c, ui.Select):
                    parts.append("[ %s v ]  options: %s" % (c.placeholder, ", ".join(o.label for o in c.options)))
                elif isinstance(c, ui.UserSelect):
                    parts.append("[ %s v ]  (server members)" % c.placeholder)
                else:
                    parts.append(button_text(c))
            out.append(pad + "  ".join(parts))
        else:
            out.append(pad + repr(item))

    out = []
    for c in view.children:
        render(c, out, 0)
    return "\n".join(out)

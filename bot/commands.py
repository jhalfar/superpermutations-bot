"""commands.py - the handlers of the slash commands and buttons: /check (and its form), /queue, /cancel, /records, /about,
/settings, /panel, and the buttons of a job card. They use only a few attributes of the interaction, so they run against
the fakes of tests/test_bot.py.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import time
import urllib.parse
from types import SimpleNamespace

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import discord

import history
import magic
import records
import settings
import ui
from cards import Card, card_loop, display_name, is_admin, may_cancel_any, no_mentions, reply, safe, side_cards, viewer_info
from config import ALLOWED_EXT, BIG_HINT, VERSION, UserError

DETAILS_TTL = 600          # seconds: Details and record look-ups remove themselves (they are ephemeral, but pile up)
from downloads import clean_name, download, ext_of, validate_url
from jobs import plan_job
from manage import hist_add, hist_finish
from state import JobCtx
from submit import do_submit, handle_add

log = logging.getLogger("superperm")


async def handle_about(state, interaction):
    await interaction.response.send_message(view=ui.about_card(state.cfg, VERSION), allowed_mentions=no_mentions())


async def handle_records(state, interaction):
    table = records.load(state.cfg.records_path)
    await interaction.response.send_message(view=ui.records_card(table), allowed_mentions=no_mentions())


async def handle_records_detail(state, n, interaction):
    """the select under the records card: every entry of one n, only for the person who chose."""
    table = records.load(state.cfg.records_path)
    n = str(n)
    if n not in table["records"]:
        await reply(interaction, ui.notice_card("No entries", "The table has nothing for n = %s." % ui.safe_text(n, 10)))
        return
    await reply(interaction, ui.record_details_card(table, n, viewer_info(interaction)), delete_after=DETAILS_TTL)


async def handle_queue(state, interaction):
    u = interaction.user
    urls = {j: c.card.url for j, c in state.cards.items() if c.card is not None and c.card.url}
    await reply(interaction, ui.queue_card(state.sched, urls=urls, viewer_id=u.id, staff=may_cancel_any(u), admin=is_admin(u)))


async def handle_settings(state, interaction):
    await reply(interaction, ui.settings_card(state.visibility_of(interaction.user.id)))


async def handle_settings_set(state, value, interaction):
    try:
        settings.set_visibility(state.cfg.settings_path, interaction.user.id, value)
    except (ValueError, OSError) as e:
        log.warning("could not save the settings: %r", e)
        await reply(interaction, ui.notice_card("Not saved", "I could not save your setting. Nothing changed."))
        return
    text = ("Your checks are %s by default. `/check visibility:` changes one check." % value)
    await reply(interaction, ui.notice_card("Saved", text, "valid"))


async def handle_cancel(state, interaction, job_id):
    job = state.sched.jobs.get(int(job_id))
    if job is None:
        await reply(interaction, ui.notice_card("Nothing to cancel", "There is no waiting or running job #%d." % int(job_id)))
        return
    mine = job.user_id == interaction.user.id
    if not mine and not may_cancel_any(interaction.user):
        await reply(interaction, ui.notice_card(
            "Not yours", "Job #%d is not yours. Cancelling other people's jobs needs Manage Messages or Administrator." % job.id))
        return
    state.sched.cancel(job, display_name(interaction.user))
    await reply(interaction, ui.notice_card("Cancelling", "Job #%d is being cancelled." % job.id, "cancelled"))


def restore_ctx(state, jid):
    """a job the bot no longer has in memory (restart, or an old card), rebuilt from its row in the history: enough for
    Details and Add to records (n, length, SHA-256, verdict, who sent it, whether it was added). None if the history has
    no row for it (never recorded, or past SUPERPERM_HISTORY_DAYS)."""
    try:
        row = history.get_job(state.cfg.history_path, jid)
    except Exception as e:
        log.warning("history lookup of job %d failed: %r", jid, e)
        return None
    if row is None:
        return None
    job = SimpleNamespace(id=jid, name=row["file_name"] or "", n=row["n"])
    ctx = JobCtx(job, None, row["source"] or "", int(row["user_id"]), row["user_name"] or "")
    ctx.private = row["visibility"] in ("private", "dm")
    ctx.hid, ctx.added, ctx.restored, ctx.row = row["hid"], bool(row["added"]), True, row
    if row["verdict"] == "valid" and row["n"] and row["length"] and row["sha256"]:
        ctx.result = {"ok": True, "valid": True, "n": int(row["n"]), "length": int(row["length"]),
                      "sha256": row["sha256"], "kind": row["kind"] or "word"}
    return ctx


def history_card(ctx):
    """Details of a restored job: what the history keeps."""
    r = ctx.row
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(r["t"])) if r["t"] else "-"
    lines = ["Job #%d, from the history (the bot restarted since, so only this is kept):" % ctx.job.id,
             "**File** `%s` %s **n** %s %s **Verdict** %s" % (ui.safe_text(r["file_name"], 60), ui.DOT, r["n"] if r["n"] else "-",
                                                             ui.DOT, r["verdict"]),
             "**Length** %s" % (ui.fmt_int(r["length"]) if r["length"] else "-"),
             "**SHA-256** `%s`" % (r["sha256"] or "-"),
             "**Checked** %s by %s %s **In the records** %s" % (when, ui.safe_text(r["user_name"], 40), ui.DOT,
                                                                "yes" if r["added"] else "no")]
    return ui.notice_card("Job #%d" % ctx.job.id, "\n".join(lines), "valid" if r["verdict"] == "valid" else "refused")


async def handle_job_button(state, act, jid, interaction):
    """Cancel / Add to records / Details on a job card. A job the bot no longer remembers is rebuilt from the history."""
    jid = int(jid)
    ctx = state.cards.get(jid)
    if ctx is None:
        ctx = restore_ctx(state, jid)
        if ctx is not None:
            state.remember(ctx)
    if ctx is None:
        await reply(interaction, ui.notice_card(
            "Job #%d is gone" % jid, "The bot does not remember this job and its history has no row for it "
            "(older than the history keeps). Send the file again if you still need a check."))
        return
    if getattr(ctx, "restored", False):
        if act == "cancel":
            await reply(interaction, ui.notice_card("Not running", "Job #%d is not running any more." % jid))
            return
        if act in ("details", "audit"):
            if act == "audit" and not is_admin(interaction.user):
                await reply(interaction, ui.notice_card("Administrators only", "Only members with the Administrator permission can open this."))
            elif act == "details" and ctx.private and interaction.user.id != ctx.user_id:
                await reply(interaction, ui.notice_card("Only the submitter", "The result of a private check is for the person who sent it."))
            else:
                await reply(interaction, history_card(ctx), delete_after=DETAILS_TTL)
            return
    if act == "cancel":
        await handle_cancel(state, interaction, jid)
    elif act == "details" and ctx.private and interaction.user.id != ctx.user_id:
        await reply(interaction, ui.notice_card("Only the submitter", "The result of a private check is for the person who sent it."))
    elif act == "audit":
        if not is_admin(interaction.user):
            await reply(interaction, ui.notice_card("Administrators only", "Only members with the Administrator permission can open this."))
        elif ctx.result is None:
            await reply(interaction, ui.notice_card("Not finished", "Job #%d has no result yet." % jid, "queued"))
        else:
            await reply(interaction, ui.details_card(ctx.job, ctx.result), delete_after=DETAILS_TTL)
    elif act == "publish":
        await handle_add(state, ctx, interaction, confirmed=True)
    elif act == "keep":
        await reply(interaction, ui.notice_card("Kept private", "Nothing was published.", "queued"))
    elif act == "details":
        if ctx.result is None:
            await reply(interaction, ui.notice_card("Not finished", "Job #%d has no result yet." % jid, "queued"))
        else:
            await reply(interaction, ui.details_card(ctx.job, ctx.result), delete_after=DETAILS_TTL)
    elif act == "add":
        await handle_add(state, ctx, interaction)


async def handle_check(state, interaction, attachment=None, url=None, submit=False, form=True, visibility=None, holder=None):
    """/check. Without a file and without a link it opens the form (form=False: it is the answer to the form itself and
    cannot open another one). One ephemeral answer for the user.
    holder (administrators only): the word is this person's; a valid word is entered for them (submit implied) and the
    administrator is shown as the one who checked it (records.submit checked_by).
    visibility 'public' (default for a user who never chose): one card in the channel. 'private': the card goes to the user by
    direct message and the channel gets only a small note (who, n, queued / running / finished). Run in a direct message with
    the bot (no server): the card is that conversation, nothing is posted on any server."""
    cfg, sched = state.cfg, state.sched
    in_dm = getattr(interaction, "guild", None) is None
    vis = visibility if visibility in settings.CHOICES else state.visibility_of(interaction.user.id)
    if attachment is None and url is None and form:
        await interaction.response.send_modal(CheckModal(state, submit, vis))
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    tmp = tempfile.mkdtemp(prefix="bot-", dir=cfg.workdir)
    job = ctx = ticker = None
    try:
        holder = " ".join(str(holder or "").split())[:60]
        if holder:
            if not is_admin(interaction.user):
                raise UserError("only administrators can check a word for somebody else.", title="Not allowed",
                                hint="Leave out the holder option to check the word as your own.")
            submit = True
        if attachment is None and url is None:
            raise UserError("there is nothing to check: no file and no link.", hint="Attach a file or paste a link.")
        if attachment is not None and url is not None:
            raise UserError("a file and a link were both given.", hint="Give one file or one link, not both.")
        if attachment is not None:
            name = clean_name(attachment.filename)
            if ext_of(name) is None:
                raise UserError("file name must end in %s" % ", ".join(ALLOWED_EXT),
                                hint="Rename the file, or convert it to one of these.")
            if attachment.size > cfg.max_download:
                raise UserError("file is larger than %d MB" % (cfg.max_download >> 20), hint=BIG_HINT)
            dest = os.path.join(tmp, "upload" + ext_of(name))
            await attachment.save(dest)
            source = "Discord attachment " + name
        else:
            name = clean_name(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
            if ext_of(name) is None:
                raise UserError("the link must end in %s" % ", ".join(ALLOWED_EXT),
                                hint="Link to the file itself, not to a page about it.")
            dest = os.path.join(tmp, "upload" + ext_of(name))
            await asyncio.to_thread(download, url, dest, cfg, state.fetch)
            source = validate_url(url)
        if os.path.getsize(dest) > cfg.max_download:
            raise UserError("file is larger than %d MB" % (cfg.max_download >> 20), hint=BIG_HINT)
        try:                                                         # a clear message for a file that is not what its name says
            await asyncio.to_thread(magic.check, dest, name)
        except magic.MagicError as e:
            raise UserError(str(e), hint=e.hint, title="File refused")
        plan = await asyncio.to_thread(plan_job, dest, cfg)          # n, shards, time estimate; refusals
        user_name = display_name(interaction.user)
        job = sched.new_job(interaction.user.id, user_name, name, dest, plan)
        job.private = in_dm or vis == "private"
        ctx = JobCtx(job, None, source, interaction.user.id, user_name)
        ctx.private, ctx.member, ctx.guild = job.private, interaction.user, getattr(interaction, "guild", None)
        ctx.holder = holder or None
        # ---- accepted: one card (bot token, editable for hours), a short ephemeral answer for the user
        if job.private and not in_dm:
            log.debug("job #%d is private: %s", job.id, name)          # file names of private jobs only at debug level
            sched.check_admit(job)                                     # per-user and queue limits, before the DM is sent
            try:
                dm = await interaction.user.create_dm()
                ctx.card = Card(dm, cfg.min_edit_s)
                await ctx.card.post(state.card_view(ctx))
            except discord.HTTPException as e:
                log.warning("job #%d: could not send the direct message: %r", job.id, e)
                raise UserError("I cannot send you a direct message.", title="Could not start",
                                hint="Allow direct messages from server members (Server menu > Privacy settings), or run the "
                                     "check as public.")
            try:
                sched.admit(job)
            except UserError as e:                                      # lost a race for the last place
                ctx.result = {"ok": False, "valid": False, "error": "not admitted: %s" % e}
                await ctx.card.update(lambda: state.card_view(ctx), wait=True)
                raise
        else:
            sched.admit(job)                                           # per-user and queue limits
            ctx.card = Card(getattr(interaction, "channel", None), cfg.min_edit_s)
            try:
                await ctx.card.post(state.card_view(ctx))
            except Exception as e:
                log.warning("job #%d: could not post the card: %r", job.id, e)
                raise UserError("I could not post the card in this channel.", title="Could not start",
                                hint="The bot needs View Channel and Send Messages here. Nothing was run.")
        state.remember(ctx)
        hist_add(state, ctx, "dm" if in_dm else "private" if job.private else "public",
                 "attachment" if attachment is not None else source)
        channel = getattr(interaction, "channel", None)
        if job.private and not in_dm and channel is not None:          # the note in the channel: who and n, nothing else
            try:
                ctx.trace = Card(channel, cfg.min_edit_s)
                await ctx.trace.post(state.trace_view(ctx))
            except Exception as e:
                log.warning("job #%d: could not post the note in the channel: %r", job.id, e)
                ctx.trace = None
        if job.private and cfg.audit_channel_id and state.audit_channel is not None:
            try:
                ch = await state.audit_channel()
                ctx.audit = Card(ch, cfg.min_edit_s)
                await ctx.audit.post(state.card_view(ctx, audit=True))
            except Exception as e:
                log.warning("job #%d: could not post the audit copy: %r", job.id, e)
                ctx.audit = None
        await safe(interaction.followup.send, view=ui.accepted_card(job, ctx.card.url, sched.position(job)), ephemeral=True,
                   allowed_mentions=no_mentions())
        ticker = asyncio.ensure_future(card_loop(state, ctx))

        async def on_start(j):          # the check does not wait for the edits
            ctx.tasks.append(asyncio.ensure_future(ctx.card.update(lambda: state.card_view(ctx), wait=True)))
            ctx.tasks.append(asyncio.ensure_future(side_cards(state, ctx)))
        job.on_start = on_start
        job.ready.set()
        res = dict(await job.fut, file=name)      # the checker saw the temporary name upload.EXT; show the user's
        ticker.cancel()
        ctx.result = res
        hist_finish(state, ctx)
        if res.get("ok"):
            ctx.can_add = records.would_enter(records.load(cfg.records_path), res["n"], res["length"])
            if submit:
                await do_submit(state, ctx, interaction)
        await ctx.card.update(lambda: state.card_view(ctx), wait=True)
        await side_cards(state, ctx)
    except UserError as e:
        await safe(interaction.followup.send, view=ui.refusal_card(str(e), e.hint, e.title), ephemeral=True,
                   allowed_mentions=no_mentions())
    except Exception as e:                                           # never leave the user without an answer
        log.warning("check failed: %r", e, exc_info=True)
        if job is not None:
            sched.cancel(job, "an internal error")
        if ctx is not None and ctx.card.msg is not None and ctx.result is None:
            ctx.result = {"ok": False, "valid": False, "error": "internal error: %s" % type(e).__name__}
            hist_finish(state, ctx)
            await ctx.card.update(lambda: state.card_view(ctx), wait=True)
            await side_cards(state, ctx)
        await safe(interaction.followup.send, view=ui.refusal_card(
            "internal error (%s)." % type(e).__name__, "Nothing was added to the records.", "Something went wrong"),
            ephemeral=True, allowed_mentions=no_mentions())
    finally:
        if ticker is not None:
            ticker.cancel()
        if ctx is not None:
            for t in ctx.tasks:
                t.cancel()
        if job is not None:
            if job.ready is not None:
                job.ready.set()
            if job.id in sched.jobs and job.status in ("queued",):
                sched.cancel(job, "cleanup")
        shutil.rmtree(tmp, ignore_errors=True)


class CheckModal(discord.ui.Modal, title="Check a word"):
    """the form of /check without options and of the panel button: a file upload or a link (exactly one)."""
    file = discord.ui.Label(text="Word or recipe file", description="a .txt, .txt.xz, .spra, .spra.xz or .splan.json.xz file",
                            component=discord.ui.FileUpload(required=False, min_values=0, max_values=1))
    link = discord.ui.Label(text="or a public link", description="github.com, raw.githubusercontent.com or gitlab.com",
                            component=discord.ui.TextInput(required=False, max_length=500,
                                                           placeholder="https://github.com/user/repo/blob/main/word.txt.xz"))

    vis = discord.ui.Label(text="Visibility", description="Public: the card is posted here. Private: it comes by direct message",
                           component=discord.ui.Select(options=[
                               discord.SelectOption(label="Public", value="public"),
                               discord.SelectOption(label="Private", value="private")]))

    def __init__(self, state, submit=False, visibility="public"):
        super().__init__()
        self.state, self.submit = state, submit
        for o in self.vis.component.options:                     # preset to the user's default
            o.default = o.value == visibility

    async def on_submit(self, interaction):
        files = self.file.component.values
        url = (self.link.component.value or "").strip() or None
        chosen = self.vis.component.values
        await handle_check(self.state, interaction, files[0] if files else None, url, self.submit, form=False,
                           visibility=chosen[0] if chosen else None)

    async def on_error(self, interaction, error):
        log.warning("the check form failed: %r", error)
        await safe(reply, interaction, ui.refusal_card("the form could not be read.", "Try /check again.", "Something went wrong"))


async def handle_panel_button(state, interaction):
    await interaction.response.send_modal(CheckModal(state, False, state.visibility_of(interaction.user.id)))


async def handle_panel(state, interaction):
    """/panel: post the message with the Check a word button in this channel (Manage Server only)."""
    gp = getattr(interaction.user, "guild_permissions", None)
    if not (gp and (getattr(gp, "manage_guild", False) or getattr(gp, "administrator", False))):
        await reply(interaction, ui.refusal_card("/panel needs the Manage Server permission.", "Ask a server admin.", "Not allowed"))
        return
    channel = getattr(interaction, "channel", None)
    try:
        await channel.send(view=ui.panel_card(), allowed_mentions=no_mentions())
    except Exception as e:
        log.warning("could not post the panel: %r", e)
        await reply(interaction, ui.refusal_card("I could not post in this channel.",
                                                  "The bot needs View Channel and Send Messages here.", "Could not post"))
        return
    await reply(interaction, ui.notice_card("Panel posted", "The button opens the same form as /check.", "valid"))

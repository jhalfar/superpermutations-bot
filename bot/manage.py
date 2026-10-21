"""manage.py - the history of checks (/history, paging, rename, delete) and the management of records entries (rename,
remove), with the rights of owner, staff and admin and a line in the audit channel for changes made by somebody else.
"""
import asyncio
import logging

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import discord

import history
import records
import ui
from cards import no_mentions, reply, safe, update_message, viewer_info
from downloads import clean_name

log = logging.getLogger("superperm")


def hist_masked(row, uid, admin):
    """staff see somebody else's private (or DM) check masked; the owner and admins see everything."""
    return row["visibility"] in ("private", "dm") and str(row["user_id"]) != str(uid) and not admin


def hist_rights(row, uid, staff, admin):
    """-> (can_view, can_delete, can_rename). Rename: the owner and admins; delete: the owner, staff and admins."""
    owner = str(row["user_id"]) == str(uid)
    return owner or staff or admin, owner or staff or admin, owner or admin


def hist_try(what, fn, *a, **kw):
    """a history write must never stop a check: log and go on."""
    try:
        return fn(*a, **kw)
    except Exception as e:
        log.warning("history %s failed: %r", what, e)
        return None


def hist_add(state, ctx, visibility, source):
    job = ctx.job
    ctx.hid = hist_try("add", history.add, state.cfg.history_path, job.id, ctx.user_id, ctx.user_name, visibility, job.name,
                       source, job.plan.kind, job.n)


def hist_finish(state, ctx):
    if ctx.hid is None or ctx.result is None:
        return
    res = ctx.result
    hist_try("finish", history.finish, state.cfg.history_path, ctx.hid, ui.verdict_of(res), n=res.get("n") or ctx.job.n,
             length=res.get("length"), sha256=res.get("sha256"), seconds=res.get("seconds"), kind=res.get("kind"))
    if ctx.added:
        hist_try("mark_added", history.mark_added, state.cfg.history_path, ctx.hid)


async def audit_say(state, text):
    """a line in the audit channel (if SUPERPERM_AUDIT_CHANNEL is set); never raises."""
    if not state.cfg.audit_channel_id or state.audit_channel is None:
        return
    try:
        ch = await state.audit_channel()
        await ch.send(view=ui.notice_card("Audit", text, "info"), allowed_mentions=no_mentions())
    except Exception as e:
        log.warning("could not write to the audit channel: %r", e)


def hist_view(state, tid, page, viewer):
    cfg = state.cfg
    uid, staff, admin = viewer
    total = history.count_user(cfg.history_path, tid)
    pages = max(1, -(-total // ui.HISTORY_PAGE))
    page = min(max(0, int(page)), pages - 1)
    rows = history.list_user(cfg.history_path, tid, page * ui.HISTORY_PAGE, ui.HISTORY_PAGE)
    masks = [hist_masked(r, uid, admin) for r in rows]
    return ui.history_card(rows, masks, page, pages, total, tid, cfg.history_days)


async def handle_history(state, interaction, target=None):
    """/history: your own checks (10 per page); /history user: for staff (private checks of others masked) and admins."""
    viewer = viewer_info(interaction)
    uid, staff, admin = viewer
    tid = target.id if target is not None else uid
    if tid != uid and not staff:
        await reply(interaction, ui.refusal_card("only moderators (Manage Messages) can look at somebody else's history.",
                                                 "Use /history without a user to see your own.", "Not allowed"))
        return
    await reply(interaction, hist_view(state, tid, 0, viewer))


class RenameModal(discord.ui.Modal, title="Rename"):
    """one text field: the shown file name of a history entry or of a records entry (1 to 80 characters)."""
    newname = discord.ui.Label(text="Shown file name", description="1 to 80 characters; letters, digits, . _ -",
                               component=discord.ui.TextInput(required=True, min_length=1, max_length=80))

    def __init__(self, state, kind, arg):
        super().__init__()
        self.state, self.kind, self.arg = state, kind, arg

    async def on_submit(self, interaction):
        await handle_rename(self.state, self.kind, self.arg, self.newname.component.value, interaction)

    async def on_error(self, interaction, error):
        log.warning("the rename form failed: %r", error)
        await safe(reply, interaction, ui.refusal_card("the form could not be read.", "Try again.", "Something went wrong"))


async def handle_rename(state, kind, arg, raw, interaction):
    """apply a rename from the modal: validate (1 to 80 characters), re-check the rights, clean like clean_name, audit."""
    raw = (raw or "").strip()
    if not 1 <= len(raw) <= 80:
        await reply(interaction, ui.refusal_card("the name must have 1 to 80 characters.", "Try again with a shorter name.", "Not renamed"))
        return
    new = clean_name(raw)
    uid, staff, admin = viewer_info(interaction)
    cfg = state.cfg
    if kind == "h":
        row = history.get(cfg.history_path, arg)
        if row is None or not hist_rights(row, uid, staff, admin)[2]:
            await reply(interaction, ui.notice_card("Not allowed", "Only the owner of an entry or an administrator can rename it."))
            return
        history.rename(cfg.history_path, row["hid"], new)
        if str(row["user_id"]) != str(uid):
            await audit_say(state, "<@%s> renamed history entry #%s of <@%s>" % (uid, row["hid"], row["user_id"]))
        await reply(interaction, ui.notice_card("Renamed", "History entry #%s is now `%s`." % (row["hid"], new), "valid"))
    else:
        n, r = arg.split(":", 1)
        table = records.load(cfg.records_path)
        e = records.find(table, n, r)
        if e is None or not records.permissions(table, n, e, uid, staff, admin)[1]:
            await reply(interaction, ui.notice_card("Not allowed", "Only the submitter of an entry or an administrator can rename it."))
            return
        records.set_label(cfg.records_path, n, r, new)
        if str(e.get("submitter_id") or "") != str(uid):
            await audit_say(state, "<@%s> renamed the entry of n = %s (%s letters) of %s to `%s`" % (
                uid, n, "{:,}".format(e["length"]), ui.safe_text(e["submitter"], 40), new))
        await reply(interaction, ui.notice_card("Renamed", "The entry is now shown as `%s`." % new, "valid"))


async def handle_x(state, kind, arg, value, interaction):
    """buttons and selects of the history and records cards (custom_id sp:x:KIND:ARG)."""
    cfg = state.cfg
    viewer = viewer_info(interaction)
    uid, staff, admin = viewer
    gone = lambda: ui.notice_card("Not found", "That entry is gone (deleted, or past the retention time).")
    if kind == "hp":                                           # Previous / Next
        tid, page = arg.split(":")
        if int(tid) != uid and not staff:
            await reply(interaction, ui.notice_card("Not allowed", "Only moderators can look at somebody else's history."))
            return
        await update_message(interaction, hist_view(state, int(tid), int(page), viewer))
    elif kind in ("hs", "hd", "hr"):
        row = history.get(cfg.history_path, value if kind == "hs" else arg)
        if row is None:
            await reply(interaction, gone())
            return
        can_view, can_del, can_ren = hist_rights(row, uid, staff, admin)
        if not can_view:
            await reply(interaction, ui.notice_card("Not allowed", "This entry belongs to somebody else."))
        elif kind == "hs":
            await reply(interaction, ui.history_entry_card(row, hist_masked(row, uid, admin), can_del, can_ren))
        elif kind == "hd":
            history.delete(cfg.history_path, row["hid"])
            if str(row["user_id"]) != str(uid):
                await audit_say(state, "<@%s> deleted history entry #%s of <@%s>" % (uid, row["hid"], row["user_id"]))
            await reply(interaction, ui.notice_card("Deleted", "History entry #%s is deleted." % row["hid"], "cancelled"))
        elif not can_ren:
            await reply(interaction, ui.notice_card("Not allowed", "Only the owner of an entry or an administrator can rename it."))
        else:
            await interaction.response.send_modal(RenameModal(state, "h", row["hid"]))
    elif kind in ("rm", "rd", "rr", "ry", "rn"):
        n, r = (arg, value) if kind == "rm" else arg.split(":", 1)
        table = records.load(cfg.records_path)
        e = records.find(table, n, r)
        if e is None:
            await reply(interaction, gone())
            return
        can_del, can_ren, is_record = records.permissions(table, n, e, uid, staff, admin)
        if kind == "rm":
            if can_del or can_ren:
                await reply(interaction, ui.record_manage_card(table, n, e, viewer))
            else:
                await reply(interaction, ui.notice_card("Not allowed", "You cannot change this entry."))
        elif kind == "rd":
            if can_del:
                await reply(interaction, ui.record_confirm_card(n, e, is_record))
            else:
                await reply(interaction, ui.notice_card("Not allowed", "The record of an n can be removed only by an administrator;"
                                                        " other entries by their submitter, a moderator or an administrator."))
        elif kind == "rr":
            if can_ren:
                await interaction.response.send_modal(RenameModal(state, "r", arg))
            else:
                await reply(interaction, ui.notice_card("Not allowed", "Only the submitter of an entry or an administrator can rename it."))
        elif kind == "ry":
            if not can_del:
                await reply(interaction, ui.notice_card("Not allowed", "You cannot remove this entry."))
                return
            records.remove(cfg.records_path, n, r)
            owner = str(e.get("submitter_id") or "") == str(uid)
            if is_record or not owner:
                await audit_say(state, "<@%s> removed %sthe entry of n = %s (%s letters, by %s) from the records" % (
                    uid, "the RECORD entry, " if is_record else "", n, "{:,}".format(e["length"]),
                    "<@%s>" % e["submitter_id"] if e.get("submitter_id") else ui.safe_text(e["submitter"], 40)))
            await reply(interaction, ui.notice_card("Removed", "The entry is removed from the records.", "cancelled"))
        else:
            await reply(interaction, ui.notice_card("Kept", "Nothing was removed.", "queued"))


async def history_loop(state):
    """delete history rows older than SUPERPERM_HISTORY_DAYS once a day (also done at start)."""
    while True:
        await asyncio.sleep(86400)
        await asyncio.to_thread(hist_try, "purge", history.purge, state.cfg.history_path, state.cfg.history_days)

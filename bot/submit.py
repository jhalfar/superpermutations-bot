"""submit.py - a verified word goes into the records table: `submit: true` or the Add to records button, the Contributor
role, the announcement of a new record.
"""
import logging
from types import SimpleNamespace

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import discord

import history
import records
import ui
from cards import reply, safe
from manage import hist_try

log = logging.getLogger("superperm")


async def grant_contributor(cfg, interaction):
    """give the submitter the Contributor role. -> text for the card, or None if no role is configured.
    Never raises, never removes a role."""
    if not cfg.contributor_role:
        return None
    member = interaction.user
    try:
        if any(getattr(r, "id", None) == cfg.contributor_role for r in getattr(member, "roles", None) or []):
            return "Contributor role: you already have it."
        guild = getattr(interaction, "guild", None)
        if guild is None or not hasattr(member, "add_roles"):
            raise RuntimeError("the command was not used on a server")
        role = guild.get_role(cfg.contributor_role)
        if role is None:
            role = discord.Object(id=cfg.contributor_role)
        await member.add_roles(role, reason="superpermutations-bot: verified a new record word")
        return "Contributor role given."
    except Exception as e:
        log.warning("could not give the Contributor role: %r", e)
        return ("I could not give the Contributor role (%s). The bot needs the Manage Roles permission and its own "
                "role must be above the Contributor role; ask a server admin." % (str(e)[:150] or type(e).__name__))


async def handle_add(state, ctx, interaction, confirmed=False):
    """the Add to records button: only the person who sent the file; the same as `submit: true`."""
    if interaction.user.id != ctx.user_id:
        await reply(interaction, ui.notice_card("Only the submitter", "Only the person who sent this word can add it to the records."))
        return
    if ctx.result is None or not ctx.result.get("ok"):
        await reply(interaction, ui.notice_card("Nothing to add", "Only a valid word can go into the records."))
        return
    if ctx.added or ctx.busy:
        await reply(interaction, ui.notice_card("Already added", "This word is already in the records."))
        return
    if ctx.private and not confirmed:
        where = "<#%d>" % state.cfg.channel_id if state.cfg.channel_id else "the records table"
        await reply(interaction, ui.confirm_publish_card(ctx.job, where))
        return
    ctx.busy = True
    try:
        await interaction.response.defer(ephemeral=True)
        lines = await do_submit(state, ctx, interaction)
        await ctx.card.update(lambda: state.card_view(ctx), wait=True)
        await reply(interaction, ui.notice_card("Records", "\n".join(lines), "valid"))
    finally:
        ctx.busy = False


async def role_actor(state, ctx, interaction):
    """who gets the Contributor role: the click interaction of a public check; for a private check the member and server
    saved when it was sent; for a check run in a direct message the user is looked up in the server of SUPERPERM_CHANNEL
    (fetch_member). None if that fails."""
    if not ctx.private:
        return interaction
    if ctx.guild is not None:
        return SimpleNamespace(user=ctx.member, guild=ctx.guild)
    if state.find_member is None:
        return None
    try:
        m = await state.find_member(ctx.user_id)
    except Exception as e:
        log.warning("could not find the user on the server: %r", e)
        return None
    return SimpleNamespace(user=m, guild=getattr(m, "guild", None)) if m is not None else None


async def may_submit(state, ctx, interaction):
    """True if this user may put a word into the table: always when SUPERPERM_SUBMIT_ROLE is not set, else only a member
    with that role (looked up as for the Contributor role; a server admin needs the role too)."""
    need = state.cfg.submit_role
    if not need:
        return True
    actor = await role_actor(state, ctx, interaction)
    roles = getattr(getattr(actor, "user", None), "roles", None) or []
    return any(getattr(r, "id", None) == need for r in roles)


async def do_submit(state, ctx, interaction):
    """enter the checked word into the table (like `submit: true`), give the role, announce a new record.
    -> lines for the card; sets ctx.added and ctx.extra."""
    cfg, res, job = state.cfg, ctx.result, ctx.job
    if not await may_submit(state, ctx, interaction):
        lines = ["Table: not added. Adding words to the records needs the role <@&%d> on this server." % cfg.submit_role]
        ctx.added = False
        ctx.extra = lines
        return lines
    out = records.submit(cfg.records_path, res, ctx.user_name, ctx.source, submitter_id=ctx.user_id, label=job.name)
    lines = [_submit_text(out, res)]
    if out["status"] == "new_record":
        role_text = None
        if cfg.contributor_role:
            actor = await role_actor(state, ctx, interaction)
            role_text = (await grant_contributor(cfg, actor)) if actor is not None else \
                "Contributor role not given: use the server (run the check there)."
        if role_text:
            lines.append(role_text)
        if state.announce:                         # a private check gets no link: the card is a direct message
            await safe(state.announce, ui.announcement_card(res["n"], res["length"], out["previous"], ctx.user_id,
                                                            ctx.user_name, job.name, res["sha256"],
                                                            None if ctx.private else ctx.card.url))
    ctx.added = out["status"] != "invalid"
    if ctx.added and ctx.hid is not None:
        hist_try("mark_added", history.mark_added, cfg.history_path, ctx.hid)
    ctx.extra = lines
    return lines


def _submit_text(out, res):
    s, prev = out["status"], out["previous"]
    if s == "new_record":
        t = "Table: **new record** for n = %d" % res["n"]
        if prev:
            t += " (%s letters shorter than %s)" % ("{:,}".format(prev["length"] - res["length"]), "{:,}".format(prev["length"]))
        else:
            t += " (first verified entry)"
        if out.get("claim"):
            t += "; a shorter unverified claim exists: {:,}".format(out["claim"]["length"])
        return t
    return {"equal": "Table: same length as the record, added as another entry.",
            "longer": "Table: longer than the current record (%s), added to the list." %
                      ("{:,}".format(prev["length"]) if prev else "-"),
            "duplicate": "Table: this word is already listed.",
            "invalid": "Table: not added."}[s]

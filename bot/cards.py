"""cards.py - messages out: the job card that is posted once and then edited (Card), its refresh pace and loop, the
ephemeral replies, and who the user is (display name, staff, admin). The cards themselves are built by ui.py.
"""
import asyncio
import logging
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import discord

import ui

log = logging.getLogger("superperm")


def no_mentions():
    return discord.AllowedMentions.none()


def display_name(user):
    """the name people see on the server (not the login name)."""
    for attr in ("display_name", "global_name", "name"):
        v = getattr(user, attr, None)
        if v:
            return str(v)
    return str(getattr(user, "id", "unknown"))


def is_admin(user):
    return bool(getattr(getattr(user, "guild_permissions", None), "administrator", False))


def may_cancel_any(user):
    gp = getattr(user, "guild_permissions", None)
    return bool(gp and (getattr(gp, "administrator", False) or getattr(gp, "manage_messages", False)))


def viewer_info(interaction):
    """(user id, staff, admin) of whoever pressed or typed. Staff = Manage Messages (or Administrator) in the permissions that
    Discord sends with the interaction; admin = Administrator. In a direct message both are False."""
    u = interaction.user
    return u.id, may_cancel_any(u), is_admin(u)


async def safe(coro_fn, *a, **kw):
    """run a message call; a failed post never stops the job, but it is logged (the reason is never lost). -> result or None."""
    try:
        return await coro_fn(*a, **kw)
    except Exception as e:
        log.warning("%s failed: %r", getattr(coro_fn, "__qualname__", coro_fn), e)
        return None


async def reply(interaction, view, delete_after=None):
    """an ephemeral card as the answer to an interaction (the first response, or a follow-up if it was already answered).
    delete_after: seconds after which the card removes itself (Details and similar look-ups, so they do not pile up)."""
    if interaction.response.is_done():
        msg = await interaction.followup.send(view=view, ephemeral=True, allowed_mentions=no_mentions(), wait=True)
        if delete_after and msg is not None and hasattr(msg, "delete"):
            asyncio.ensure_future(_delete_later(msg, delete_after))
        return msg
    kw = {"delete_after": delete_after} if delete_after else {}
    return await interaction.response.send_message(view=view, ephemeral=True, allowed_mentions=no_mentions(), **kw)


async def _delete_later(msg, seconds):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception as e:                       # already dismissed by the user, or the token expired
        log.debug("could not remove an ephemeral card: %r", e)


async def update_message(interaction, view):
    await interaction.response.edit_message(view=view, allowed_mentions=no_mentions())


class Card:
    """the one message of a job: sent once with channel.send (bot token: editable for hours), then edited. Edits are at most
    one per min_edit_s (the first post does not count); a progress refresh (wait=False) is skipped when it is too early or
    changes nothing, a state change (wait=True) waits its turn. The view is made by a function that runs when the edit is due,
    so a late edit always shows the newest state. If the message is gone (deleted) a new card is posted once; other failures
    are logged."""

    def __init__(self, channel, min_edit_s=5.0, clock=time.monotonic):
        self.channel, self.min_edit_s, self.clock = channel, min_edit_s, clock
        self.msg, self.sig, self.last_edit, self.reposted = None, None, -1e9, False
        self.lock = asyncio.Lock()

    @property
    def url(self):
        return getattr(self.msg, "jump_url", None)

    async def post(self, view):
        self.msg = await self.channel.send(view=view, allowed_mentions=no_mentions())
        self.sig = ui.signature(view)

    async def update(self, make_view, wait=False):
        """make_view() -> the card to show. -> True if the message now shows it."""
        async with self.lock:
            if self.msg is None:
                return False
            view = make_view()
            sig = ui.signature(view)
            if sig == self.sig:
                return True
            gap = self.min_edit_s - (self.clock() - self.last_edit)
            if gap > 0:
                if not wait:
                    return False
                await asyncio.sleep(gap)
                view = make_view()
                sig = ui.signature(view)
                if sig == self.sig:
                    return True
            try:
                await self.msg.edit(view=view, allowed_mentions=no_mentions())
            except discord.NotFound as e:
                log.warning("the card message is gone (%r)%s", e, "; not posting it again" if self.reposted else "")
                if self.reposted:
                    return False
                self.reposted = True
                try:
                    await self.post(view)
                    self.last_edit = self.clock()
                except Exception as e2:
                    log.warning("could not post the card again: %r", e2)
                    return False
                return True
            except Exception as e:
                log.warning("could not edit the card: %r", e)
                return False
            self.sig, self.last_edit = sig, self.clock()
            return True


async def side_cards(state, ctx):
    """the note in the channel of a private check and the audit copy: updated at state changes only."""
    if ctx.trace is not None:
        await ctx.trace.update(lambda: state.trace_view(ctx), wait=True)
    if ctx.audit is not None:
        await ctx.audit.update(lambda: state.card_view(ctx, audit=True), wait=True)


def refresh_interval(job, cfg, now=None):
    """seconds until the next refresh of a job card: about a twentieth of the time the job still needs, between
    progress_min_s and progress_s, so a check of a minute moves every few seconds and one of hours about once a minute.
    A waiting card is refreshed every 15 s at most (its place in the queue changes)."""
    now = now or time.time()
    hi = cfg.progress_s
    lo = min(cfg.progress_min_s, hi)
    if job.status in ("queued", "starting"):
        return max(lo, min(hi, 15.0))
    left = None
    if job.t_start:
        p, k, letters = job.progress
        length = job.plan.length or job.plan.est_len
        if length:
            done = (max(1, p) - 1) * length + letters
            total = max(1, k) * length
            el = now - job.t_start
            if done > 0 and el > 1:
                left = el * max(0, total - done) / done
    if left is None:
        left = job.plan.est_total_s or hi
    return max(lo, min(hi, left / 20.0))


async def card_loop(state, ctx):
    """refresh the card while the job waits or runs (the place in the queue changes, the bar moves), at the pace of
    refresh_interval. Unchanged cards and edits that come too early are skipped by Card.update."""
    job = ctx.job
    while job.status in ("queued", "starting", "running"):
        await asyncio.sleep(refresh_interval(job, state.cfg))
        if job.status in ("queued", "starting", "running"):
            await ctx.card.update(lambda: state.card_view(ctx))

"""state.py - the state of the running bot in memory: the scheduler, the job cards whose buttons still work (JobCtx),
and the hooks the client sets (announce, audit channel, member lookup). A restart drops all of it.
"""
from collections import OrderedDict

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import history
import settings
import ui
from config import Config
from downloads import default_fetch
from jobs import Scheduler, subprocess_runner


class JobCtx:
    """everything the buttons of one job card need, in memory (a restart drops it)."""

    def __init__(self, job, card, source, user_id, user_name):
        self.job, self.card, self.source = job, card, source
        self.user_id, self.user_name = user_id, user_name
        self.result = None
        self.extra = []            # lines under the result (table, role)
        self.can_add = False
        self.added = False
        self.busy = False          # an Add to records is under way
        self.tasks = []            # background card updates
        self.private = False       # the card is a direct message
        self.hid = None            # the row of this job in the history
        self.member, self.guild = None, None   # who sent it and where (to give the role later)
        self.trace = None          # Card: the small note in the channel of a private check
        self.audit = None          # Card: the copy in the audit channel


class State:
    def __init__(self, cfg=None, runner=None, fetch=default_fetch):
        self.cfg = cfg or Config()
        self.sched = Scheduler(runner or subprocess_runner(self.cfg), self.cfg)
        try:                     # job numbers go on after a restart (the history keeps the last one)
            self.sched._next = history.last_job_id(self.cfg.history_path) + 1
        except Exception:
            pass
        self.fetch = fetch
        self.announce = None     # async callable(view), set by the client when a channel is configured
        self.audit_channel = None    # async callable() -> channel of SUPERPERM_AUDIT_CHANNEL, set by the client
        self.find_member = None      # async callable(user_id) -> Member of the records channel's server, or raises
        self.cards = OrderedDict()   # job id -> JobCtx, the newest cfg.card_keep

    def remember(self, ctx):
        self.cards[ctx.job.id] = ctx
        for jid in list(self.cards):                 # oldest first; a live job is never forgotten
            if len(self.cards) <= self.cfg.card_keep:
                break
            if jid not in self.sched.jobs:
                del self.cards[jid]

    def visibility_of(self, user_id):
        """'public' or 'private': the saved default of a user, else cfg.default_visibility."""
        return settings.get_visibility(self.cfg.settings_path, user_id, self.cfg.default_visibility)

    def card_view(self, ctx, now=None, audit=False):
        """the current card of a job, from its state."""
        job = ctx.job
        if job.id in self.sched.jobs:
            pos = self.sched.position(job)
        else:                                     # not admitted yet (a private card is sent first)
            lane = self.sched.lanes[job.lane]
            pos = len(lane.waiting) + 1 if (lane.running or lane.waiting) else 0
        return ui.job_card(job, ctx.result, now=now, position=pos, extra=ctx.extra,
                           can_add=ctx.can_add and not ctx.added, added=ctx.added, audit=audit)

    def trace_view(self, ctx):
        phase = "finished" if ctx.result is not None else "running" if ctx.job.status == "running" else "queued"
        return ui.trace_card(ctx.job, phase)

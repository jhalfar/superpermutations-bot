"""jobs.py - what happens to an accepted file: the plan (n, shards, time estimate, refusals above the limits), the job,
the two lanes with one worker each (Scheduler), and the worker itself: checker/check_any.py in a child process under the
memory limit (limits.py), whose progress lines are read and which is killed on cancel or timeout (supervise).
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import check_any
import decode
import limits
import spra
import ui
from config import CHECK_ANY, LANES, SLOW_FROM_N, UserError
from ui import fmt_dur

log = logging.getLogger("superperm")


class Plan:
    """what is known about a submission before it is queued."""

    def __init__(self, kind, n, length, shards, est_len, est_pass_s, est_total_s):
        self.kind, self.n, self.length, self.shards = kind, n, length, shards
        self.est_len, self.est_pass_s, self.est_total_s = est_len, est_pass_s, est_total_s

    @property
    def lane(self):
        return "slow" if self.n >= SLOW_FROM_N else "fast"


def plan_job(path, cfg):
    """kind, n, length from the file header; refuse (UserError) what is above the limits: n, memory (number of shards),
    estimated time. Blocking (reads the recipe header or the first symbols of a word)."""
    try:
        kind, n, length = check_any.peek_full(path)
    except spra.SpraError as e:
        raise UserError("SPRA file refused: %s" % e, title="File refused",
                        hint="Nothing was run. Check that the file is complete and in the documented format.")
    except decode.RecipeError as e:
        raise UserError("recipe refused: %s" % e, title="File refused",
                        hint="Nothing was run. The recipe format is described in docs/RECIPE.md.")
    except (OSError, ValueError, EOFError) as e:
        raise UserError("cannot read the file: %s" % e, hint="Send a plain word, a SPRA file or a recipe.")
    if n < 2:
        raise UserError("found %d different symbols; a word needs n >= 2 of them" % n,
                        hint="Send a word with at least two different symbols.")
    if n > cfg.max_n:
        raise UserError("n = %d is above this bot's limit (n %s %d). Nothing was run." % (n, ui.LE, cfg.max_n),
                        hint="Send a word with n %s %d." % (ui.LE, cfg.max_n))
    if kind in ("spra", "recipe") and length and length > n * check_any.factorial(n):
        # a SPRA header or a recipe states its length; writing all n! permutations one after the other gives n * n!
        # letters, so no word worth checking is longer (this also stops a tiny file that claims a huge length)
        raise UserError("the file says the word has %s letters, more than n x n! = %s for n = %d. Nothing was run." % (
            "{:,}".format(length), "{:,}".format(n * check_any.factorial(n)), n),
            hint="Every permutation written out one after another is already shorter; check the file.")
    try:
        k = check_any.plan_shards(n, cfg.mem_gb, cfg.max_shards, recipe=(kind == "recipe"))
    except ValueError as e:
        raise UserError(str(e), hint="Nothing was run. The owner can raise SUPERPERM_MEM_GB or SUPERPERM_MAX_SHARDS.")
    est_len = length or check_any.estimate_letters(n)
    one, total = check_any.estimate_seconds(est_len, k, cfg.mbps)
    if total > cfg.max_hours * 3600:
        raise UserError("estimated time %s (%s letters, %d pass%s at %g M letters/s) is above the limit of %g h "
                        "(SUPERPERM_MAX_HOURS). Nothing was run." % (
                            fmt_dur(total), "{:,}".format(est_len), k, "" if k == 1 else "es", cfg.mbps, cfg.max_hours),
                        hint="Send a smaller n; the owner can raise SUPERPERM_MAX_HOURS.")
    return Plan(kind, n, length, k, est_len, one, total)


class Job:
    def __init__(self, jid, user_id, user_name, name, path, plan):
        self.id, self.user_id, self.user_name, self.name, self.path, self.plan = jid, user_id, user_name, name, path, plan
        self.n, self.lane, self.shards = plan.n, plan.lane, plan.shards
        self.status = "queued"          # queued | starting | running | done | cancelled
        self.t_queued, self.t_start = time.time(), None
        self.progress = (0, plan.shards, 0)         # pass (from 1), passes, letters of the pass
        self.cancel_event = threading.Event()
        self.private = False            # the card goes by direct message (a note in the channel), see handle_check
        self.cancelled_by = None
        self.ready = None               # asyncio.Event: the handler has posted the card and the worker may start
        self.fut = None
        self.on_start = None            # async callable(job), set by the handler

    def cancel(self, by):
        self.cancelled_by = by
        self.cancel_event.set()


def kill_child(p):
    """kill the check_any process and, through the job object of guard.py (Windows) or the process group, its children."""
    try:
        if os.name == "nt":
            p.kill()     # guard.py is the job's owner; its job has KILL_ON_JOB_CLOSE, so the whole tree goes
        else:
            import signal
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def supervise(cmd, job, cfg, mem_gb=None, poll=0.2):
    """run cmd (check_any.py ... --progress) with the memory limit; read its progress lines into job.progress; kill it on
    cancel, when a pass takes longer than pass_factor x its estimate (+ start-up), or after max_hours in all.
    Returns the result dict of the child, or {"ok": False, "cancelled"|"timeout": True, "error": ...}."""
    kw = {"stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name != "nt":
        kw["start_new_session"] = True
    p = limits.popen(cmd, mem_gb=mem_gb, **kw)
    result = []
    state = {"pass": None, "t_pass": time.time()}

    def reader():
        for raw in p.stdout:
            line = raw.decode(errors="replace").strip()
            if line.startswith("#P "):
                try:
                    d = json.loads(line[3:])
                    job.progress = (int(d["pass"]), int(d["passes"]), int(d["letters"]))
                except (ValueError, KeyError, TypeError):
                    continue
                if d["pass"] != state["pass"]:
                    state["pass"], state["t_pass"] = d["pass"], time.time()
            elif line.startswith("{"):
                result.append(line)
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    t0 = time.time()
    total_limit = cfg.max_hours * 3600
    failure = None
    try:
        while th.is_alive():
            th.join(poll)
            now = time.time()
            if job.cancel_event.is_set():
                failure = {"cancelled": True, "error": "cancelled by %s" % (job.cancelled_by or "the owner")}
            elif now - t0 > total_limit:
                failure = {"timeout": True, "error": "timed out: more than %g h in all (SUPERPERM_MAX_HOURS)" %
                           cfg.max_hours}
            else:
                lim = cfg.pass_factor * job.plan.est_pass_s + cfg.startup_s
                if now - state["t_pass"] > lim:
                    failure = {"timeout": True, "error": "timed out: pass %s/%d took more than %s (%g x its estimate of "
                               "%s, plus start-up); the word is longer or the machine slower than assumed" % (
                                   state["pass"] or 1, job.shards, fmt_dur(lim), cfg.pass_factor,
                                   fmt_dur(job.plan.est_pass_s))}
            if failure:
                kill_child(p)
                break
    finally:
        if failure:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill_child(p)
        th.join(10)
        p.wait()
        try:
            p.stdout.close()
        except OSError:
            pass
    if failure:
        failure.update({"ok": False, "valid": False, "seconds": round(time.time() - t0, 1)})
        return failure
    if not result:
        return {"ok": False, "valid": False, "error": "the checker process ended without a result (code %s; probably "
                "out of memory)" % p.returncode}
    return json.loads(result[-1])


def subprocess_runner(cfg):
    """-> function(path, job=None) -> result dict; runs check_any.py in a child process under the memory limit."""
    def run(path, job=None):
        if job is None:      # direct call (tests, scripts): plan here
            try:
                plan = plan_job(path, cfg)
            except UserError as e:
                return {"ok": False, "valid": False, "error": str(e)}
            job = Job(0, 0, "", os.path.basename(path), path, plan)
        cmd = [sys.executable, "-I", CHECK_ANY, path, "--threads", str(cfg.threads),
               "--shards", str(job.shards), "--progress"]
        return supervise(cmd, job, cfg, mem_gb=cfg.mem_gb)
    return run


class Lane:
    def __init__(self, name):
        self.name, self.waiting, self.running = name, [], None
        self.wake, self.task = None, None


class Scheduler:
    """two lanes (fast: n <= 12, slow: n = 13, 14), one worker each. Per user at most one job waiting or running per
    lane; at most queue_len waiting per lane. In memory only."""

    def __init__(self, runner, cfg):
        self.runner, self.cfg = runner, cfg
        self.lanes = {name: Lane(name) for name in LANES}
        self.jobs = {}            # id -> Job, while waiting or running
        self._next = 1

    def start(self):
        for lane in self.lanes.values():
            if lane.task is None or lane.task.done():
                lane.wake = asyncio.Event()      # bound to the running loop
                lane.task = asyncio.ensure_future(self._loop(lane))

    def new_job(self, user_id, user_name, name, path, plan):
        job = Job(self._next, user_id, user_name, name, path, plan)
        self._next += 1
        return job

    def check_admit(self, job):
        """UserError if the user already has a job in the lane or the lane is full (nothing is changed)."""
        lane = self.lanes[job.lane]
        if any(j.user_id == job.user_id for j in lane.waiting + ([lane.running] if lane.running else [])):
            raise UserError("you already have a check waiting or running in the %s lane." % lane.name,
                            hint="Wait for it to finish, or cancel it with /cancel or its Cancel button.")
        if len(lane.waiting) >= self.cfg.queue_len:
            raise UserError("the %s lane is full (%d waiting)." % (lane.name, self.cfg.queue_len),
                            hint="Try again in a while; /queue shows the lanes.")

    def admit(self, job):
        """put the job in its lane; UserError if the user already has one there or the lane is full.
        -> number of jobs ahead (running + waiting)."""
        self.check_admit(job)
        lane = self.lanes[job.lane]
        self.start()
        job.ready = asyncio.Event()
        job.fut = asyncio.get_running_loop().create_future()
        ahead = len(lane.waiting) + (1 if lane.running else 0)
        lane.waiting.append(job)
        self.jobs[job.id] = job
        lane.wake.set()
        return ahead

    def position(self, job):
        """0 = running or about to run; else the 1-based place among the waiting jobs."""
        lane = self.lanes[job.lane]
        return lane.waiting.index(job) + 1 if job in lane.waiting else 0

    def cancel(self, job, by):
        """cancel a waiting job at once; a running one is killed by its supervisor within a moment."""
        job.cancel(by)
        lane = self.lanes[job.lane]
        if job in lane.waiting:
            lane.waiting.remove(job)
            self._finish(job, {"ok": False, "valid": False, "cancelled": True, "error": "cancelled by %s" % by})

    def _finish(self, job, res):
        job.status = "cancelled" if res.get("cancelled") else "done"
        self.jobs.pop(job.id, None)
        if job.fut is not None and not job.fut.done():
            job.fut.set_result(res)

    async def _loop(self, lane):
        while True:
            while not lane.waiting:
                lane.wake.clear()
                await lane.wake.wait()
            job = lane.waiting.pop(0)
            lane.running = job
            job.status = "starting"
            try:
                await job.ready.wait()            # the handler has posted the card
                if job.cancel_event.is_set():
                    res = {"ok": False, "valid": False, "cancelled": True,
                           "error": "cancelled by %s" % (job.cancelled_by or "the owner")}
                else:
                    job.status, job.t_start = "running", time.time()
                    if job.on_start:
                        try:
                            await job.on_start(job)
                        except Exception as e:    # a failed card update must not stop the check
                            log.warning("job #%d: could not update the card at the start: %r", job.id, e)
                    res = await asyncio.to_thread(self.runner, job.path, job)
            except Exception as e:                # the runner failed: tell the user, keep the loop alive
                log.warning("job #%d: the runner failed: %r", job.id, e, exc_info=True)
                res = {"ok": False, "valid": False, "error": "internal error: %s" % e}
            finally:
                lane.running = None
            self._finish(job, res)

    async def stop(self):
        for lane in self.lanes.values():
            if lane.task:
                lane.task.cancel()

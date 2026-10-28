"""config.py - the settings of the bot (Config, read from the SUPERPERM_* environment), its constants and UserError.

Config(**kw) takes every setting as a keyword (the tests do that) and falls back to the environment, then to the default.
README.md and docs/REFERENCE.md list the variables.
"""
import os
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import board
import check_any
import history
import records
import registry
import settings


HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_ANY = os.path.join(os.path.dirname(HERE), "checker", "check_any.py")
ALLOWED_HOSTS = {"github.com", "raw.githubusercontent.com", "gitlab.com"}
REDIRECT_HOSTS = ALLOWED_HOSTS | {"objects.githubusercontent.com", "release-assets.githubusercontent.com",
                                  "media.githubusercontent.com"}
ALLOWED_EXT = (".splan.json.xz", ".splan.json", ".spra.xz", ".spra", ".txt.xz", ".txt")
LANES = ("fast", "slow")
SLOW_FROM_N = 13          # n >= 13 goes to the slow lane
VERSION = "1.2.1"
BIG_HINT = "Put the file on github.com or gitlab.com and give the link instead."


class UserError(Exception):
    """a problem to be told to the user in plain words: str(e) is the reason (one line), hint what to do about it."""

    def __init__(self, msg, hint=None, title="Not accepted"):
        super().__init__(msg)
        self.hint, self.title = hint, title


def _env_num(name, default, kind=float):
    v = os.environ.get(name)
    if v in (None, ""):
        return default
    try:
        return kind(v)
    except ValueError:
        sys.exit("%s must be a number, got %r" % (name, v))


class Config:
    def __init__(self, **kw):
        env = os.environ.get
        self.records_path = kw.get("records_path") or env("SUPERPERM_RECORDS") or records.DEFAULT
        ch = kw.get("channel_id", env("SUPERPERM_CHANNEL"))
        self.channel_id = int(ch) if ch else None
        role = kw.get("contributor_role", env("SUPERPERM_CONTRIBUTOR_ROLE"))
        self.contributor_role = int(role) if role else None
        sr = kw.get("submit_role", env("SUPERPERM_SUBMIT_ROLE"))
        self.submit_role = int(sr) if sr else None              # only members with this role may add to the records
        ac = kw.get("audit_channel_id", env("SUPERPERM_AUDIT_CHANNEL"))
        self.audit_channel_id = int(ac) if ac else None            # a copy of every private check goes there (optional)
        bd = kw.get("board", env("SUPERPERM_BOARD"))
        self.board = str(bd).lower() in ("1", "true", "yes", "on") if bd is not None else False   # table as last message
        self.board_path = kw.get("board_path") or env("SUPERPERM_BOARD_FILE") or board.default_path(self.records_path)
        try:                                                        # n -> role pinged by a new record (optional)
            self.n_roles = board.parse_n_roles(kw.get("n_roles", env("SUPERPERM_N_ROLES") or ""))
        except ValueError as e:
            sys.exit(str(e))
        self.settings_path = kw.get("settings_path") or env("SUPERPERM_SETTINGS") or settings.default_path(self.records_path)
        self.default_visibility = kw.get("default_visibility", settings.DEFAULT)      # for users who never chose
        self.history_path = kw.get("history_path") or env("SUPERPERM_HISTORY") or history.default_path(self.records_path)
        self.history_days = float(kw.get("history_days", _env_num("SUPERPERM_HISTORY_DAYS", 180, int)))
        self.max_download = int(kw.get("max_download", 64 << 20))       # bytes of a downloaded or attached file
        self.download_total_s = float(kw.get("download_total_s", 120))
        self.download_read_s = float(kw.get("download_read_s", 20))
        self.mem_gb = float(kw.get("mem_gb", _env_num("SUPERPERM_MEM_GB", 14.0)))
        self.max_n = min(int(kw.get("max_n", _env_num("SUPERPERM_MAX_N", 14, int))), registry.MAX_N)
        self.mbps = float(kw.get("mbps", _env_num("SUPERPERM_MBPS", check_any.DEFAULT_MBPS)))
        self.max_hours = float(kw.get("max_hours", _env_num("SUPERPERM_MAX_HOURS", 6.0)))
        self.queue_len = int(kw.get("queue_len", _env_num("SUPERPERM_QUEUE", 6, int)))        # waiting per lane
        self.max_shards = int(kw.get("max_shards", _env_num("SUPERPERM_MAX_SHARDS", 8, int)))
        self.pass_factor = float(kw.get("pass_factor", 1.5))     # a pass is killed at pass_factor x its estimate ...
        self.startup_s = float(kw.get("startup_s", 30.0))        # ... plus this much for process start-up
        self.progress_s = float(kw.get("progress_s", 60.0))      # longest gap between two refreshes of a job card
        self.progress_min_s = float(kw.get("progress_min_s", 5.0))   # shortest gap (short checks move every few seconds)
        self.min_edit_s = float(kw.get("min_edit_s", 5.0))       # and a message is never edited more often than this
        self.card_keep = int(kw.get("card_keep", 300))           # finished job cards whose buttons still work (in memory)
        self.threads = int(kw.get("threads", 1))
        self.workdir = kw.get("workdir")

"""history.py - the history of checks, in a SQLite file (stdlib sqlite3).

One row per accepted job: job id, user id, display name, time (UTC, unix seconds), visibility (public | private | dm), the
file name as shown to the user, the source ("attachment" or the URL), kind, n, length, SHA-256 of the letters, verdict
(pending | valid | invalid | refused | cancelled | stopped | error), seconds, and whether the word was added to the
records. Never the word itself (the file is deleted right after the check). Rows older than SUPERPERM_HISTORY_DAYS are
deleted at start and once a day (purge). The file is SUPERPERM_HISTORY (default history.sqlite next to the records table).
Each call opens its own short connection, so it is safe from several threads.
"""
import os
import sqlite3
import threading
import time

VERDICTS = ("pending", "valid", "invalid", "refused", "cancelled", "stopped", "error")
_lock = threading.Lock()
COLUMNS = ("hid", "job_id", "user_id", "user_name", "t", "visibility", "file_name", "source", "kind", "n", "length", "sha256",
           "verdict", "seconds", "added")


def default_path(records_path):
    return os.path.join(os.path.dirname(os.path.abspath(records_path)), "history.sqlite")


def _connect(path):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS checks (
        hid INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, user_id TEXT NOT NULL, user_name TEXT, t INTEGER NOT NULL,
        visibility TEXT, file_name TEXT, source TEXT, kind TEXT, n INTEGER, length INTEGER, sha256 TEXT,
        verdict TEXT NOT NULL DEFAULT 'pending', seconds REAL, added INTEGER NOT NULL DEFAULT 0)""")
    con.execute("CREATE INDEX IF NOT EXISTS checks_user ON checks (user_id, t)")
    return con


def _run(path, sql, args=(), fetch=None, write=False):
    with _lock:
        con = _connect(path)
        try:
            cur = con.execute(sql, args)
            out = None
            if fetch == "all":
                out = [dict(r) for r in cur.fetchall()]
            elif fetch == "one":
                r = cur.fetchone()
                out = dict(r) if r else None
            elif fetch == "id":
                out = cur.lastrowid
            elif fetch == "count":
                out = cur.rowcount
            if write:
                con.commit()
            return out
        finally:
            con.close()


def add(path, job_id, user_id, user_name, visibility, file_name, source, kind, n, now=None):
    """a new row for an accepted job (verdict pending). -> hid."""
    return _run(path, "INSERT INTO checks (job_id, user_id, user_name, t, visibility, file_name, source, kind, n) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, str(user_id), user_name, int(now if now is not None else time.time()), visibility, file_name, source,
                 kind, n), fetch="id", write=True)


def finish(path, hid, verdict, n=None, length=None, sha256=None, seconds=None, kind=None):
    if verdict not in VERDICTS:
        raise ValueError("unknown verdict %r" % verdict)
    _run(path, "UPDATE checks SET verdict=?, n=COALESCE(?, n), length=?, sha256=?, seconds=?, kind=COALESCE(?, kind) WHERE hid=?",
         (verdict, n, length, sha256, seconds, kind, hid), write=True)


def last_job_id(path):
    """the largest job number in the history (0 if none): job numbers go on from there after a restart."""
    row = _run(path, "SELECT MAX(job_id) AS m FROM checks", fetch="one")
    return int(row["m"] or 0) if row else 0


def mark_added(path, hid):
    _run(path, "UPDATE checks SET added=1 WHERE hid=?", (hid,), write=True)


def get(path, hid):
    return _run(path, "SELECT * FROM checks WHERE hid=?", (int(hid),), fetch="one")


def get_job(path, job_id):
    """the newest row of a job number (job numbers go on after a restart, so there is normally one), or None."""
    return _run(path, "SELECT * FROM checks WHERE job_id=? ORDER BY hid DESC LIMIT 1", (int(job_id),), fetch="one")


def count_user(path, user_id):
    return _run(path, "SELECT COUNT(*) AS c FROM checks WHERE user_id=?", (str(user_id),), fetch="one")["c"]


def list_user(path, user_id, offset=0, limit=10):
    """the user's checks, newest first."""
    return _run(path, "SELECT * FROM checks WHERE user_id=? ORDER BY t DESC, hid DESC LIMIT ? OFFSET ?",
                (str(user_id), int(limit), int(offset)), fetch="all")


def delete(path, hid):
    return _run(path, "DELETE FROM checks WHERE hid=?", (int(hid),), fetch="count", write=True)


def rename(path, hid, file_name):
    return _run(path, "UPDATE checks SET file_name=? WHERE hid=?", (file_name, int(hid)), fetch="count", write=True)


def purge(path, days, now=None):
    """delete rows older than `days` days; rows still 'pending' from before a restart become 'error'. -> rows deleted."""
    now = int(now if now is not None else time.time())
    _run(path, "UPDATE checks SET verdict='error' WHERE verdict='pending' AND t < ?", (now - 3600,), write=True)
    return _run(path, "DELETE FROM checks WHERE t < ?", (now - int(days * 86400),), fetch="count", write=True)

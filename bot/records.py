"""records.py - the table of the shortest known explicit words per n (records.json).

Entry: {length, sha256 | null, submitter, source, date, verified, kind, note}.
  verified true  : this bot (check_any.py) streamed the word and found every permutation; sha256 is of the letters.
  verified false : a published claim (no word available to the bot, or not yet re-checked).
Per n the table keeps a list of entries, shortest first. `best_verified` is the record of the bot; `best_known` may be
a shorter unverified claim. Only a verified entry can change the record.

    python -I bot/records.py show [records.json]
    python -I bot/records.py md [records.json]
    python -I bot/records.py add RECORDS.json RESULT.json SUBMITTER SOURCE      (RESULT.json = output of checker/check_any.py)
"""
import json
import os
import sys
import threading
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "records.json")
FORMAT = "superperm-records/1"
_lock = threading.Lock()
MAX_PER_N = 50


def load(path=DEFAULT):
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
    except FileNotFoundError:
        return {"format": FORMAT, "records": {}}
    if t.get("format") != FORMAT:
        raise ValueError("%s is not a %s table" % (path, FORMAT))
    return t


def save(table, path=DEFAULT):
    table["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(table, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def entries(table, n):
    return sorted(table["records"].get(str(n), []), key=lambda e: (e["length"], not e["verified"], e["date"]))


def best_verified(table, n):
    for e in entries(table, n):
        if e["verified"]:
            return e
    return None


def best_known(table, n):
    e = entries(table, n)
    return e[0] if e else None


def would_enter(table, n, length):
    """True if a verified word of this n and length would become the record (shorter than the verified record, or the
    first verified entry of its n)."""
    best = best_verified(table, n)
    return best is None or length < best["length"]


def submit(path, result, submitter, source, date=None, note=None, submitter_id=None, label=None, now=None,
           checked_by=None, checked_by_id=None):
    """add a checked result (the dict of check_any). Returns {status, entry, previous, claim, for_other}.
    submitter is the display name; submitter_id (the Discord user id, optional) is kept in the entry next to it; label is
    the file name shown for the entry; a new entry also gets "time" (unix seconds, UTC) next to "date".
    checked_by, checked_by_id: who ran the check when the word is somebody else's (the administrators' option holder of
    /check: submitter is then the holder's name and has no id). A claim of the same length becomes verified and keeps its
    holder; the person who ran the check goes into checked_by. for_other is True when the holder of the entry is not the
    person who ran the check (no Contributor role, another announcement).
    status: invalid | duplicate | new_record | equal | longer.  The table file is only written for a new entry."""
    if not result.get("ok") or not result.get("sha256"):
        return {"status": "invalid", "entry": None, "previous": None, "claim": None, "for_other": False}
    n, length, sha = int(result["n"]), int(result["length"]), result["sha256"]
    date = date or time.strftime("%Y-%m-%d")
    with _lock:
        table = load(path)
        prev = best_verified(table, n)
        for e in table["records"].get(str(n), []):
            if e["length"] == length and e.get("sha256") in (sha, None):
                if e.get("sha256") is None:       # a claim for this length: now verified, its holder stays
                    e.update({"sha256": sha, "verified": True, "kind": result.get("kind", "word")})
                    cid = checked_by_id or submitter_id
                    own = bool(cid) and str(e.get("submitter_id") or "") == str(cid)
                    if not own:
                        e["checked_by"] = str(checked_by or submitter)[:80]
                        if cid:
                            e["checked_by_id"] = str(cid)
                    save(table, path)
                    status = "new_record" if prev is None or length < prev["length"] else                         ("equal" if length == prev["length"] else "longer")
                    return {"status": status, "entry": e, "previous": prev, "claim": None, "for_other": not own}
                return {"status": "duplicate", "entry": e, "previous": prev, "claim": None, "for_other": False}
        e = {"length": length, "sha256": sha, "submitter": submitter, "source": source, "date": date,
             "verified": True, "kind": result.get("kind", "word")}
        if note:
            e["note"] = note
        if submitter_id:
            e["submitter_id"] = str(submitter_id)
        if checked_by:
            e["checked_by"] = str(checked_by)[:80]
        if checked_by_id:
            e["checked_by_id"] = str(checked_by_id)
        if label:
            e["label"] = str(label)[:80]
        e["time"] = int(now if now is not None else time.time())
        if result.get("generator"):
            e["generator"] = result["generator"]
        lst = table["records"].setdefault(str(n), [])
        lst.append(e)
        lst.sort(key=lambda x: (x["length"], not x["verified"]))
        del lst[MAX_PER_N:]
        if prev is None or length < prev["length"]:
            status = "new_record"
        elif length == prev["length"]:
            status = "equal"
        else:
            status = "longer"
        save(table, path)
        claim = best_known(table, n)
        return {"status": status, "entry": e, "previous": prev,
                "claim": claim if claim and not claim["verified"] and claim["length"] < length else None,
                "for_other": bool(checked_by)}


def ref(e):
    """a short stable reference of an entry inside its n: length and the start of the SHA-256 ('' for a claim)."""
    return "%d-%s" % (e["length"], (e.get("sha256") or "")[:16])


def find(table, n, r):
    for e in table["records"].get(str(n), []):
        if ref(e) == r:
            return e
    return None


def permissions(table, n, e, user_id, staff=False, admin=False):
    """-> (can_delete, can_rename, is_record) for a viewer. The record (best verified entry of its n) can be removed only by an
    admin; any other entry by its submitter, staff or an admin; the label of an entry is renamed by its submitter or an admin.
    An entry without submitter_id (a seed claim) can only be changed by an admin."""
    is_record = best_verified(table, n) is e
    owner = bool(e.get("submitter_id")) and str(e["submitter_id"]) == str(user_id)
    if admin:
        return True, True, is_record
    return (not is_record) and bool(e.get("submitter_id")) and (owner or staff), owner, is_record


def remove(path, n, r):
    """remove the entry; -> the entry, or None if it is not there. The next best verified entry becomes the record."""
    with _lock:
        table = load(path)
        e = find(table, n, r)
        if e is None:
            return None
        lst = table["records"][str(n)]
        lst.remove(e)
        if not lst:
            del table["records"][str(n)]
        save(table, path)
        return e


def set_label(path, n, r, label):
    with _lock:
        table = load(path)
        e = find(table, n, r)
        if e is None:
            return None
        e["label"] = str(label)[:80]
        save(table, path)
        return e


def set_holder(path, n, r, name, user_id):
    """make a member the holder of an entry (administrators; for a word checked for somebody before they joined the
    server, or a verified claim): their display name and Discord id. -> the entry, or None if it is not there."""
    with _lock:
        table = load(path)
        e = find(table, n, r)
        if e is None:
            return None
        e["submitter"] = str(name)[:80]
        e["submitter_id"] = str(user_id)
        save(table, path)
        return e


def when(e):
    """a Discord timestamp (shown in the viewer's own time zone) for a new entry, the stored date for an old one."""
    return "<t:%d:f>" % e["time"] if e.get("time") else str(e.get("date", ""))


def fmt(e):
    if e is None:
        return "-"
    return "{:,}".format(e["length"])


def markdown(table):
    rows = ["| n | shortest verified | by | shortest known claim | by |", "|---:|---:|---|---:|---|"]
    for n in sorted(table["records"], key=int):
        v, k = best_verified(table, n), best_known(table, n)
        rows.append("| %s | %s | %s | %s | %s |" % (
            n, fmt(v), v["submitter"] if v else "", fmt(k) if k is not v else "", k["submitter"] if k is not v else ""))
    return "\n".join(rows)


def main(a):
    if a and a[0] in ("show", "md"):
        print(markdown(load(a[1] if len(a) > 1 else DEFAULT)))
    elif len(a) >= 5 and a[0] == "add":
        with open(a[2], encoding="utf-8") as f:
            res = json.loads(f.read().strip().splitlines()[-1])
        print(json.dumps(submit(a[1], res, a[3], a[4]), indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

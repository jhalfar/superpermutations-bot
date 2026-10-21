"""settings.py - per-user settings of the Discord bot: one field, the default visibility of a user's checks.

A small JSON file next to the records table (SUPERPERM_SETTINGS; default settings.json in the folder of
SUPERPERM_RECORDS, so /data/settings.json in Docker), written atomically, keyed by Discord user id:

    {"format": "superperm-settings/1", "users": {"123456789": {"visibility": "private"}}}

Nothing else about a user is stored. A user who never chose gets DEFAULT (public).
"""
import json
import os
import threading

FORMAT = "superperm-settings/1"
DEFAULT = "public"
CHOICES = ("private", "public")
_lock = threading.Lock()


def default_path(records_path):
    return os.path.join(os.path.dirname(os.path.abspath(records_path)), "settings.json")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
    except FileNotFoundError:
        return {"format": FORMAT, "users": {}}
    if t.get("format") != FORMAT:
        raise ValueError("%s is not a %s file" % (path, FORMAT))
    return t


def get_visibility(path, user_id, default=DEFAULT):
    try:
        v = load(path)["users"].get(str(user_id), {}).get("visibility")
    except (OSError, ValueError):
        return default
    return v if v in CHOICES else default


def set_visibility(path, user_id, value):
    if value not in CHOICES:
        raise ValueError("visibility must be one of %s" % ", ".join(CHOICES))
    with _lock:
        t = load(path)
        t["users"][str(user_id)] = {"visibility": value}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(t, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)

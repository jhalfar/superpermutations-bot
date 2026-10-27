"""board.py - the records table as the last message of the records channel (SUPERPERM_BOARD=1), and the role of an n
that a new record pings (SUPERPERM_N_ROLES).

The board is the card of /records (the table and the select for the details of one n), sent by the bot into
SUPERPERM_CHANNEL. It is kept as the newest message there: after every change of the table (a word added, an entry
removed or renamed) and when the bot starts, the bot edits the board if it is still the last message of the channel,
and otherwise deletes its old board and sends a new one under the announcement. So the channel shows the history of
records and, at the bottom where Discord opens it, the current table. The id of the board message is kept in a small
JSON file next to the records table (SUPERPERM_BOARD_FILE; default board.json in the folder of SUPERPERM_RECORDS):

    {"format": "superperm-board/1", "message_id": 1234567890}

The bot deletes only that one message, its own. It needs Read Message History in the channel (to see which message is
the last one); deleting its own message needs no extra permission.

SUPERPERM_N_ROLES maps n to a role, as comma-separated "N:ROLE_ID" or "LO-HI:ROLE_ID" items, for example
"1-6:111,7:222,8:333,15-99:444". The announcement of a new record for n then mentions that role, and only that role
is allowed to be pinged (the submitter's name stays a plain mention). The role must be mentionable, or the bot needs
the permission Mention @everyone, @here and All Roles.
"""
import asyncio
import json
import logging
import os

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder

import records
import ui
from cards import no_mentions

FORMAT = "superperm-board/1"
log = logging.getLogger("superperm")


def default_path(records_path):
    return os.path.join(os.path.dirname(os.path.abspath(records_path)), "board.json")


def parse_n_roles(text):
    """'1-6:111,7:222' -> [(1, 6, 111), (7, 7, 222)]. Raises ValueError on anything else."""
    out = []
    for item in (text or "").replace(" ", "").split(","):
        if not item:
            continue
        rng, _, rid = item.partition(":")
        lo, _, hi = rng.partition("-")
        if not (lo.isdigit() and (hi == "" or hi.isdigit()) and rid.isdigit()):
            raise ValueError("SUPERPERM_N_ROLES item %r is not N:ROLE_ID or LO-HI:ROLE_ID" % item)
        lo_i, hi_i = int(lo), int(hi or lo)
        if lo_i > hi_i:
            raise ValueError("SUPERPERM_N_ROLES item %r: the range is empty" % item)
        out.append((lo_i, hi_i, int(rid)))
    return out


def role_for_n(n_roles, n):
    """the role id for n (the first matching item), or None."""
    for lo, hi, rid in n_roles or []:
        if lo <= int(n) <= hi:
            return rid
    return None


def load_id(path):
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError):
        return None
    if t.get("format") != FORMAT or not isinstance(t.get("message_id"), int):
        return None
    return t["message_id"]


def save_id(path, message_id):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"format": FORMAT, "message_id": int(message_id)}, f)
        f.write("\n")
    os.replace(tmp, path)


async def last_message_id(channel):
    """the id of the newest message of the channel, asked from Discord (the cache can lag behind a send)."""
    async for m in channel.history(limit=1):
        return m.id
    return None


async def refresh(state):
    """bring the board up to date (see the module text). Does nothing unless the board is on. Never raises."""
    cfg = state.cfg
    if not cfg.board or state.board_channel is None:
        return
    async with state.board_lock:
        try:
            ch = await state.board_channel()
            view = ui.records_card(records.load(cfg.records_path))
            mid = load_id(cfg.board_path)
            if mid is not None and await last_message_id(ch) == mid:
                try:
                    msg = await ch.fetch_message(mid)
                    await msg.edit(view=view, allowed_mentions=no_mentions())
                    return
                except Exception as e:            # gone or not editable: send a new one
                    log.warning("board: could not edit message %s: %r", mid, e)
            if mid is not None:
                try:
                    old = await ch.fetch_message(mid)
                    await old.delete()
                except Exception as e:            # already deleted by somebody: fine
                    log.info("board: old message %s not deleted: %r", mid, e)
            msg = await ch.send(view=view, allowed_mentions=no_mentions())
            save_id(cfg.board_path, msg.id)
        except Exception as e:
            log.warning("board: refresh failed: %r", e)


def schedule(state):
    """refresh the board in the background (used where the caller must not wait)."""
    if state.cfg.board and state.board_channel is not None:
        return asyncio.ensure_future(refresh(state))
    return None

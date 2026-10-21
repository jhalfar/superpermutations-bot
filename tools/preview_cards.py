"""Preview of every card of the Discord bot as indented plain text, without Discord (what a user would read).

    python -I tools/preview_cards.py            # all cards
    python -I tools/preview_cards.py valid      # only the cards whose title contains "valid"

The cards are built by the pure functions of bot/ui.py from sample jobs and results; tests/test_ui.py uses the same samples
(build_all) and checks them. A line "+-- accent #RRGGBB" starts a card (the colour bar at its left edge); [ x ] is a button
(red, green, blue, link, disabled in brackets); a select shows its options.
"""
import copy
import json
import os
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOT = os.path.join(ROOT, "bot")
CHECKER = os.path.join(ROOT, "checker")
sys.path.insert(0, CHECKER)
sys.path.insert(0, BOT)

import discord_bot as bot  # noqa: E402
import records  # noqa: E402
import ui  # noqa: E402

NAMES = {101: "Jakub Halfar", 102: "alice", 103: "Theo H."}
NOW = 1_000_000.0
URL = "https://discord.com/channels/1/2/3"


def sample_job(jid, n, kind, length, shards, name, uid=101, uname="Jakub Halfar", status="queued", lane_pass_s=None,
               est_len=None, progress=None, started=None, cancelled_by=None, private=False):
    est_len = est_len or length or 1000
    one = lane_pass_s if lane_pass_s is not None else est_len / 30e6
    plan = bot.Plan(kind, n, length, shards, est_len, one, one * shards)
    job = bot.Job(jid, uid, uname, name, "x", plan)
    job.status = status
    job.t_queued = NOW - 60
    if started is not None:
        job.t_start = NOW - started
    if progress:
        job.progress = progress
    job.cancelled_by = cancelled_by
    job.private = private
    return job


def result_valid(n, length, kind="word", **kw):
    r = {"ok": True, "valid": True, "kind": kind, "n": n, "length": length, "alphabet": "123456789ABCDE"[:n],
         "sha256": "aec1e713" + "0" * 48 + "5b2c9fd3", "seconds": 257.0, "checker_rc": 0, "missing": 0,
         "foreign_bytes": 0, "first_occurrences": 0, "repeats": 0, "distinct_seen": 0, "letters_read": length}
    r.update(kw)
    return r


def sample_table():
    t = records.load(os.path.join(BOT, "records.json"))
    t = copy.deepcopy(t)
    t["records"]["5"] = [{"length": 153, "sha256": "ab" * 32, "submitter": "Jakub Halfar", "source": "Discord attachment a.txt",
                          "date": "2026-10-21", "verified": True, "kind": "word", "submitter_id": "101"}]
    t["records"]["9"] = [
        {"length": 407999, "sha256": None, "submitter": "Bob Claimer", "source": "https://example.org/claim", "date": "2026-10",
         "verified": False, "kind": "claim"},
        {"length": 408000, "sha256": "cd" * 32, "submitter": "alice", "source": "https://github.com/a/b/blob/main/w9.txt.xz",
         "date": "2026-10-20", "verified": True, "kind": "word"}]
    return t


def build_all():
    """-> list of (title, kind, view); kind says who sees it: channel (public message), ephemeral, announcement."""
    out = []

    def add(title, kind, view):
        out.append((title, kind, view))

    # ---- job cards
    j = sample_job(12, 14, "recipe", 93_904_936_032, 3, "13_slow_n14_recipe.splan.json.xz", est_len=93_904_936_032)
    add("job card, queued (position 2, slow lane)", "channel", ui.job_card(j, position=2, now=NOW))
    j1 = sample_job(13, 10, "word", None, 1, "my_word.txt.xz", status="starting", est_len=4_034_855)
    add("job card, queued (starting now)", "channel", ui.job_card(j1, position=0, now=NOW))
    j = sample_job(12, 14, "recipe", 93_904_936_032, 3, "13_slow_n14_recipe.splan.json.xz", status="running", started=720,
                   progress=(1, 3, 29_100_000_000), est_len=93_904_936_032)
    add("job card, running (pass 1 of 3)", "channel", ui.job_card(j, now=NOW))
    j = sample_job(14, 10, "word", None, 1, "my_word.txt.xz", status="running", started=20, progress=(1, 1, 1_200_000),
                   est_len=4_034_855)
    add("job card, running, length not stated (compressed word)", "channel", ui.job_card(j, now=NOW))
    j = sample_job(15, 13, "spra", 6_747_802_319, 1, "12_slow_n13_6747802319.spra.xz", status="done")
    res = result_valid(13, 6_747_802_319, "spra", spra_version=2, spra_bits=6, spra_length=6_747_802_319, length_matches=True,
                       seconds=257.0)
    add("job card, valid (SPRA word, can be added)", "channel", ui.job_card(j, res, can_add=True, now=NOW))
    j = sample_job(16, 14, "recipe", 93_904_936_032, 1, "n14_echols.splan.json.xz", status="done")
    res = result_valid(14, 93_904_936_032, "recipe", generator="gen14", recipe_length=93_904_936_032, length_matches=True,
                       sha_matches=None, seconds=3430.0, shards=2, pass_mismatch=False)
    add("job card, valid (recipe, two passes)", "channel", ui.job_card(j, res, now=NOW))
    j = sample_job(17, 13, "word", 6_747_801_994, 1, "n13_new.txt.xz", status="done")
    res = result_valid(13, 6_747_801_994)
    add("job card, valid, after Add to records (new record, role given)", "channel", ui.job_card(
        j, res, added=True, now=NOW, extra=["Table: **new record** for n = 13 (399 letters shorter than 6,747,802,393)",
                                            "Contributor role given."]))
    j = sample_job(18, 6, "word", 872, 1, "04_bad_n6_one_letter_deleted.txt", status="done")
    res = {"ok": False, "valid": False, "kind": "word", "n": 6, "length": 872, "alphabet": "012345", "missing": 4,
           "foreign_bytes": 0, "sha256": "ee" * 32, "seconds": 0.1}
    add("job card, not valid", "channel", ui.job_card(j, res, now=NOW))
    res = {"ok": False, "valid": False, "kind": "word", "n": 8, "length": 40000, "alphabet": "01234567", "missing": 5,
           "foreign_bytes": 3, "shards": 3, "shard_missing": [0, 5, 0], "sha256": "ee" * 32, "seconds": 61.0}
    add("job card, not valid (3 passes, foreign bytes)", "channel", ui.job_card(
        sample_job(19, 8, "word", 40000, 3, "w8.txt", status="done"), res, now=NOW))
    j = sample_job(20, 8, "spra", 46_233, 1, "07_bad_n8_broken.spra.xz", status="done")
    res = {"ok": False, "valid": False, "kind": "spra", "n": 8, "length": 46225, "missing": 40320,
           "error": "SPRA decoder refused the file: spra: extra bytes after the last token (at letter 46225, tuple 7702)",
           "seconds": 0.4}
    add("job card, file refused (decoder)", "channel", ui.job_card(j, res, now=NOW))
    j = sample_job(21, 14, "recipe", 93_904_936_032, 3, "13_slow_n14_recipe.splan.json.xz", status="cancelled", started=180,
                   progress=(1, 3, 9_950_000_000), cancelled_by="alice")
    add("job card, cancelled while running", "channel",
        ui.job_card(j, {"ok": False, "valid": False, "cancelled": True, "error": "cancelled by alice"}, now=NOW))
    j = sample_job(22, 13, "word", 6_747_802_393, 1, "w13.txt", status="cancelled", cancelled_by="Jakub Halfar")
    add("job card, cancelled while waiting", "channel",
        ui.job_card(j, {"ok": False, "valid": False, "cancelled": True, "error": "cancelled by Jakub Halfar"}, now=NOW))
    j = sample_job(23, 14, "recipe", 93_904_936_032, 1, "slow.splan.json.xz", status="done", started=3000,
                   progress=(1, 1, 5_000_000_000))
    add("job card, stopped by a time limit", "channel", ui.job_card(
        j, {"ok": False, "valid": False, "timeout": True, "seconds": 3000.0,
            "error": "timed out: pass 1/1 took more than 1 h 21 min (1.5 x its estimate of 52 min, plus start-up); the word "
                     "is longer or the machine slower than assumed"}, now=NOW))
    add("job card, internal error", "channel", ui.job_card(
        sample_job(24, 9, "word", 100, 1, "w9.txt", status="done"),
        {"ok": False, "valid": False, "error": "internal error: boom"}, now=NOW))

    # ---- private answers
    j = sample_job(12, 14, "recipe", 93_904_936_032, 3, "13_slow_n14_recipe.splan.json.xz", est_len=93_904_936_032)
    add("accepted (ephemeral)", "ephemeral", ui.accepted_card(j, URL, 2))
    refusals = [
        ("refused: n above the limit", "n = 15 is above this bot's limit (n ≤ 14). Nothing was run.", "Send a word with n ≤ 14.", "Not accepted"),
        ("refused: wrong ending", "file name must end in .splan.json.xz, .splan.json, .spra.xz, .spra, .txt.xz, .txt",
         "Rename the file, or convert it to one of these.", "Not accepted"),
        ("refused: host", "host evil.example is not allowed", "Use a link on github.com, raw.githubusercontent.com or gitlab.com.", "Not accepted"),
        ("refused: too long", "estimated time 9 h 12 min (94,000,000,000 letters, 3 passes at 30 M letters/s) is above the limit of 6 h (SUPERPERM_MAX_HOURS). Nothing was run.",
         "Send a smaller n; the owner can raise SUPERPERM_MAX_HOURS.", "Not accepted"),
        ("refused: lane full", "the slow lane is full (6 waiting).", "Try again in a while; /queue shows the lanes.", "Not accepted"),
        ("refused: one job per lane", "you already have a check waiting or running in the slow lane.",
         "Wait for it to finish, or cancel it with /cancel or its Cancel button.", "Not accepted"),
        ("refused: file in the wrong format", "SPRA file refused: not a SPRA file", "Nothing was run. Check that the file is complete and in the documented format.", "File refused"),
    ]
    for title, reason, hint, head in refusals:
        add(title + " (ephemeral)", "ephemeral", ui.refusal_card(reason, hint, head))
    add("cancel answer (ephemeral)", "ephemeral", ui.notice_card("Cancelling", "Job #12 is being cancelled.", "cancelled"))
    add("details (ephemeral)", "ephemeral", ui.details_card(sample_job(15, 13, "spra", 6_747_802_319, 1, "12_slow_n13_6747802319.spra.xz",
        status="done"), result_valid(13, 6_747_802_319, "spra", spra_version=2, spra_bits=6, spra_length=6_747_802_319,
                                     length_matches=True)))

    # ---- records, queue, about, announcement, panel
    table = sample_table()
    add("/records", "channel", ui.records_card(table))
    add("/records, details for n = 9 (ephemeral)", "ephemeral", ui.record_details_card(table, "9"))
    add("/records, details for n = 13 (ephemeral)", "ephemeral", ui.record_details_card(table, "13"))
    run = sample_job(5, 10, "word", 4_034_855, 1, "w10.txt", uid=102, uname="alice", status="running", started=12,
                     progress=(1, 1, 1_200_000))
    w1 = sample_job(6, 11, "word", 43_930_578, 1, "w11.txt", uid=103, uname="Theo H.", est_len=43_930_578)
    s1 = sample_job(7, 14, "recipe", 93_904_936_032, 3, "n14.splan.json.xz", status="running", started=720,
                    progress=(2, 3, 20_000_000_000), est_len=93_904_936_032)
    w2 = sample_job(8, 13, "word", 6_747_802_393, 1, "w13.txt", uid=103, uname="Theo H.", est_len=6_747_802_393)
    sched = SimpleNamespace(lanes={"fast": SimpleNamespace(running=run, waiting=[w1]),
                                   "slow": SimpleNamespace(running=s1, waiting=[w2])})
    add("/queue (ephemeral)", "ephemeral", ui.queue_card(sched, now=NOW, urls={5: URL, 7: URL}))
    add("/queue, idle (ephemeral)", "ephemeral", ui.queue_card(SimpleNamespace(lanes={
        "fast": SimpleNamespace(running=None, waiting=[]), "slow": SimpleNamespace(running=None, waiting=[])}), now=NOW))
    add("/about", "channel", ui.about_card(bot.Config(records_path=os.path.join(BOT, "records.json")), bot.VERSION))
    add("record announcement", "announcement", ui.announcement_card(
        13, 6_747_801_994, {"length": 6_747_802_393, "submitter": "Jakub Halfar"}, 101, "Jakub Halfar", "n13_new.txt.xz",
        "ab" * 32, URL))
    add("record announcement, first verified entry", "announcement", ui.announcement_card(
        5, 153, None, 102, "alice", "w5.txt", "cd" * 32, URL))
    add("/panel message", "channel", ui.panel_card())

    # ---- private checks
    pj = sample_job(30, 13, "word", 6_747_802_393, 1, "secret_word_n13.txt.xz", status="queued", private=True, est_len=6_747_802_393)
    add("job card, queued, private (direct message)", "dm", ui.job_card(pj, position=1, now=NOW))
    pj = sample_job(30, 13, "word", 6_747_802_393, 1, "secret_word_n13.txt.xz", status="running", private=True, started=95,
                    progress=(1, 1, 2_100_000_000))
    add("job card, running, private (direct message)", "dm", ui.job_card(pj, now=NOW))
    pj = sample_job(30, 13, "spra", 6_747_802_319, 1, "secret_word_n13.spra.xz", status="done", private=True)
    pres = result_valid(13, 6_747_802_319, "spra", spra_version=2, spra_bits=6, spra_length=6_747_802_319, length_matches=True)
    add("job card, valid, private (direct message, can be added)", "dm", ui.job_card(pj, pres, can_add=True, now=NOW))
    for phase in ("queued", "running", "finished"):
        add("note in the channel, private check, %s" % phase, "channel", ui.trace_card(
            sample_job(30, 13, "word", 1, 1, "secret_word_n13.txt.xz", private=True), phase))
    add("audit copy of a private check, finished", "audit", ui.job_card(pj, pres, now=NOW, audit=True))
    add("audit copy of a private check, queued", "audit", ui.job_card(
        sample_job(31, 12, "word", 522_737_175, 1, "alices_n12.txt", uid=102, uname="alice", private=True), position=0, now=NOW,
        audit=True))
    add("accepted, private (ephemeral)", "ephemeral", ui.accepted_card(
        sample_job(30, 13, "word", 1, 1, "x.txt", private=True), "https://discord.com/channels/@me/5/6", 1))
    add("refused: closed direct messages (ephemeral)", "ephemeral", ui.refusal_card(
        "I cannot send you a direct message.", "Allow direct messages from server members (Server menu > Privacy settings), or "
        "run the check as public.", "Could not start"))
    add("confirm publish (ephemeral)", "ephemeral", ui.confirm_publish_card(
        sample_job(30, 13, "word", 1, 1, "x.txt", private=True), "<#900>"))
    add("settings (ephemeral)", "ephemeral", ui.settings_card("public"))
    running = sample_job(5, 13, "word", 6_747_802_393, 1, "secret_n13.txt.xz", uid=102, uname="alice", status="running", private=True,
                         started=95, progress=(1, 1, 2_100_000_000))
    waiting = [sample_job(6, 11, "word", 43_930_578, 1, "bobs_n11.txt", uid=103, uname="Theo H.", private=True, est_len=43_930_578),
               sample_job(7, 12, "word", 522_737_175, 1, "cy_n12.txt", uid=101, uname="Jakub Halfar", est_len=522_737_175)]
    qs = SimpleNamespace(lanes={"fast": SimpleNamespace(running=None, waiting=waiting[:0]),
                                "slow": SimpleNamespace(running=running, waiting=[waiting[0]])})
    qs.lanes["fast"] = SimpleNamespace(running=None, waiting=[waiting[1]])
    urls = {5: URL}
    add("/queue with private checks, as another user (ephemeral)", "ephemeral",
        ui.queue_card(qs, now=NOW, urls=urls, viewer_id=999))
    add("/queue with private checks, as the submitter (alice) (ephemeral)", "ephemeral",
        ui.queue_card(qs, now=NOW, urls=urls, viewer_id=102))
    add("/queue with private checks, as staff (Manage Messages) (ephemeral)", "ephemeral",
        ui.queue_card(qs, now=NOW, urls=urls, viewer_id=998, staff=True))
    add("/queue with private checks, as admin (Administrator) (ephemeral)", "ephemeral",
        ui.queue_card(qs, now=NOW, urls=urls, viewer_id=997, staff=True, admin=True))

    # ---- history and records management
    def hrow(hid, job, vis, name, n, length, verdict, t, added=0, uid=102):
        return {"hid": hid, "job_id": job, "user_id": str(uid), "user_name": "alice", "t": t, "visibility": vis, "file_name": name,
                "source": "attachment", "kind": "word", "n": n, "length": length, "sha256": ("%02x" % hid) * 32 if length else None,
                "verdict": verdict, "seconds": 257.0 if length else 0.1, "added": added}
    rows = [hrow(31, 31, "private", "secret_n13.txt.xz", 13, 6_747_802_393, "valid", 1_761_052_800, 1),
            hrow(30, 30, "public", "n12_candidate.txt", 12, 522_737_175, "invalid", 1_761_000_000),
            hrow(29, 29, "public", "07_bad_n8_broken.spra.xz", 8, 46_225, "refused", 1_760_990_000),
            hrow(28, 28, "dm", "dm_word_n10.txt", 10, 4_034_855, "valid", 1_760_900_000),
            hrow(27, 27, "public", "slow_n14.splan.json.xz", 14, None, "cancelled", 1_760_800_000)]
    add("history, own page 1 of 3 (ephemeral)", "ephemeral", ui.history_card(rows, [False] * 5, 0, 3, 25, 102, 180))
    add("history, page 3 of 3 (ephemeral)", "ephemeral", ui.history_card(rows[:2], [False, False], 2, 3, 22, 102, 180))
    add("history, as staff looking at another user (private checks masked) (ephemeral)", "ephemeral",
        ui.history_card(rows, [True, False, False, True, False], 0, 1, 5, 102, 180))
    add("history, empty (ephemeral)", "ephemeral", ui.history_card([], [], 0, 1, 0, 102, 180))
    add("history entry, own valid private check (ephemeral)", "ephemeral", ui.history_entry_card(rows[0], False, True, True))
    add("history entry, as staff: private check of another user, masked (ephemeral)", "ephemeral",
        ui.history_entry_card(rows[0], True, True, False))
    add("history entry, refused check (ephemeral)", "ephemeral", ui.history_entry_card(rows[2], False, True, True))
    t9 = sample_table()
    t9["records"]["9"][1]["submitter_id"] = "102"
    t9["records"]["9"][1]["time"] = 1_761_052_800
    t9["records"]["9"][1]["label"] = "alice_n9.txt"
    add("/records, details for n = 9, as the submitter alice (with Manage entry) (ephemeral)", "ephemeral",
        ui.record_details_card(t9, "9", (102, False, False)))
    add("/records, details for n = 9, as admin (Manage entry) (ephemeral)", "ephemeral",
        ui.record_details_card(t9, "9", (997, True, True)))
    e13 = records.entries(t9, "13")[0]
    add("records manage entry, the record, as admin (ephemeral)", "ephemeral", ui.record_manage_card(t9, "13", e13, (997, True, True)))
    add("records confirm removing the record (ephemeral)", "ephemeral", ui.record_confirm_card("13", e13, True))
    t9["records"]["9"].append({"length": 408200, "sha256": "ee" * 32, "submitter": "alice", "submitter_id": "102", "source": "s",
                               "date": "2026-10-19", "time": 1_760_900_000, "verified": True, "kind": "word"})
    e9 = records.entries(t9, "9")[2]
    add("records manage entry, a non-record, as its submitter (ephemeral)", "ephemeral",
        ui.record_manage_card(t9, "9", e9, (102, False, False)))
    add("records confirm removing a non-record (ephemeral)", "ephemeral", ui.record_confirm_card("9", e9, False))
    add("refused: file is not what its name says (ephemeral)", "ephemeral", ui.refusal_card(
        "this is not an xz file: it does not start with the xz signature (FD 37 7A 58 5A 00)",
        "Check that the file is complete and has the format its name says.", "File refused"))
    return out


def modal_text(modal):
    lines = ["Form \"%s\"" % modal.title]
    for item in modal.children:
        c = item.component
        lines.append("  %s%s" % (item.text, "  (%s)" % item.description if item.description else ""))
        extra = "; options: " + ", ".join("%s%s" % (o.label, " (default)" if o.default else "") for o in c.options)             if hasattr(c, "options") else ""
        lines.append("    [%s%s%s]" % (type(c).__name__, ", optional" if not c.required else "", extra))
    return "\n".join(lines)


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    want = (argv[0] if argv else "").lower()
    for title, kind, view in build_all():
        if want and want not in title.lower():
            continue
        ui.check_limits(view)
        print("=== %s  [%s; %d components, %d characters]" % (title, kind, ui.component_count(view), ui.text_length(view)))
        print(ui.plain(view, NAMES))
        print()
    if not want or "rename" in want:
        print("=== the rename form (history entry or records entry)  [modal]")
        print(modal_text(bot.RenameModal(bot.State(bot.Config(records_path=os.path.join(BOT, "records.json"))), "h", 1)))
        print()
    if not want or "form" in want:
        print("=== the form of /check without options and of the panel button  [modal]")
        print(modal_text(bot.CheckModal(bot.State(bot.Config(records_path=os.path.join(BOT, "records.json"))))))


if __name__ == "__main__":
    main(sys.argv[1:])

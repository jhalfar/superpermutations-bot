"""Offline tests of the history of checks (history.py, /history), the management of the records table, the timestamps and the
magic check of uploaded files (magic.py).

    python -I tests/test_history.py
"""
import asyncio
import json
import lzma
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOT = os.path.join(ROOT, "bot")
CHECKER = os.path.join(ROOT, "checker")
sys.path.insert(0, CHECKER)
sys.path.insert(0, BOT)
sys.path.insert(0, HERE)

import discord  # noqa: E402

import discord_bot as bot  # noqa: E402
import history  # noqa: E402
import magic  # noqa: E402
import records  # noqa: E402
import test_bot as tb  # noqa: E402
import ui  # noqa: E402

FakeInteraction, FakeChannel, World = tb.FakeInteraction, tb.FakeChannel, tb.World
STAFF = {"manage_messages": True}
ADMIN = {"administrator": True}


class Base(tb.Base):
    def setUp(self):
        super().setUp()
        self.world = World()
        self.hist = os.path.join(self.tmp, "history.sqlite")

    def state(self, runner=None, audit=True, **kw):
        st = super().state(runner=runner, **kw)
        if audit:
            st.cfg.audit_channel_id = 555

            async def audit_channel():
                return FakeChannel(self.world, "audit")
            st.audit_channel = audit_channel
        return st

    def audit_texts(self):
        return [m.text for m in self.world.log if m.where == "audit"]

    def user(self, uid, name=None, perms=None, **kw):
        return FakeInteraction(uid=uid, name=name or "u%d" % uid, perms=perms, world=kw.pop("world", None) or World(), **kw)

    def rows(self, uid, n=3, vis="public", start=1_700_000_000):
        return [history.add(self.hist, 100 + k, uid, "u%d" % uid, vis, "file%d.txt" % k, "attachment", "word", 10, now=start + k)
                for k in range(n)]


class TestStore(Base):
    def test_rows_and_fields(self):
        hid = history.add(self.hist, 7, 42, "Dora D", "public", "w.txt", "attachment", "word", 10, now=1_700_000_000)
        r = history.get(self.hist, hid)
        self.assertEqual((r["job_id"], r["user_id"], r["user_name"], r["t"], r["visibility"], r["file_name"], r["source"], r["n"],
                          r["verdict"], r["added"]), (7, "42", "Dora D", 1_700_000_000, "public", "w.txt", "attachment", 10,
                                                      "pending", 0))
        history.finish(self.hist, hid, "valid", n=10, length=4034855, sha256="ab" * 32, seconds=1.5, kind="word")
        r = history.get(self.hist, hid)
        self.assertEqual((r["verdict"], r["length"], r["sha256"], r["seconds"], r["kind"]), ("valid", 4034855, "ab" * 32, 1.5, "word"))
        history.mark_added(self.hist, hid)
        self.assertEqual(history.get(self.hist, hid)["added"], 1)
        with self.assertRaises(ValueError):
            history.finish(self.hist, hid, "maybe")
        # the columns of the table: the word itself is not among them
        self.assertEqual(set(history.COLUMNS), {"hid", "job_id", "user_id", "user_name", "t", "visibility", "file_name", "source",
                                                "kind", "n", "length", "sha256", "verdict", "seconds", "added"})

    def test_paging_newest_first_and_per_user(self):
        self.rows(1, 25)
        self.rows(2, 4)
        self.assertEqual(history.count_user(self.hist, 1), 25)
        p0 = history.list_user(self.hist, 1, 0, 10)
        p2 = history.list_user(self.hist, 1, 20, 10)
        self.assertEqual((len(p0), len(p2)), (10, 5))
        self.assertEqual([r["file_name"] for r in p0][:2], ["file24.txt", "file23.txt"])
        self.assertTrue(all(r["user_id"] == "1" for r in p0 + p2))
        self.assertEqual(history.count_user(self.hist, 3), 0)

    def test_delete_rename_and_retention(self):
        old = history.add(self.hist, 1, 5, "x", "public", "old.txt", "attachment", "word", 5, now=1000)
        new = history.add(self.hist, 2, 5, "x", "public", "new.txt", "attachment", "word", 5, now=1000 + 179 * 86400)
        stale = history.add(self.hist, 3, 5, "x", "private", "stale.txt", "attachment", "word", 5, now=1000 + 179 * 86400)
        now = 1000 + 181 * 86400
        self.assertEqual(history.purge(self.hist, 180, now=now), 1)             # only the row older than 180 days
        self.assertIsNone(history.get(self.hist, old))
        self.assertIsNotNone(history.get(self.hist, new))
        self.assertEqual(history.get(self.hist, stale)["verdict"], "error")       # pending since before a restart
        history.rename(self.hist, new, "renamed.txt")
        self.assertEqual(history.get(self.hist, new)["file_name"], "renamed.txt")
        self.assertEqual(history.delete(self.hist, new), 1)
        self.assertIsNone(history.get(self.hist, new))
        self.assertEqual(history.delete(self.hist, new), 0)

    def test_config(self):
        c = bot.Config(records_path=os.path.join(self.tmp, "r.json"))
        self.assertEqual((c.history_path, c.history_days), (os.path.join(self.tmp, "history.sqlite"), 180))
        self.assertEqual(bot.Config(history_path="/x/h.sqlite", history_days=7).history_days, 7)
        old = {k: os.environ.get(k) for k in ("SUPERPERM_HISTORY", "SUPERPERM_HISTORY_DAYS")}
        try:
            os.environ.update({"SUPERPERM_HISTORY": "/y/h.db", "SUPERPERM_HISTORY_DAYS": "30"})
            c = bot.Config()
            self.assertEqual((c.history_path, c.history_days), ("/y/h.db", 30))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class TestFlow(Base):
    def test_one_row_per_accepted_job(self):
        st = self.state()
        i = FakeInteraction(uid=7, name="login", display_name="Dora D", world=World())
        t0 = time.time()
        self.run_async(bot.handle_check(st, i, attachment=self.att(10, "my word.txt"), visibility="public"))
        rows = history.list_user(st.cfg.history_path, 7)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["user_name"], r["visibility"], r["file_name"], r["source"], r["kind"], r["n"], r["length"],
                          r["sha256"], r["verdict"], r["added"]),
                         ("Dora D", "public", "my_word.txt", "attachment", "word", 10, 4034855, "ab" * 32, "valid", 0))
        self.assertLessEqual(abs(r["t"] - t0), 5)
        self.assertEqual(r["job_id"], 1)
        # a refusal before a job exists writes nothing
        j = FakeInteraction(uid=7, world=World())
        self.run_async(bot.handle_check(st, j, attachment=self.att(15)))
        self.assertEqual(history.count_user(st.cfg.history_path, 7), 1)
        # visibilities and the URL as source
        k = FakeInteraction(uid=8, world=World())
        self.run_async(bot.handle_check(st, k, attachment=self.att(10), visibility="private"))
        d = FakeInteraction(uid=9, guild=False, world=World())
        self.run_async(bot.handle_check(st, d, attachment=self.att(10)))
        self.assertEqual(history.list_user(st.cfg.history_path, 8)[0]["visibility"], "private")
        self.assertEqual(history.list_user(st.cfg.history_path, 9)[0]["visibility"], "dm")
        with open(tb.W10, "rb") as f:
            data = f.read()
        st2 = self.state(fetch=lambda u, t: tb.FakeHttp(200, data, {"Content-Length": str(len(data))}))
        u = FakeInteraction(uid=10, world=World())
        self.run_async(bot.handle_check(st2, u, url="https://github.com/a/b/blob/main/w.txt.xz"))
        self.assertEqual(history.list_user(st2.cfg.history_path, 10)[0]["source"], "https://github.com/a/b/raw/main/w.txt.xz")

    def test_verdicts_and_added(self):
        cases = [({"ok": False, "valid": False, "n": 9, "length": 1, "missing": 5, "foreign_bytes": 0}, "invalid"),
                 ({"ok": False, "valid": False, "cancelled": True, "error": "cancelled by x"}, "cancelled"),
                 ({"ok": False, "valid": False, "timeout": True, "error": "timed out"}, "stopped"),
                 ({"ok": False, "valid": False, "kind": "spra", "n": 8, "error": "SPRA decoder refused the file: bad"}, "refused"),
                 ({"ok": False, "valid": False, "error": "internal error: boom"}, "error")]
        for res, verdict in cases:
            st = self.state(runner=lambda p, job=None, res=res: res)
            i = FakeInteraction(uid=7, world=World())
            self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
            self.assertEqual(history.list_user(st.cfg.history_path, 7)[0]["verdict"], verdict, res)
        st = self.state(runner=lambda p, job=None: tb.good_result(n=9, length=408000, sha256="cd" * 32))
        i = FakeInteraction(uid=7, name="ann", world=World())
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), visibility="public"))
        self.assertEqual(history.list_user(st.cfg.history_path, 7)[0]["added"], 0)
        click = FakeInteraction(uid=7, name="ann", world=World())
        jid = history.list_user(st.cfg.history_path, 7)[0]["job_id"]    # numbers go on from the earlier checks
        self.run_async(bot.handle_job_button(st, "add", jid, click))
        self.assertEqual(history.list_user(st.cfg.history_path, 7)[0]["added"], 1)

    def test_history_failure_never_stops_a_check(self):
        st = self.state()
        st.cfg.history_path = os.path.join(self.tmp, "no", "such", "dir", "h.sqlite")
        i = FakeInteraction(uid=7, world=World())
        with self.assertLogs("superperm", "WARNING") as logs:
            self.run_async(bot.handle_check(st, i, attachment=self.att(10), visibility="public"))
        self.assertTrue(any("history add failed" in x for x in logs.output))
        self.assertIn("Valid", i.card.text)


class TestHistoryCommand(Base):
    def test_own_history_page(self):
        st = self.state()
        self.rows(7, 3)
        history.finish(self.hist, 1, "valid", n=10, length=4034855)
        st.cfg.history_path = self.hist
        i = self.user(7)
        self.run_async(bot.handle_history(st, i))
        m = i.of("response")[0]
        self.assertTrue(m.ephemeral)
        t = m.text
        self.assertIn("<t:1700000002:f>", t)                          # a Discord timestamp
        self.assertIn("`file2.txt`", t)
        self.assertIn("n = 10", t)
        self.assertIn("✅", t)                                    # verdict emoji
        self.assertIn("4,034,855 letters", t)
        self.assertIn("#102", t)                                      # the job id
        self.assertLess(t.index("file2.txt"), t.index("file0.txt"))   # newest first
        self.assertIn("never the file itself", t)
        self.assertEqual(ui.buttons(m.view), [])                      # one page: no Previous / Next
        self.assertTrue(ui.check_limits(m.view))
        sel = [x.item for x in ui.walk(m.view) if isinstance(x, discord.ui.DynamicItem)][0]
        self.assertEqual(len(sel.options), 3)
        e = self.user(99)
        self.run_async(bot.handle_history(st, e))
        self.assertIn("Nothing here yet", e.of("response")[0].text)

    def test_paging(self):
        st = self.state()
        st.cfg.history_path = self.hist
        self.rows(7, 25)
        i = self.user(7)
        self.run_async(bot.handle_history(st, i))
        m = i.of("response")[0]
        self.assertIn("page 1 of 3", m.text)
        self.assertEqual(sum(1 for l in m.text.split("\n") if l.startswith("<t:")), 10)
        prev, nxt = ui.buttons(m.view)
        self.assertEqual((prev.label, prev.disabled, nxt.label, nxt.disabled), ("Previous", True, "Next", False))
        self.assertEqual(nxt.custom_id, "sp:x:hp:7:1")
        c = self.user(7)
        self.run_async(bot.handle_x(st, "hp", "7:2", None, c))          # the Next button of page 2
        v = c.world.paged[-1]
        self.assertIn("page 3 of 3", "\n".join(ui.texts(v)))
        self.assertEqual(sum(1 for l in "\n".join(ui.texts(v)).split("\n") if l.startswith("<t:")), 5)
        prev, nxt = ui.buttons(v)
        self.assertEqual((prev.disabled, nxt.disabled), (False, True))
        c = self.user(7)
        self.run_async(bot.handle_x(st, "hp", "7:9", None, c))          # out of range: the last page
        self.assertIn("page 3 of 3", "\n".join(ui.texts(c.world.paged[-1])))
        for v in (m.view, c.world.paged[-1]):
            self.assertTrue(ui.check_limits(v))
        # nobody pages through somebody else's history
        o = self.user(8)
        self.run_async(bot.handle_x(st, "hp", "7:1", None, o))
        self.assertEqual(o.world.paged, [])
        self.assertIn("Not allowed", o.private)

    def test_other_users_history_and_masking(self):
        st = self.state()
        st.cfg.history_path = self.hist
        h_pub = history.add(self.hist, 1, 7, "Dora", "public", "pub_file.txt", "attachment", "word", 10, now=1_700_000_000)
        h_prv = history.add(self.hist, 2, 7, "Dora", "private", "secret_file.txt", "attachment", "word", 11, now=1_700_000_100)
        h_dm = history.add(self.hist, 3, 7, "Dora", "dm", "dm_secret.txt", "attachment", "word", 12, now=1_700_000_200)
        for h, n, ln in ((h_pub, 10, 4034855), (h_prv, 11, 43930578), (h_dm, 12, 522737175)):
            history.finish(self.hist, h, "valid", n=n, length=ln, sha256="%02x" % h * 32)
        target = SimpleNamespace(id=7)
        # an ordinary member cannot look
        o = self.user(8)
        self.run_async(bot.handle_history(st, o, target))
        self.assertIn("only moderators", o.private)
        # staff: public rows in full, private and dm rows masked
        s = self.user(20, perms=STAFF)
        self.run_async(bot.handle_history(st, s, target))
        t = s.of("response")[0].text
        self.assertIn("pub_file.txt", t)
        self.assertIn("\U0001f512 private check", t)
        for hidden in ("secret_file", "dm_secret", "43,930,578", "522,737,175"):
            self.assertNotIn(hidden, t)
        c = self.user(20, perms=STAFF)
        self.run_async(bot.handle_x(st, "hs", "7", str(h_prv), c))
        self.assertNotIn("secret_file", c.private)
        self.assertNotIn("ef" * 3, c.private)
        self.assertIn("hidden", c.private)
        labels = [b.label for b in ui.buttons(c.private_view)]
        self.assertEqual(labels, ["Delete from history"])               # staff may delete, not rename
        # admin: everything
        a = self.user(21, perms=ADMIN)
        self.run_async(bot.handle_history(st, a, target))
        t = a.of("response")[0].text
        self.assertIn("secret_file.txt", t)
        self.assertIn("dm_secret.txt", t)
        c = self.user(21, perms=ADMIN)
        self.run_async(bot.handle_x(st, "hs", "7", str(h_prv), c))
        self.assertIn("%02x" % h_prv * 32, c.private)                     # the full SHA-256
        self.assertEqual([b.label for b in ui.buttons(c.private_view)], ["Delete from history", "Rename"])
        # the owner sees all of it, with Delete and Rename
        w = self.user(7)
        self.run_async(bot.handle_x(st, "hs", "7", str(h_prv), w))
        self.assertIn("secret_file.txt", w.private)
        self.assertEqual([b.label for b in ui.buttons(w.private_view)], ["Delete from history", "Rename"])
        # a stranger cannot open an entry by guessing
        x = self.user(8)
        self.run_async(bot.handle_x(st, "hs", "7", str(h_prv), x))
        self.assertIn("Not allowed", x.private)

    def test_delete_rules_and_audit(self):
        st = self.state()
        st.cfg.history_path = self.hist
        h1, h2, h3, h4 = [history.add(self.hist, k, 7, "Dora", "public", "f%d.txt" % k, "attachment", "word", 5) for k in range(4)]
        # the owner
        w = self.user(7)
        self.run_async(bot.handle_x(st, "hd", str(h1), None, w))
        self.assertIsNone(history.get(self.hist, h1))
        self.assertEqual(self.audit_texts(), [])                         # an owner's own delete is not audited
        # a stranger
        x = self.user(8)
        self.run_async(bot.handle_x(st, "hd", str(h2), None, x))
        self.assertIsNotNone(history.get(self.hist, h2))
        self.assertIn("Not allowed", x.private)
        # staff may delete; the audit channel gets a line
        s = self.user(20, perms=STAFF)
        self.run_async(bot.handle_x(st, "hd", str(h2), None, s))
        self.assertIsNone(history.get(self.hist, h2))
        self.assertEqual(self.audit_texts()[-1].split("\n")[-1], "<@20> deleted history entry #%d of <@7>" % h2)
        # admin too
        a = self.user(21, perms=ADMIN)
        self.run_async(bot.handle_x(st, "hd", str(h3), None, a))
        self.assertEqual(self.audit_texts()[-1].split("\n")[-1], "<@21> deleted history entry #%d of <@7>" % h3)
        # gone entries are reported
        g = self.user(7)
        self.run_async(bot.handle_x(st, "hd", str(h1), None, g))
        self.assertIn("gone", g.private)
        # no audit channel configured: nothing breaks
        st2 = self.state(audit=False)
        st2.cfg.history_path = self.hist
        s2 = self.user(20, perms=STAFF)
        self.run_async(bot.handle_x(st2, "hd", str(h4), None, s2))
        self.assertIsNone(history.get(self.hist, h4))

    def test_rename_rules_validation_and_audit(self):
        st = self.state()
        st.cfg.history_path = self.hist
        h = history.add(self.hist, 1, 7, "Dora", "public", "old.txt", "attachment", "word", 5)

        def modal_for(who):
            self.run_async(bot.handle_x(st, "hr", str(h), None, who))
            return who.world.modals[0] if who.world.modals else None
        self.assertIsNone(modal_for(self.user(8)))                       # a stranger
        self.assertIsNone(modal_for(self.user(20, perms=STAFF)))         # staff may delete but not rename
        m = modal_for(self.user(7))
        self.assertIsInstance(m, bot.RenameModal)
        d = m.to_dict()["components"][0]
        self.assertEqual((d["component"]["min_length"], d["component"]["max_length"]), (1, 80))
        # validation: 1 to 80 characters
        for bad in ("", "   ", "x" * 81):
            c = self.user(7)
            self.run_async(bot.handle_rename(st, "h", h, bad, c))
            self.assertIn("1 to 80 characters", c.private)
            self.assertEqual(history.get(self.hist, h)["file_name"], "old.txt")
        # cleaned like clean_name
        c = self.user(7)
        self.run_async(bot.handle_rename(st, "h", h, " my new/name! (v2).txt ", c))
        self.assertEqual(history.get(self.hist, h)["file_name"], "name___v2_.txt")
        self.assertIn("Renamed", c.private)
        self.assertEqual(self.audit_texts(), [])
        # the modal path end to end
        m = modal_for(self.user(7))
        m.newname.component._value = "again.txt"
        c = self.user(7)
        self.run_async(m.on_submit(c))
        self.assertEqual(history.get(self.hist, h)["file_name"], "again.txt")
        # a rename submitted by somebody without the right is refused even if the modal was opened before
        c = self.user(20, perms=STAFF)
        self.run_async(bot.handle_rename(st, "h", h, "hacked.txt", c))
        self.assertEqual(history.get(self.hist, h)["file_name"], "again.txt")
        # an admin renames and it is audited; only the shown name changes
        a = self.user(21, perms=ADMIN)
        self.run_async(bot.handle_rename(st, "h", h, "tidy.txt", a))
        row = history.get(self.hist, h)
        self.assertEqual((row["file_name"], row["sha256"], row["n"]), ("tidy.txt", None, 5))
        self.assertEqual(self.audit_texts()[-1].split("\n")[-1], "<@21> renamed history entry #%d of <@7>" % h)

    def test_about_and_timestamps(self):
        c = bot.Config(records_path=os.path.join(self.tmp, "r.json"))
        v = ui.about_card(c, "1.0")
        t = "\n".join(ui.texts(v))
        self.assertIn("**History**", t)
        self.assertIn("The bot keeps a history of checks for 180 days: time, user, file name, n, length, SHA-256, result; never "
                      "the file", t)
        self.assertIn("`/history` shows yours; you can delete or rename your entries.", t)
        self.assertTrue(ui.check_limits(v))
        self.assertEqual(ui.when_unix(1_700_000_000), "<t:1700000000:f>")


# ----------------------------------------------------------------------------------------------- the records table

class TestRecordsManagement(Base):
    def setUp(self):
        super().setUp()
        t = records.load(self.rec)
        self.R = {"length": 408000, "sha256": "aa" * 32, "submitter": "Ann", "submitter_id": "11", "source": "s", "date": "2026-10-21",
                  "time": 1_700_000_000, "verified": True, "kind": "word", "label": "ann.txt"}
        self.OTHER = {"length": 408100, "sha256": "bb" * 32, "submitter": "Bob", "submitter_id": "12", "source": "s",
                      "date": "2026-10-20", "time": 1_699_999_000, "verified": True, "kind": "word"}
        self.SEED = {"length": 407999, "sha256": None, "submitter": "Seed Claimer", "source": "as in README", "date": "2026-10",
                     "verified": False, "kind": "claim"}
        t["records"]["9"] = [dict(self.R), dict(self.OTHER), dict(self.SEED)]
        records.save(t, self.rec)
        self.st = self.state()
        self.rR, self.rO, self.rS = records.ref(self.R), records.ref(self.OTHER), records.ref(self.SEED)

    def table(self):
        return records.load(self.rec)

    def has(self, ref):
        return records.find(self.table(), "9", ref) is not None

    def press(self, kind, arg, who, value=None):
        self.run_async(bot.handle_x(self.st, kind, arg, value, who))
        return who

    def test_permissions_function(self):
        t = self.table()
        e = {r: records.find(t, "9", r) for r in (self.rR, self.rO, self.rS)}
        p = lambda r, uid, staff=False, admin=False: records.permissions(t, "9", e[r], uid, staff, admin)
        self.assertEqual(p(self.rR, 11), (False, True, True))             # the record: the owner cannot remove it, can rename
        self.assertEqual(p(self.rR, 20, staff=True), (False, False, True))
        self.assertEqual(p(self.rR, 21, staff=True, admin=True), (True, True, True))
        self.assertEqual(p(self.rO, 12), (True, True, False))             # a non-record: its submitter
        self.assertEqual(p(self.rO, 11), (False, False, False))
        self.assertEqual(p(self.rO, 20, staff=True), (True, False, False))
        self.assertEqual(p(self.rO, 21, staff=True, admin=True), (True, True, False))
        self.assertEqual(p(self.rS, 12), (False, False, False))           # no submitter_id: only admins
        self.assertEqual(p(self.rS, 20, staff=True), (False, False, False))
        self.assertEqual(p(self.rS, 21, staff=True, admin=True), (True, True, False))

    def test_manage_select_lists_only_what_the_viewer_may_change(self):
        def options(uid, perms=None):
            i = self.user(uid, perms=perms)
            self.run_async(bot.handle_records_detail(self.st, "9", i))
            sels = [x.item for x in ui.walk(i.private_view) if isinstance(x, discord.ui.DynamicItem) and x.item.custom_id.startswith("sp:y:rm")]
            return [o.value for o in sels[0].options] if sels else []
        self.assertEqual(options(99), [])                                 # a stranger: no select at all
        self.assertEqual(options(11), [self.rR])                          # the record's owner: rename only, but listed
        self.assertEqual(options(12), [self.rO])
        self.assertEqual(options(20, STAFF), [self.rO])                   # staff: delete a non-record that has an owner
        self.assertEqual(sorted(options(21, ADMIN)), sorted([self.rR, self.rO, self.rS]))

    def test_record_removal_only_by_an_admin_with_confirmation(self):
        # the owner, staff: no confirm card, nothing removed
        for who in (self.user(11), self.user(20, perms=STAFF), self.user(99)):
            self.press("rd", "9:" + self.rR, who)
            self.assertIn("only by an administrator", who.private)
            self.press("ry", "9:" + self.rR, who)
            self.assertIn("Not allowed", who.private)
            self.assertTrue(self.has(self.rR))
        # an admin: first a confirmation card
        a = self.user(21, perms=ADMIN)
        self.press("rd", "9:" + self.rR, a)
        self.assertIn("It is the current record", a.private)
        self.assertEqual([b.label for b in ui.buttons(a.private_view)], ["Remove", "Keep"])
        self.assertTrue(self.has(self.rR))                                # asking does not remove
        k = self.user(21, perms=ADMIN)
        self.press("rn", "9:" + self.rR, k)
        self.assertTrue(self.has(self.rR))
        y = self.user(21, perms=ADMIN)
        self.press("ry", "9:" + self.rR, y)
        self.assertFalse(self.has(self.rR))
        self.assertIn("Removed", y.private)
        self.assertIn("the RECORD entry", self.audit_texts()[-1])
        self.assertIn("<@21>", self.audit_texts()[-1])
        # the next best verified entry is the record now
        self.assertEqual(records.best_verified(self.table(), "9")["length"], 408100)

    def test_non_record_removal_rules(self):
        # a stranger cannot, the owner can (no audit line), staff can (audit line), seed entries only admins
        x = self.user(99)
        self.press("rd", "9:" + self.rO, x)
        self.assertIn("Not allowed", x.private)
        o = self.user(12)
        self.press("rd", "9:" + self.rO, o)
        self.assertEqual([b.label for b in ui.buttons(o.private_view)], ["Remove", "Keep"])
        self.assertNotIn("current record", o.private)
        self.press("ry", "9:" + self.rO, self.user(12))
        self.assertFalse(self.has(self.rO))
        self.assertEqual(self.audit_texts(), [])
        # restore the entry for the staff case
        t = self.table()
        t["records"]["9"].append(dict(self.OTHER))
        records.save(t, self.rec)
        s = self.user(20, perms=STAFF)
        self.press("ry", "9:" + self.rO, s)
        self.assertFalse(self.has(self.rO))
        self.assertIn("<@20> removed the entry of n = 9", self.audit_texts()[-1])
        self.assertNotIn("RECORD", self.audit_texts()[-1])
        # a seed claim (no submitter_id): staff cannot, an admin can
        s = self.user(20, perms=STAFF)
        self.press("ry", "9:" + self.rS, s)
        self.assertTrue(self.has(self.rS))
        a = self.user(21, perms=ADMIN)
        self.press("ry", "9:" + self.rS, a)
        self.assertFalse(self.has(self.rS))
        # an entry that is already gone
        g = self.user(21, perms=ADMIN)
        self.press("rd", "9:" + self.rS, g)
        self.assertIn("gone", g.private)

    def test_rename_label(self):
        # the owner and an admin, not staff, not a stranger; the owner of the record may rename it
        self.press("rr", "9:" + self.rR, self.user(20, perms=STAFF))
        s = self.user(20, perms=STAFF)
        self.press("rr", "9:" + self.rR, s)
        self.assertEqual(s.world.modals, [])
        self.assertIn("Not allowed", s.private)
        o = self.user(11)
        self.press("rr", "9:" + self.rR, o)
        m = o.world.modals[0]
        self.assertIsInstance(m, bot.RenameModal)
        m.newname.component._value = "best word (v2).txt"
        c = self.user(11)
        self.run_async(m.on_submit(c))
        self.assertEqual(records.find(self.table(), "9", self.rR)["label"], "best_word__v2_.txt")
        self.assertEqual(self.audit_texts(), [])
        # the length, SHA-256 and submitter are untouched
        e = records.find(self.table(), "9", self.rR)
        self.assertEqual((e["length"], e["sha256"], e["submitter_id"]), (408000, "aa" * 32, "11"))
        # validation
        c = self.user(11)
        self.run_async(bot.handle_rename(self.st, "r", "9:" + self.rR, "x" * 81, c))
        self.assertIn("1 to 80 characters", c.private)
        # an admin renames a seed claim: audited
        a = self.user(21, perms=ADMIN)
        self.run_async(bot.handle_rename(self.st, "r", "9:" + self.rS, "seed.txt", a))
        self.assertEqual(records.find(self.table(), "9", self.rS)["label"], "seed.txt")
        self.assertIn("<@21> renamed the entry of n = 9", self.audit_texts()[-1])
        # a stranger's submission through the modal is refused
        c = self.user(99)
        self.run_async(bot.handle_rename(self.st, "r", "9:" + self.rO, "mine.txt", c))
        self.assertNotIn("label", records.find(self.table(), "9", self.rO))

    def test_manage_card_buttons(self):
        a = self.user(21, perms=ADMIN)
        self.press("rm", "9", a, value=self.rR)
        self.assertEqual([b.label for b in ui.buttons(a.private_view)], ["Delete", "Rename"])
        self.assertIn("the record", a.private)
        o = self.user(11)
        self.press("rm", "9", o, value=self.rR)
        self.assertEqual([b.label for b in ui.buttons(o.private_view)], ["Rename"])
        x = self.user(99)
        self.press("rm", "9", x, value=self.rR)
        self.assertIn("Not allowed", x.private)

    def test_new_entries_get_a_time_and_a_label(self):
        out = records.submit(self.rec, {"ok": True, "sha256": "cc" * 32, "n": 8, "length": 46200, "kind": "word"}, "Cy", "src",
                             submitter_id=33, label="cy.txt", now=1_700_000_500)
        e = out["entry"]
        self.assertEqual((e["time"], e["label"], e["submitter_id"]), (1_700_000_500, "cy.txt", "33"))
        self.assertTrue(e["date"])                                         # "date" stays
        t0 = int(time.time())
        out = records.submit(self.rec, {"ok": True, "sha256": "dd" * 32, "n": 8, "length": 46199, "kind": "word"}, "Cy", "src")
        self.assertLessEqual(abs(out["entry"]["time"] - t0), 5)

    def test_timestamps_in_the_details(self):
        i = self.user(21, perms=ADMIN)
        self.run_async(bot.handle_records_detail(self.st, "9", i))
        t = i.private
        self.assertIn("<t:1700000000:f>", t)                               # a new entry: the viewer's own time zone
        self.assertIn("<t:1699999000:f>", t)
        self.assertIn("2026-10", t)                                        # the seed claim keeps its date
        self.assertNotIn("<t:0", t)
        self.assertIn("name `ann.txt`", t)
        d = self.user(1)
        self.run_async(bot.handle_records_detail(self.st, "13", d))
        self.assertIn("2026-10-07", d.private)                             # old entries: the date
        self.assertEqual(records.when({"date": "2026-10"}), "2026-10")
        self.assertEqual(records.when({"date": "x", "time": 5}), "<t:5:f>")


# ----------------------------------------------------------------------------------------------- the magic check

class TestMagic(Base):
    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def xz(self, data):
        return lzma.compress(data)

    def refused(self, name, data, need):
        p = self.write("m_" + name, data)
        with self.assertRaises(magic.MagicError) as c:
            magic.check(p, name)
        self.assertIn(need, str(c.exception), name)
        self.assertTrue(c.exception.hint)

    def test_xz_signature(self):
        self.assertEqual(magic.XZ_MAGIC, bytes([0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00]))
        for name in ("a.txt.xz", "a.spra.xz", "a.splan.json.xz"):
            self.refused(name, b"\x1f\x8b\x08\x00 this is gzip", "not an xz file")
            self.refused(name, b"", "not an xz file")
        self.refused("t.txt.xz", self.xz(b"0123\n")[:20], "cannot be read")      # a truncated stream

    def test_txt(self):
        for ok in (b"0123456789ABCDEF\n", b"abc\r\ndef \t\x0b\x0c\n", b"", b"~ !"):
            self.assertEqual(magic.check(self.write("ok.txt", ok), "ok.txt"), "word")
        self.assertEqual(magic.check(self.write("ok.txt.xz", self.xz(b"0123\n")), "ok.txt.xz"), "word")
        self.refused("t.txt", b"0123\x00456", "does not look like a text word")
        self.refused("t.txt", b"01\xff23", "does not look like a text word")
        self.refused("t.txt", b"SPRA\x02\x00\x01", "does not look like a text word")
        self.refused("t.txt.xz", self.xz(b"0123\x01"), "does not look like a text word")
        # only the first MiB is looked at
        big = b"a" * magic.TEXT_HEAD + b"\x00"
        self.assertEqual(magic.check(self.write("big.txt", big), "big.txt"), "word")

    def test_spra(self):
        self.assertEqual(magic.check(self.write("ok.spra", b"SPRA\x02\x08\x00"), "ok.spra"), "spra")
        self.assertEqual(magic.check(self.write("ok.spra.xz", self.xz(b"SPRA\x02\x08\x00")), "ok.spra.xz"), "spra")
        self.refused("t.spra", b"SPRX\x02", "not a SPRA file")
        self.refused("t.spra", b"", "not a SPRA file")
        self.refused("t.spra.xz", self.xz(b"hello world"), "once decompressed")

    def test_recipe(self):
        good = json.dumps({"format": "splan/1", "n": 5}).encode()
        self.assertEqual(magic.check(self.write("ok.splan.json", good), "ok.splan.json"), "recipe")
        self.assertEqual(magic.check(self.write("ok.splan.json.xz", self.xz(good)), "ok.splan.json.xz"), "recipe")
        self.refused("t.splan.json", b"\xff\xfe not utf-8", "not UTF-8 JSON")
        self.refused("t.splan.json", b"{not json", "not UTF-8 JSON")
        self.refused("t.splan.json", b"[1, 2, 3]", "not an object")
        self.refused("t.splan.json", b"{\"n\": 5}", "no \"format\" field")
        self.refused("t.splan.json.xz", self.xz(b"{\"n\": 5}"), "no \"format\" field")
        self.refused("t.splan.json.xz", self.xz(b"\x00\x01\x02"), "not UTF-8 JSON")

    def test_unknown_kind_is_left_to_the_name_check(self):
        self.assertIsNone(magic.check(self.write("x.zip", b"PK"), "x.zip"))

    def test_the_check_runs_before_any_decoding_in_the_bot(self):
        st = self.state()
        planned = []
        import commands                                                    # handle_check looks plan_job up there
        real = commands.plan_job
        commands.plan_job = lambda *a, **k: planned.append(1) or real(*a, **k)
        try:
            for name, data, need in (("evil.txt.xz", b"MZ\x90\x00 an executable", "not an xz file"),
                                     ("bad.spra", b"hello", "not a SPRA file"),
                                     ("bin.txt", b"\x00\x01\x02\x03", "does not look like a text word"),
                                     ("r.splan.json", b"[]", "not an object")):
                src = self.write("src_" + name, data)
                i = FakeInteraction(uid=7, world=World())
                self.run_async(bot.handle_check(st, i, attachment=tb.FakeAttachment(name, src), visibility="public"))
                t = i.private
                self.assertIn("File refused", t, name)
                self.assertIn(need, t, name)
                self.assertEqual(i.of("channel"), [])
            self.assertEqual(planned, [])                                  # plan_job (the decoders) never ran
            self.assertEqual((self.calls, st.sched.jobs), ([], {}))
            self.assertEqual(history.count_user(st.cfg.history_path, 7), 0)    # refused before a job: no history row
            # a good file goes through
            i = FakeInteraction(uid=7, world=World())
            self.run_async(bot.handle_check(st, i, attachment=self.att(10), visibility="public"))
            self.assertIn("Valid", i.card.text)
            self.assertEqual(planned, [1])
        finally:
            commands.plan_job = real


class TestRestartAndRole(Base):
    def test_job_numbers_go_on_after_a_restart(self):
        history.add(self.hist, 41, 5, "x", "public", "a.txt", "attachment", "word", 5, now=1000)
        st = self.state()
        self.assertEqual(st.sched._next, 42)

    def test_submit_role(self):
        import submit
        st = self.state()
        ctx = SimpleNamespace(private=False)
        other = SimpleNamespace(user=SimpleNamespace(roles=[SimpleNamespace(id=5)]))
        member = SimpleNamespace(user=SimpleNamespace(roles=[SimpleNamespace(id=77)]))
        self.assertTrue(asyncio.run(submit.may_submit(st, ctx, other)))      # no role set: everyone
        st.cfg.submit_role = 77
        self.assertFalse(asyncio.run(submit.may_submit(st, ctx, other)))
        self.assertTrue(asyncio.run(submit.may_submit(st, ctx, member)))


if __name__ == "__main__":
    unittest.main(verbosity=2)

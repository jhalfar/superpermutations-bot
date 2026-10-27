"""Offline tests of the Discord handlers with fake objects (no network, no token).

    python -I tests/test_bot.py

The fakes record every channel.send / message.edit / interaction response (as the LayoutView that was sent) so that the tests
read the text of the cards, their accent colours and buttons. tests/test_ui.py checks the cards themselves.
Tests that need data files skip themselves when the file is missing: SUPERPERM_WORDS (folder with
superpermutation-10-4034855.txt.xz, default testdata/words), SUPERPERM_N14_RECIPE and SUPERPERM_N14_LENGTH (an n = 14
recipe and its length). See tests/test_check.py.
"""
import asyncio
import inspect
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOT = os.path.join(ROOT, "bot")
CHECKER = os.path.join(ROOT, "checker")
sys.path.insert(0, CHECKER)
sys.path.insert(0, BOT)

import discord  # noqa: E402
import check_any  # noqa: E402
import board  # noqa: E402
import discord_bot as bot  # noqa: E402
import records  # noqa: E402
import ui  # noqa: E402

# the modules that came out of discord_bot.py; the tests that read the source read all of them
BOT_MODULES = ("discord_bot.py", "config.py", "downloads.py", "jobs.py", "state.py", "cards.py", "commands.py", "submit.py",
               "manage.py")


def bot_source():
    out = []
    for fn in BOT_MODULES:
        with open(os.path.join(BOT, fn), encoding="utf-8") as f:
            out.append(f.read())
    return "\n".join(out)


TESTDATA = os.environ.get("SUPERPERM_TESTDATA") or os.path.join(ROOT, "testdata")
WORDS = os.environ.get("SUPERPERM_WORDS") or os.path.join(TESTDATA, "words")
W10 = os.path.join(WORDS, "superpermutation-10-4034855.txt.xz")
N14 = os.environ.get("SUPERPERM_N14_RECIPE") or os.path.join(TESTDATA, "n14_recipe.splan.json.xz")
N14_LENGTH = int(os.environ.get("SUPERPERM_N14_LENGTH") or 0)
need_w10 = unittest.skipUnless(os.path.exists(W10), "n = 10 word not found: %s (set SUPERPERM_WORDS)" % W10)
need_n14 = unittest.skipUnless(os.path.exists(N14) and N14_LENGTH > 0,
                               "n = 14 recipe not found or length unknown (set SUPERPERM_N14_RECIPE and SUPERPERM_N14_LENGTH)")
ALPH = "0123456789ABCDEFG"


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Message")


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")


# ----------------------------------------------------------------------------------------------- fakes

def check_sent(content, view, allowed_mentions):
    """what Discord would reject: content next to a LayoutView, no allowed_mentions, a card over a limit."""
    assert content is None, "a LayoutView message cannot carry content"
    assert isinstance(view, discord.ui.LayoutView), type(view)
    assert allowed_mentions is not None, "allowed_mentions missing: a name could ping somebody"
    assert not (allowed_mentions.everyone or allowed_mentions.users or allowed_mentions.roles
                or allowed_mentions.replied_user), "allowed_mentions must be none"
    ui.check_limits(view)


class FakeMessage:
    """a message that was sent; edits change .view and are kept in .history (views) and .times."""

    def __init__(self, world, view, where, ephemeral=False):
        self.world, self.view, self.where, self.ephemeral = world, view, where, ephemeral
        self.history, self.times = [view], [time.monotonic()]
        world.counter += 1
        self.id = world.counter
        self.jump_url = "https://discord.com/channels/1/2/%d" % self.id
        world.log.append(self)

    @property
    def text(self):
        return "\n".join(ui.texts(self.view))

    @property
    def texts(self):
        return ["\n".join(ui.texts(v)) for v in self.history]

    async def edit(self, content=None, *, view=None, allowed_mentions=None, **kw):
        check_sent(content, view, allowed_mentions)
        if self.world.edit_errors:
            raise self.world.edit_errors.pop(0)
        self.view = view
        self.history.append(view)
        self.times.append(time.monotonic())
        self.world.edits += 1


class World:
    """everything one interaction produced, in order."""

    def __init__(self):
        self.log, self.edits, self.counter = [], 0, 0
        self.dm_error = None         # raised by user.create_dm (closed direct messages)
        self.edit_errors = []        # exceptions raised by the next edits
        self.send_error = None       # raised by channel.send
        self.modals = []
        self.paged = []              # views the user paged to (response.edit_message)


class FakeChannel:
    def __init__(self, world, where="channel"):
        self.world, self.where = world, where

    async def send(self, content=None, *, view=None, allowed_mentions=None, **kw):
        check_sent(content, view, allowed_mentions)
        if self.where == "channel" and self.world.send_error:
            raise self.world.send_error
        return FakeMessage(self.world, view, self.where)


class FakeResponse:
    def __init__(self, world):
        self.world, self.deferred, self.defer_kw, self.done = world, False, {}, False

    def is_done(self):
        return self.done

    async def defer(self, **kw):
        self.deferred, self.defer_kw, self.done = True, kw, True

    async def send_message(self, content=None, *, view=None, ephemeral=False, allowed_mentions=None, **kw):
        check_sent(content, view, allowed_mentions)
        self.done = True
        return FakeMessage(self.world, view, "response", ephemeral)

    async def edit_message(self, *, view=None, allowed_mentions=None, **kw):
        check_sent(None, view, allowed_mentions)
        self.done = True
        self.world.paged.append(view)

    async def send_modal(self, modal):
        self.done = True
        self.world.modals.append(modal)


class FakeFollowup:
    def __init__(self, world):
        self.world = world

    async def send(self, content=None, *, view=None, ephemeral=False, allowed_mentions=None, **kw):
        check_sent(content, view, allowed_mentions)
        return FakeMessage(self.world, view, "followup", ephemeral)


class FakeRole:
    def __init__(self, rid):
        self.id = rid


class FakeMember:
    def __init__(self, uid, name, display_name=None, perms=None, roles=None, add_roles_error=None):
        self.id, self.name = uid, name
        if display_name:
            self.display_name = display_name
        self.guild_permissions = SimpleNamespace(manage_messages=False, administrator=False, manage_guild=False)
        for k, v in (perms or {}).items():
            setattr(self.guild_permissions, k, v)
        self.roles = roles or []
        self.added = []
        self._err = add_roles_error

    async def add_roles(self, role, reason=None):
        if self._err:
            raise self._err
        self.added.append((role, reason))


class FakeGuild:
    def __init__(self, roles=()):
        self.roles = {r.id: r for r in roles}

    def get_role(self, rid):
        return self.roles.get(rid)


class FakeInteraction:
    def __init__(self, uid=1, name="tester", member=None, guild=True, world=None, **member_kw):
        self.world = world or World()
        self.response = FakeResponse(self.world)
        self.followup = FakeFollowup(self.world)
        self.user = member or FakeMember(uid, name, **member_kw)
        self.channel = FakeChannel(self.world)
        self.guild = FakeGuild() if guild else None
        world_ = self.world

        async def create_dm():
            if world_.dm_error:
                raise world_.dm_error
            return FakeChannel(world_, "dm")
        self.user.create_dm = create_dm

    def of(self, where):
        return [m for m in self.world.log if m.where == where]

    @property
    def card(self):
        """the job card: the first message sent to the channel."""
        c = self.of("channel")
        return c[0] if c else None

    @property
    def dm_card(self):
        """the card of a private check: the first message sent to the user's direct messages."""
        c = self.of("dm")
        return c[0] if c else None

    @property
    def private(self):
        """text of the last private (followup or response) message."""
        m = [x for x in self.world.log if x.where in ("followup", "response")]
        return m[-1].text if m else None

    @property
    def private_view(self):
        m = [x for x in self.world.log if x.where in ("followup", "response")]
        return m[-1].view if m else None


class FakeAttachment:
    def __init__(self, filename, src=None, size=None):
        self.filename, self.src = filename, src
        self.size = size if size is not None else (os.path.getsize(src) if src else 0)
        self.saved = None

    async def save(self, path):
        self.saved = path
        shutil.copyfile(self.src, path)


def good_result(**kw):
    r = {"ok": True, "valid": True, "n": 10, "length": 4034855, "alphabet": "0421356789", "sha256": "ab" * 32,
         "kind": "word", "seconds": 1.0}
    r.update(kw)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        # a test whose source mentions the n = 10 word is skipped when that file is missing
        if not os.path.exists(W10) and "W10" in inspect.getsource(getattr(self, self._testMethodName)):
            self.skipTest("n = 10 word not found: %s (set SUPERPERM_WORDS)" % W10)
        self.tmp = tempfile.mkdtemp(prefix="botest-")
        self.rec = os.path.join(self.tmp, "records.json")
        shutil.copyfile(os.path.join(BOT, "records.json"), self.rec)
        self.calls = []
        self.announced = []
        self.pinged = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def word_file(self, n, name=None):
        """a tiny .txt with n distinct symbols: enough for the planner (n, length), the fake runners never read it."""
        p = os.path.join(self.tmp, name or ("w%d.txt" % n))
        with open(p, "w") as f:
            f.write(ALPH[:n] + "\n")
        return p

    def att(self, n, name=None):
        return FakeAttachment(name or "w%d.txt" % n, self.word_file(n, "src%d.txt" % n))

    def state(self, runner=None, fetch=None, **cfgkw):
        cfgkw.setdefault("progress_s", 60)
        cfgkw.setdefault("min_edit_s", 0.0)
        cfg = bot.Config(records_path=self.rec, workdir=self.tmp, **cfgkw)
        st = bot.State(cfg, runner or (lambda path, job=None: self.calls.append(path) or good_result()),
                       fetch or (lambda u, t: self.fail("no network in tests")))

        async def announce(view, role_id=None):
            self.announced.append(view)
            self.pinged.append(role_id)
        st.announce = announce
        return st

    def run_async(self, coro):
        return asyncio.run(coro)


class TestUrl(Base):
    def test_accepts(self):
        self.assertEqual(bot.validate_url("https://raw.githubusercontent.com/a/b/main/x.txt.xz"),
                         "https://raw.githubusercontent.com/a/b/main/x.txt.xz")
        self.assertEqual(bot.validate_url("https://github.com/a/b/blob/main/x.txt.xz"),
                         "https://github.com/a/b/raw/main/x.txt.xz")
        self.assertIn("/-/raw/", bot.validate_url("https://gitlab.com/g/p/-/blob/main/x.txt"))

    def test_rejects(self):
        for u in ("http://github.com/a/b/raw/x.txt", "https://evil.example/x.txt", "https://github.com.evil.io/x",
                  "https://user:pw@github.com/x", "https://github.com:8443/x", "ftp://github.com/x",
                  "https://127.0.0.1/x", "https://localhost/x", "file:///etc/passwd", "not a url"):
            with self.assertRaises(bot.UserError, msg=u) as c:
                bot.validate_url(u)
            self.assertTrue(c.exception.hint, u)           # every refusal says what to do


class FakeHttp:
    def __init__(self, status=200, body=b"", headers=None, chunks=None):
        self.status, self.headers, self.body = status, headers or {}, body
        self.pos, self.closed = 0, False

    def read(self, n):
        b = self.body[self.pos:self.pos + n]
        self.pos += len(b)
        return b

    def close(self):
        self.closed = True


class TestDownload(Base):
    def cfg(self, **kw):
        return bot.Config(workdir=self.tmp, **kw)

    def test_ok_and_redirect(self):
        seen = []

        def fetch(url, timeout):
            seen.append(url)
            if len(seen) == 1:
                return FakeHttp(302, headers={"Location": "https://raw.githubusercontent.com/a/b/main/x.txt"})
            return FakeHttp(200, b"abc\n", {"Content-Length": "4"})
        dest = os.path.join(self.tmp, "d")
        self.assertEqual(bot.download("https://github.com/a/b/blob/main/x.txt", dest, self.cfg(), fetch), 4)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"abc\n")
        self.assertEqual(seen[0], "https://github.com/a/b/raw/main/x.txt")

    def test_redirect_to_foreign_host(self):
        fetch = lambda u, t: FakeHttp(302, headers={"Location": "https://evil.example/x"})
        with self.assertRaises(bot.UserError):
            bot.download("https://github.com/a/b/raw/main/x.txt", os.path.join(self.tmp, "d"), self.cfg(), fetch)

    def test_redirect_to_http_or_loop(self):
        fetch = lambda u, t: FakeHttp(302, headers={"Location": "http://raw.githubusercontent.com/x"})
        with self.assertRaises(bot.UserError):
            bot.download("https://github.com/a/b/raw/main/x.txt", os.path.join(self.tmp, "d"), self.cfg(), fetch)
        fetch = lambda u, t: FakeHttp(302, headers={"Location": "https://github.com/loop"})
        with self.assertRaises(bot.UserError) as c:
            bot.download("https://github.com/a/b/raw/main/x.txt", os.path.join(self.tmp, "d"), self.cfg(), fetch)
        self.assertIn("redirects", str(c.exception))

    def test_size_caps(self):
        c = self.cfg(max_download=1000)
        with self.assertRaises(bot.UserError):   # announced
            bot.download("https://github.com/a/b/raw/x.txt", os.path.join(self.tmp, "d"), c,
                         lambda u, t: FakeHttp(200, b"x", {"Content-Length": "5000"}))
        with self.assertRaises(bot.UserError):   # not announced, streamed
            bot.download("https://github.com/a/b/raw/x.txt", os.path.join(self.tmp, "d"), c,
                         lambda u, t: FakeHttp(200, b"x" * 5000))

    def test_deadline_and_status(self):
        c = self.cfg(download_total_s=-1)
        with self.assertRaises(bot.UserError):
            bot.download("https://github.com/a/b/raw/x.txt", os.path.join(self.tmp, "d"), c,
                         lambda u, t: FakeHttp(200, b"x" * 10))
        with self.assertRaises(bot.UserError):
            bot.download("https://github.com/a/b/raw/x.txt", os.path.join(self.tmp, "d"), self.cfg(),
                         lambda u, t: FakeHttp(404))


# ----------------------------------------------------------------------------------------------- the check flow

class TestHandlers(Base):
    def test_about_and_records(self):
        st = self.state()
        i = FakeInteraction()
        self.run_async(bot.handle_about(st, i))
        m = i.of("response")[0]
        self.assertFalse(m.ephemeral)
        txt = m.text
        self.assertIn("superpermutations-bot", txt)
        for need in ("Send Messages", "Embed Links", "Read Message History", "Manage Roles", "restart", "/queue", "/cancel",
                     "/panel", "n ≤ 14", "slow", "fast", "Contributor"):
            self.assertIn(need, txt)
        for gone in ("Create Public Threads", "Threads", "thread"):
            self.assertNotIn(gone, txt)
        i = FakeInteraction()
        self.run_async(bot.handle_records(st, i))
        m = i.of("response")[0]
        self.assertFalse(m.ephemeral)
        self.assertIn("4,034,855", m.text)
        self.assertIn("93,904,936,032", m.text)
        self.assertNotIn("|", m.text)                  # Discord does not render Markdown tables
        self.assertNotIn("---", m.text)

    def test_check_attachment_one_card_flow(self):
        st = self.state()
        i = FakeInteraction(uid=7, name="alice_login", display_name="Alice A")
        a = FakeAttachment("my word.txt.xz", W10)
        self.run_async(bot.handle_check(st, i, attachment=a))
        self.assertTrue(i.response.deferred)
        self.assertTrue(i.response.defer_kw.get("ephemeral"))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0].endswith("upload.txt.xz"))
        # exactly one message in the channel (no thread, no second message), edited to the result
        self.assertEqual(len(i.of("channel")), 1)
        card = i.card
        self.assertGreaterEqual(len(card.history), 2)
        self.assertIn("Check #1", card.texts[0])
        self.assertIn("Valid superpermutation", card.text)
        self.assertIn("4,034,855", card.text)
        self.assertEqual(ui.accent(card.view), ui.COLOURS["valid"])
        # the submitter is a mention (shown as the display name, no ping: allowed_mentions none is checked by the fakes)
        self.assertIn("<@7>", card.text)
        self.assertNotIn("alice_login", card.text)
        self.assertEqual([b.label for b in ui.buttons(card.view)][-1], "Details")
        # the user got one short ephemeral reply with a link to the card
        priv = i.of("followup")
        self.assertEqual(len(priv), 1)
        self.assertTrue(priv[0].ephemeral)
        self.assertIn("Check #1 accepted", priv[0].text)
        self.assertEqual([b.url for b in ui.buttons(priv[0].view)], [card.jump_url])
        self.assertLess(len(priv[0].text), 200)
        self.assertFalse(os.path.exists(os.path.dirname(self.calls[0])), "temp dir removed")
        self.assertEqual(st.sched.jobs, {})

    def test_no_thread_code_left(self):
        for fn in BOT_MODULES + ("ui.py",):
            with open(os.path.join(BOT, fn), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("create_thread", src)
            self.assertNotIn("ThreadSink", src)
        # the display name is kept, and so is the user id
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32))
        i = FakeInteraction(uid=7, name="alice_login", display_name="Alice A")
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), submit=True))
        e = records.best_verified(records.load(self.rec), 9)
        self.assertEqual((e["submitter"], e["submitter_id"]), ("Alice A", "7"))

    def test_card_post_failure_is_reported_and_logged(self):
        st = self.state()
        i = FakeInteraction()
        i.world.send_error = forbidden()
        with self.assertLogs("superperm", "WARNING") as logs:
            self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
        self.assertTrue(any("could not post the card" in x for x in logs.output), logs.output)
        self.assertIn("Could not start", i.private)
        self.assertIn("Send Messages", i.private)
        self.assertEqual((self.calls, st.sched.jobs, i.of("channel")), ([], {}, []))      # nothing ran

    def test_card_message_gone_posts_a_new_card_once(self):
        gate = threading.Event()
        st = self.state(runner=lambda p, job=None: gate.wait(10) and good_result(n=job.n))
        i = FakeInteraction()
        i.world.edit_errors = [not_found()]

        async def go():
            t = asyncio.ensure_future(bot.handle_check(st, i, attachment=self.att(10)))
            await asyncio.sleep(0.5)
            gate.set()
            await t
        with self.assertLogs("superperm", "WARNING") as logs:
            self.run_async(go())
        cards = i.of("channel")
        self.assertEqual(len(cards), 2)                       # the original and ONE new card
        self.assertIn("Valid superpermutation", cards[-1].text)
        self.assertTrue(any("gone" in x for x in logs.output), logs.output)
        # deleted again: no endless reposting, the failure is logged
        st = self.state(runner=lambda p, job=None: gate.wait(10) and good_result(n=job.n))
        i = FakeInteraction()
        i.world.edit_errors = [not_found(), not_found(), not_found()]
        with self.assertLogs("superperm", "WARNING") as logs:
            self.run_async(go())
        self.assertEqual(len(i.of("channel")), 2)
        self.assertTrue(any("not posting it again" in x for x in logs.output), logs.output)

    def test_edit_failure_is_logged_and_the_job_goes_on(self):
        st = self.state()
        i = FakeInteraction()
        i.world.edit_errors = [forbidden()] * 3
        with self.assertLogs("superperm", "WARNING") as logs:
            self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
        self.assertTrue(any("could not edit the card" in x for x in logs.output), logs.output)
        self.assertEqual(len(self.calls), 1)

    def test_check_without_options_opens_the_form(self):
        st = self.state()
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i))
        self.assertFalse(i.response.deferred)
        self.assertEqual(len(i.world.modals), 1)
        self.assertIsInstance(i.world.modals[0], bot.CheckModal)
        self.assertEqual(self.calls, [])

    def test_check_rejects(self):
        st = self.state()
        for kw, need in (({"attachment": FakeAttachment("a.txt", W10), "url": "https://github.com/a/b/raw/x.txt"}, "not both"),
                         ({"attachment": FakeAttachment("a.exe", W10)}, "file name must end"),
                         ({"attachment": FakeAttachment("a.txt", W10, 10 ** 12)}, "larger than 64 MB"),
                         ({"url": "https://evil.example/x.txt"}, "evil.example is not allowed"),
                         ({"url": "https://github.com/a/b/raw/main/x.py"}, "must end in")):
            i = FakeInteraction()
            self.run_async(bot.handle_check(st, i, **kw))
            self.assertIn("Not accepted", i.private, (kw, i.private))
            self.assertIn(need, i.private)
            self.assertEqual(ui.accent(i.private_view), ui.COLOURS["refused"])
            self.assertTrue(i.of("followup")[-1].ephemeral)
            self.assertEqual(i.of("channel"), [])             # a refusal never reaches the channel
        self.assertEqual(self.calls, [])

    def test_check_url_fake_fetch(self):
        with open(W10, "rb") as f:
            data = f.read()
        st = self.state(fetch=lambda u, t: FakeHttp(200, data, {"Content-Length": str(len(data))}))
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, url="https://github.com/a/b/blob/main/w.txt.xz"))
        self.assertIn("Valid superpermutation", i.card.text)

    def test_submit_new_record_and_announce(self):
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32))
        i = FakeInteraction(uid=7, name="alice")
        self.run_async(bot.handle_check(st, i, attachment=FakeAttachment("w.txt.xz", W10), submit=True))
        self.assertIn("new record", i.card.text)
        self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Added ✓", "Details"])
        self.assertTrue(ui.buttons(i.card.view)[0].disabled)
        self.assertEqual(len(self.announced), 1)
        a = "\n".join(ui.texts(self.announced[0]))
        self.assertIn("New record for n = 9", a)
        self.assertIn("408,000", a)
        self.assertIn("<@7>", a)
        self.assertEqual(ui.accent(self.announced[0]), ui.COLOURS["record"])
        link = ui.buttons(self.announced[0])[0]
        self.assertEqual(link.url, i.card.jump_url)                 # the link button opens the job card
        t = records.load(self.rec)
        self.assertEqual(records.best_verified(t, 9)["submitter"], "alice")
        # the same word again: duplicate, no announcement
        i = FakeInteraction(uid=8, name="bob")
        self.run_async(bot.handle_check(st, i, attachment=FakeAttachment("w.txt.xz", W10), submit=True))
        self.assertIn("already listed", i.card.text)
        self.assertEqual(len(self.announced), 1)

    def test_no_submit_without_flag_and_invalid(self):
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=1, sha256="ee" * 32))
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=FakeAttachment("w.txt.xz", W10), submit=False))
        self.assertIsNone(records.best_verified(records.load(self.rec), 9))
        st = self.state(runner=lambda p, job=None: {"ok": False, "valid": False, "n": 9, "length": 1, "missing": 5,
                                                    "foreign_bytes": 0, "sha256": "ee" * 32})
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=FakeAttachment("w.txt.xz", W10), submit=True))
        self.assertIn("Not a superpermutation", i.card.text)
        self.assertEqual(ui.accent(i.card.view), ui.COLOURS["invalid"])
        self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Details"])      # nothing to add
        self.assertIsNone(records.best_verified(records.load(self.rec), 9))

    def test_runner_crash_is_reported(self):
        def boom(p, job=None):
            raise RuntimeError("boom")
        st = self.state(runner=boom)
        i = FakeInteraction()
        with self.assertLogs("superperm", "WARNING"):
            self.run_async(bot.handle_check(st, i, attachment=FakeAttachment("w.txt.xz", W10)))
        self.assertIn("internal error", i.card.text)
        self.assertEqual(ui.accent(i.card.view), ui.COLOURS["refused"])

    def test_refused_file_shows_only_the_reason(self):
        res = {"ok": False, "valid": False, "kind": "spra", "n": 8, "length": 46225, "missing": 40320,
               "error": "SPRA decoder refused the file: spra: extra bytes after the last token (at letter 46225, tuple 7702)"}
        st = self.state(runner=lambda p, job=None: res)
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(8, "w8.txt")))
        t = i.card.text
        self.assertIn("File refused", t)
        self.assertIn("extra bytes after the last token (at letter 46,225)", t)
        for never in ("40,320", "Missing", "DIFFERS"):
            self.assertNotIn(never, t)

    def test_plan_counts_letters_not_bytes(self):
        p = os.path.join(self.tmp, "w5.txt")
        with open(p, "w") as f:
            f.write("0" * 152 + "1" + "\n")             # 153 letters, 154 bytes
        self.assertEqual(check_any.peek_full(p)[2], 153)
        self.assertEqual(check_any.count_letters(p), 153)
        with open(p, "wb") as f:
            f.write(b"01234\r\n0123\n\n")
        self.assertEqual(check_any.count_letters(p), 9)


class TestForm(Base):
    def modal(self, st, **kw):
        m = bot.CheckModal(st, **kw)
        return m

    def test_modal_layout(self):
        m = self.modal(self.state())
        d = m.to_dict()
        self.assertLessEqual(len(m.title), 45)
        labels = d["components"]
        self.assertEqual([c["type"] for c in labels], [18, 18, 18])                # three Labels
        self.assertEqual([c["component"]["type"] for c in labels], [19, 4, 3])     # FileUpload, TextInput, Select
        self.assertEqual([o["value"] for o in labels[2]["component"]["options"]], ["public", "private"])
        self.assertEqual([o["default"] for o in labels[2]["component"]["options"]], [True, False])   # public unless chosen
        self.assertTrue(all(len(c["label"]) <= 45 for c in labels))
        self.assertTrue(all(len(c.get("description", "")) <= 100 for c in labels))
        self.assertFalse(labels[0]["component"]["required"] or labels[1]["component"]["required"])
        self.assertEqual([o["default"] for o in self.modal(self.state(), visibility="private").to_dict()["components"][2]
                          ["component"]["options"]], [False, True])

    def test_modal_submit_with_a_file(self):
        st = self.state()
        m = self.modal(st)
        m.file.component._values = [FakeAttachment("form word.txt.xz", W10)]
        i = FakeInteraction(uid=3, name="carol")
        self.run_async(m.on_submit(i))
        self.assertEqual(len(self.calls), 1)
        self.assertIn("Valid superpermutation", i.card.text)
        self.assertFalse(i.world.modals)                                      # no second form

    def test_modal_submit_with_a_link(self):
        with open(W10, "rb") as f:
            data = f.read()
        st = self.state(fetch=lambda u, t: FakeHttp(200, data, {"Content-Length": str(len(data))}))
        m = self.modal(st)
        m.link.component._value = "  https://github.com/a/b/blob/main/w.txt.xz  "
        i = FakeInteraction()
        self.run_async(m.on_submit(i))
        self.assertIn("Valid superpermutation", i.card.text)

    def test_modal_needs_exactly_one(self):
        st = self.state()
        m = self.modal(st)
        i = FakeInteraction()
        self.run_async(m.on_submit(i))                                        # nothing given
        self.assertIn("nothing to check", i.private)
        self.assertFalse(i.world.modals)
        m = self.modal(st)
        m.file.component._values = [FakeAttachment("a.txt", W10)]
        m.link.component._value = "https://github.com/a/b/raw/main/x.txt"
        i = FakeInteraction()
        self.run_async(m.on_submit(i))
        self.assertIn("not both", i.private)
        self.assertEqual(self.calls, [])

    def test_panel(self):
        st = self.state()
        # a normal member is refused
        i = FakeInteraction(uid=1)
        self.run_async(bot.handle_panel(st, i))
        self.assertIn("Manage Server", i.private)
        self.assertEqual(i.of("channel"), [])
        # a member with Manage Server posts it in the channel
        i = FakeInteraction(uid=2, perms={"manage_guild": True})
        self.run_async(bot.handle_panel(st, i))
        self.assertEqual(len(i.of("channel")), 1)
        panel = i.of("channel")[0]
        self.assertEqual([b.label for b in ui.buttons(panel.view)], ["Check a word"])
        self.assertTrue(i.of("response")[0].ephemeral)
        # the button opens the form for anybody; its custom_id is fixed, so it survives a restart
        b = ui.buttons(panel.view)[0]
        self.assertEqual(b.custom_id, "sp:panel:check")
        c = FakeInteraction(uid=9)
        build = bot.build_client(st)
        self.run_async(ui.PanelButton().callback(c))
        self.assertIsInstance(c.world.modals[0], bot.CheckModal)
        self.assertEqual(build.tree.get_command("panel").default_permissions.manage_guild, True)


# ----------------------------------------------------------------------------------------------- buttons

class TestButtons(Base):
    def finished(self, st, uid=1, name="ann", **kw):
        i = FakeInteraction(uid=uid, name=name, **kw)
        self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
        return i

    def test_add_to_records_button(self):
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32), contributor_role=777)

        async def go():
            i = FakeInteraction(uid=1, name="ann", display_name="Ann Display")
            await bot.handle_check(st, i, attachment=self.att(10))
            self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Add to records", "Details"])
            self.assertEqual(ui.buttons(i.card.view)[0].style, discord.ButtonStyle.success)
            self.assertIsNone(records.best_verified(records.load(self.rec), 9))
            # somebody else cannot add it
            other = FakeInteraction(uid=2, name="bob")
            await bot.handle_job_button(st, "add", 1, other)
            self.assertIn("Only the submitter", other.private)
            self.assertIsNone(records.best_verified(records.load(self.rec), 9))
            # the submitter can (the click interaction carries the member: the role goes to them)
            click = FakeInteraction(uid=1, name="ann", display_name="Ann Display")
            click.guild = FakeGuild([FakeRole(777)])
            await bot.handle_job_button(st, "add", 1, click)
            e = records.best_verified(records.load(self.rec), 9)
            self.assertEqual((e["submitter"], e["submitter_id"]), ("Ann Display", "1"))
            self.assertEqual(len(click.user.added), 1)
            self.assertEqual(len(self.announced), 1)
            self.assertIn("new record", click.private)
            self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Added ✓", "Details"])
            self.assertTrue(ui.buttons(i.card.view)[0].disabled)
            self.assertIn("new record", i.card.text)                  # the card says what happened
            # a second press does nothing
            again = FakeInteraction(uid=1, name="ann")
            await bot.handle_job_button(st, "add", 1, again)
            self.assertIn("Already added", again.private)
            self.assertEqual(len(self.announced), 1)
        self.run_async(go())

    def test_add_button_only_when_the_word_would_enter_the_table(self):
        # n = 10 word of the record's own length: not shorter, no button
        st = self.state()
        i = self.finished(st)
        self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Details"])
        # shorter than the record: button
        st = self.state(runner=lambda p, job=None: good_result(length=4034000, sha256="12" * 32))
        i = self.finished(st)
        self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Add to records", "Details"])
        # an n without a verified entry: first verified entry, button
        st = self.state(runner=lambda p, job=None: good_result(n=5, length=153, sha256="34" * 32))
        i = self.finished(st)
        self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Add to records", "Details"])

    def test_details_button(self):
        st = self.state(runner=lambda p, job=None: good_result(spra_version=2, extra_key="x"))
        i = self.finished(st)
        c = FakeInteraction(uid=5, name="anybody")
        self.run_async(bot.handle_job_button(st, "details", 1, c))
        m = c.of("response")[0]
        self.assertTrue(m.ephemeral)
        self.assertIn("ab" * 32, m.text)                  # the full SHA-256
        self.assertIn("extra_key", m.text)                # every key of the result
        self.assertIn("4,034,855", m.text)

    def test_cancel_button_permissions_and_effect(self):
        gate = threading.Event()

        def runner(path, job=None):
            while not gate.is_set():
                if job.cancel_event.is_set():
                    return {"ok": False, "valid": False, "cancelled": True, "error": "cancelled by %s" % job.cancelled_by}
                time.sleep(0.02)
            return good_result(n=job.n)
        st = self.state(runner=runner)

        async def go():
            ia = FakeInteraction(uid=1, name="ann")
            ta = asyncio.ensure_future(bot.handle_check(st, ia, attachment=self.att(10)))
            await asyncio.sleep(0.4)
            # the running card has a Cancel button (red) and nothing else
            self.assertEqual([(b.label, b.style) for b in ui.buttons(ia.card.view)], [("Cancel", discord.ButtonStyle.danger)])
            self.assertEqual(ui.accent(ia.card.view), ui.COLOURS["running"])
            c = FakeInteraction(uid=2, name="bob")
            await bot.handle_job_button(st, "cancel", 1, c)
            self.assertIn("Not yours", c.private)
            self.assertFalse(st.sched.jobs[1].cancel_event.is_set())
            c = FakeInteraction(uid=3, name="zed", display_name="Zed Staff", perms={"manage_messages": True})
            await bot.handle_job_button(st, "cancel", 1, c)
            self.assertIn("Cancelling", c.private)
            await ta
            return ia
        ia = self.run_async(go())
        self.assertIn("Cancelled", ia.card.text)
        self.assertIn("Cancelled by Zed Staff", ia.card.text)
        self.assertEqual(ui.accent(ia.card.view), ui.COLOURS["cancelled"])

    def test_cancel_own_waiting_job_with_the_command(self):
        gate = threading.Event()
        st = self.state(runner=lambda p, job=None: gate.wait(10) and good_result(n=job.n))

        async def go():
            ia, ib = FakeInteraction(uid=1, name="ann"), FakeInteraction(uid=2, name="bob")
            ta = asyncio.ensure_future(bot.handle_check(st, ia, attachment=self.att(10)))
            await asyncio.sleep(0.3)
            tb = asyncio.ensure_future(bot.handle_check(st, ib, attachment=self.att(11)))
            await asyncio.sleep(0.3)
            self.assertIn("Position 1", ib.card.text)
            c = FakeInteraction(uid=2, name="bob")
            await bot.handle_cancel(st, c, 2)
            self.assertIn("being cancelled", c.private)
            await tb
            gate.set()
            await ta
            return ia, ib
        ia, ib = self.run_async(go())
        self.assertIn("Cancelled by bob before it started", ib.card.text)
        self.assertIn("Valid", ia.card.text)

    def test_unknown_job_and_cards_from_before_a_restart(self):
        st = self.state()
        for act in ("cancel", "add", "details"):
            c = FakeInteraction()
            self.run_async(bot.handle_job_button(st, act, 99, c))
            self.assertIn("Job #99 is gone", c.private)
            self.assertIn("restarted", c.private)
        c = FakeInteraction()
        self.run_async(bot.handle_cancel(st, c, 99))
        self.assertIn("no waiting or running job #99", c.private)
        # a finished job: cancel says there is nothing to cancel
        self.finished(st)
        c = FakeInteraction()
        self.run_async(bot.handle_job_button(st, "cancel", 1, c))
        self.assertIn("no waiting or running job #1", c.private)

    def test_dynamic_items_dispatch_to_the_handlers(self):
        st = self.state()
        bot.build_client(st)                       # sets ui.HANDLERS
        i = self.finished(st)
        c = FakeInteraction(uid=4, name="x")
        btn = ui.buttons(i.card.view)[-1]
        self.assertEqual(btn.custom_id, "sp:details:1")
        m = ui.JobButton.__discord_ui_compiled_template__.fullmatch(btn.custom_id)
        item = self.run_async(ui.JobButton.from_custom_id(c, btn, m))
        self.run_async(item.callback(c))
        self.assertIn("Details", c.private)
        # the records select
        sel = ui.RecordsSelect(["13"])
        sel.item._values = ["13"]
        c = FakeInteraction()
        self.run_async(sel.callback(c))
        self.assertIn("n = 13", c.private)
        self.assertTrue(c.of("response")[0].ephemeral)
        sel.item._values = ["99"]
        c = FakeInteraction()
        self.run_async(sel.callback(c))
        self.assertIn("nothing for n = 99", c.private)


# ----------------------------------------------------------------------------------------------- limits, lanes, queue

class TestLimits(Base):
    def test_max_n_refused_before_any_work(self):
        st = self.state()
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(15)))
        self.assertIn("n = 15 is above this bot's limit (n ≤ 14). Nothing was run.", i.private)
        self.assertEqual((self.calls, i.of("channel"), st.sched.jobs), ([], [], {}))
        # configurable: with 12 the 13 is refused, 12 accepted
        st = self.state(max_n=12)
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(13)))
        self.assertIn("limit (n ≤ 12)", i.private)
        self.assertEqual(self.calls, [])
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(12)))
        self.assertEqual(len(self.calls), 1)
        # never above what the registry builds
        self.assertEqual(bot.Config(max_n=99).max_n, 14)

    def test_env_vars_are_read(self):
        old = {k: os.environ.get(k) for k in ("SUPERPERM_MAX_N", "SUPERPERM_QUEUE", "SUPERPERM_MAX_HOURS",
                                              "SUPERPERM_MBPS", "SUPERPERM_MAX_SHARDS", "SUPERPERM_CONTRIBUTOR_ROLE")}
        try:
            os.environ.update({"SUPERPERM_MAX_N": "12", "SUPERPERM_QUEUE": "3", "SUPERPERM_MAX_HOURS": "2",
                               "SUPERPERM_MBPS": "50", "SUPERPERM_MAX_SHARDS": "4",
                               "SUPERPERM_CONTRIBUTOR_ROLE": "12345"})
            c = bot.Config()
            self.assertEqual((c.max_n, c.queue_len, c.max_hours, c.mbps, c.max_shards, c.contributor_role),
                             (12, 3, 2.0, 50.0, 4, 12345))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        c = bot.Config()
        self.assertEqual((c.max_n, c.queue_len, c.max_hours, c.max_shards, c.contributor_role), (14, 6, 6.0, 8, None))
        self.assertEqual((c.progress_s, c.progress_min_s, c.min_edit_s), (60.0, 5.0, 5.0))

    def test_time_estimate_refusal(self):
        # a word whose length is known (.txt): 14 letters at 1e-6 M letters/s = 14 s > 0.001 h
        st = self.state(mbps=1e-6, max_hours=0.001)
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(14)))
        self.assertIn("estimated time", i.private)
        self.assertIn("SUPERPERM_MAX_HOURS", i.private)
        self.assertEqual((self.calls, i.of("channel")), ([], []))
        # a compressed word has no stated length: the estimate comes from n
        st = self.state(mbps=0.001, max_hours=0.5)
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=FakeAttachment("w.txt.xz", W10)))
        self.assertIn("estimated time", i.private)
        self.assertEqual(self.calls, [])
        # and the same word is accepted with the default throughput
        i = FakeInteraction()
        self.run_async(bot.handle_check(self.state(), i, attachment=FakeAttachment("w.txt.xz", W10)))
        self.assertEqual(len(self.calls), 1)

    @need_n14
    def test_plan_estimates_use_shards(self):
        cfg = bot.Config(records_path=self.rec, mem_gb=14, mbps=30, max_hours=6)
        recipe = N14
        p = bot.plan_job(recipe, cfg)
        self.assertEqual((p.kind, p.n, p.shards, p.lane, p.length), ("recipe", 14, 1, "slow", N14_LENGTH))
        self.assertAlmostEqual(p.est_pass_s, N14_LENGTH / 30e6, delta=1)
        cfg = bot.Config(records_path=self.rec, mem_gb=6, mbps=30, max_hours=6)
        p = bot.plan_job(recipe, cfg)
        self.assertGreater(p.shards, 1)
        self.assertAlmostEqual(p.est_total_s, p.shards * p.est_pass_s)
        # too little memory for max_shards passes
        with self.assertRaises(bot.UserError) as c:
            bot.plan_job(recipe, bot.Config(records_path=self.rec, mem_gb=2))
        self.assertIn("passes", str(c.exception))
        # many shards need more time than allowed
        with self.assertRaises(bot.UserError) as c:
            bot.plan_job(recipe, bot.Config(records_path=self.rec, mem_gb=3.4, max_hours=2))
        self.assertIn("estimated time", str(c.exception))

    def test_lane_routing(self):
        for n, lane in ((6, "fast"), (10, "fast"), (12, "fast"), (13, "slow"), (14, "slow")):
            cfg = bot.Config(records_path=self.rec)
            self.assertEqual(bot.plan_job(self.word_file(n), cfg).lane, lane, n)

    def test_fast_job_not_stuck_behind_slow(self):
        gate = threading.Event()
        order = []

        def runner(path, job=None):
            order.append(job.lane)
            if job.lane == "slow":
                gate.wait(10)
            return good_result(n=job.n)
        st = self.state(runner=runner)

        async def go():
            slow = asyncio.ensure_future(bot.handle_check(st, (i_slow := FakeInteraction(uid=1)),
                                                          attachment=self.att(13)))
            await asyncio.sleep(0.4)       # the slow job runs and is blocked
            await bot.handle_check(st, (i_fast := FakeInteraction(uid=2)), attachment=self.att(10))
            self.assertIn("Valid", i_fast.card.text)          # finished while the slow one is still running
            self.assertFalse(slow.done())
            self.assertIn("Check #1", i_slow.card.text)
            self.assertEqual(st.sched.lanes["slow"].running.id, 1)
            gate.set()
            await slow
            return i_slow, i_fast
        i_slow, i_fast = self.run_async(go())
        self.assertEqual(order, ["slow", "fast"])
        self.assertIn("n = 13", i_slow.card.texts[0])
        self.assertIn("n = 10", i_fast.card.texts[0])

    def test_per_user_per_lane_and_queue_full(self):
        gate = threading.Event()
        st = self.state(runner=lambda p, job=None: gate.wait(10) and good_result(n=job.n), queue_len=1)

        async def go():
            ix = {k: FakeInteraction(uid=u) for k, u in (("a1", 1), ("a2", 1), ("a_slow", 1), ("b", 2), ("c", 3))}
            t1 = asyncio.ensure_future(bot.handle_check(st, ix["a1"], attachment=self.att(10)))
            await asyncio.sleep(0.4)                                   # a1 runs
            await bot.handle_check(st, ix["a2"], attachment=self.att(10))      # same user, same lane
            t_slow = asyncio.ensure_future(bot.handle_check(st, ix["a_slow"], attachment=self.att(13)))  # other lane: ok
            t3 = asyncio.ensure_future(bot.handle_check(st, ix["b"], attachment=self.att(10)))   # waits (1 of 1)
            await asyncio.sleep(0.4)
            await bot.handle_check(st, ix["c"], attachment=self.att(10))       # fast lane full
            self.assertEqual(len(st.sched.lanes["fast"].waiting), 1)
            gate.set()
            await asyncio.gather(t1, t3, t_slow)
            return ix
        ix = self.run_async(go())
        self.assertIn("already have a check", ix["a2"].private)
        self.assertIn("fast lane", ix["a2"].private)
        self.assertIn("cancel it with /cancel", ix["a2"].private)
        self.assertIn("lane is full", ix["c"].private)
        self.assertIn("Valid", ix["a1"].card.text)
        self.assertIn("Valid", ix["a_slow"].card.text)
        self.assertIn("Valid", ix["b"].card.text)
        self.assertIn("Position 1 in the fast lane", ix["b"].card.texts[0])
        # refused users got no card
        self.assertIsNone(ix["a2"].card)
        self.assertIsNone(ix["c"].card)

    def test_queue_command(self):
        gate = threading.Event()
        st = self.state(runner=lambda p, job=None: gate.wait(10) and good_result(n=job.n))

        async def go():
            ia = FakeInteraction(uid=1, name="ann")
            t1 = asyncio.ensure_future(bot.handle_check(st, ia, attachment=self.att(10)))
            await asyncio.sleep(0.3)
            t2 = asyncio.ensure_future(bot.handle_check(st, FakeInteraction(uid=2, name="bob"), attachment=self.att(11)))
            await asyncio.sleep(0.3)
            q = FakeInteraction()
            await bot.handle_queue(st, q)
            gate.set()
            await asyncio.gather(t1, t2)
            return q, ia
        q, ia = self.run_async(go())
        m = q.of("response")[0]
        self.assertTrue(m.ephemeral)
        self.assertIn("#1 · n = 10 · <@1>", m.text)
        self.assertIn("1. #2 · n = 11 · <@2>", m.text)
        self.assertIn("slow lane (n = 13, 14)**: idle", m.text)
        self.assertIn("in memory", m.text)
        self.assertEqual([b.url for b in ui.buttons(m.view)], [ia.card.jump_url])    # link to the running job's card


# ----------------------------------------------------------------------------------------------- progress

class TestProgress(Base):
    def slow_runner(self, seconds):
        def runner(path, job=None):
            t0 = time.time()
            k = 0
            while time.time() - t0 < seconds:
                k += 1
                job.progress = (1, 1, k * 1000)
                time.sleep(0.02)
            return good_result(n=job.n)
        return runner

    def test_card_edited_not_flooded(self):
        st = self.state(runner=self.slow_runner(1.5), progress_s=0.2, min_edit_s=0.5)
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
        self.assertEqual(len(i.of("channel")), 1)                 # never one message per update
        card = i.card
        running = [t for t in card.texts if "letters" in t and "elapsed" in t]
        self.assertGreaterEqual(len(running), 1)
        self.assertGreaterEqual(len(card.history) - 1, 3)         # start, progress, result
        gaps = [b - a for a, b in zip(card.times, card.times[1:])]
        self.assertGreaterEqual(min(gaps[1:]), 0.45, gaps)        # at most one edit per min_edit_s (the post is not an edit)
        self.assertIn("Valid", card.text)

    def test_a_job_shorter_than_the_interval_is_edited_to_running_and_result_only(self):
        st = self.state(runner=self.slow_runner(0.4), progress_s=60)
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
        self.assertLessEqual(len(i.card.history), 3)              # queued, (running), result
        self.assertIn("Valid", i.card.text)

    def test_unchanged_card_is_not_edited(self):
        gate = threading.Event()
        st = self.state(runner=lambda p, job=None: gate.wait(10) and good_result(n=job.n), progress_s=0.1)

        async def go():
            i = FakeInteraction()
            t = asyncio.ensure_future(bot.handle_check(st, i, attachment=self.att(10)))
            await asyncio.sleep(1.0)               # ten ticks; the runner reports no progress, so nothing new to show
            n = len(i.card.history)
            gate.set()
            await t
            return i, n
        i, n = self.run_async(go())
        self.assertLessEqual(n, 3)                 # queued + at most the start and one elapsed-time change
        self.assertIn("Valid", i.card.text)

    def test_core_progress_callback_once_per_chunk(self):
        import core
        import io
        import lzma
        with lzma.open(W10, "rb") as f:
            data = f.read().rstrip(b"\n")
        seen = []
        res = core.check_stream(io.BytesIO(data).read, core.alphabet_of(W10), None, 1, None, progress=seen.append)
        self.assertTrue(res["valid"])
        self.assertEqual(seen[-1], len(data))
        self.assertEqual(seen, sorted(seen))
        self.assertLessEqual(len(seen), len(data) // core.CHUNK + 1)    # per chunk, not per byte

    def test_start_hook_failure_is_logged_and_the_check_runs(self):
        st = self.state()
        sched = st.sched

        async def go():
            job = sched.new_job(1, "u", "x", "p", bot.Plan("word", 10, None, 1, 100, 1, 1))
            sched.admit(job)

            async def bad(j):
                raise RuntimeError("card gone")
            job.on_start = bad
            job.ready.set()
            return await job.fut
        with self.assertLogs("superperm", "WARNING") as logs:
            res = self.run_async(go())
        self.assertTrue(res["ok"])
        self.assertTrue(any("could not update the card" in x for x in logs.output), logs.output)


class TestCard(Base):
    """the throttled editor on its own, with a fake clock."""

    def test_rate_and_collapse(self):
        class Clock:
            t = 100.0

            def __call__(self):
                return self.t
        clock = Clock()
        w = World()
        ch = FakeChannel(w)
        card = bot.Card(ch, 5.0, clock)
        views = [ui.notice_card("t", "v%d" % k) for k in range(5)]
        make = lambda k: (lambda: views[k])

        async def go():
            await card.post(views[0])
            self.assertTrue(await card.update(make(1)))              # the first edit after the post is free
            self.assertFalse(await card.update(make(2)))             # too early: skipped (progress refresh)
            self.assertEqual(w.edits, 1)
            clock.t += 5.0
            self.assertTrue(await card.update(make(2)))
            self.assertTrue(await card.update(make(2)))              # unchanged: no edit
            self.assertEqual(w.edits, 2)
        self.run_async(go())

    def test_state_change_waits_for_its_turn(self):
        w = World()
        card = bot.Card(FakeChannel(w), 0.3)
        a, b = ui.notice_card("t", "a"), ui.notice_card("t", "b")

        async def go():
            await card.post(ui.notice_card("t", "0"))
            await card.update(lambda: a)
            t0 = time.monotonic()
            await card.update(lambda: b, wait=True)
            return time.monotonic() - t0
        waited = self.run_async(go())
        self.assertGreaterEqual(waited, 0.25)
        self.assertEqual(w.edits, 2)


# ----------------------------------------------------------------------------------------------- contributor role

class TestRole(Base):
    def new_record_runner(self):
        return lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32)

    def test_role_given_on_new_record(self):
        st = self.state(runner=self.new_record_runner(), contributor_role=777)
        i = FakeInteraction(name="alice")
        i.guild = FakeGuild([FakeRole(777)])
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), submit=True))
        self.assertEqual(len(i.user.added), 1)
        role, reason = i.user.added[0]
        self.assertEqual(role.id, 777)
        self.assertIn("superpermutations-bot", reason)
        self.assertIn("Contributor role given", i.card.text)
        self.assertIn("new record", i.card.text)

    def test_role_given_even_if_role_object_not_cached(self):
        st = self.state(runner=self.new_record_runner(), contributor_role=778)
        i = FakeInteraction(name="alice")
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), submit=True))
        self.assertEqual(i.user.added[0][0].id, 778)

    def test_role_failure_is_reported_not_raised(self):
        st = self.state(runner=self.new_record_runner(), contributor_role=777)
        i = FakeInteraction(name="alice", add_roles_error=forbidden())
        i.guild = FakeGuild([FakeRole(777)])
        with self.assertLogs("superperm", "WARNING"):
            self.run_async(bot.handle_check(st, i, attachment=self.att(10), submit=True))
        self.assertIn("could not give the Contributor role", i.card.text)
        self.assertIn("Manage Roles", i.card.text)
        self.assertIn("new record", i.card.text)                     # the record itself is kept
        self.assertEqual(records.best_verified(records.load(self.rec), 9)["submitter"], "alice")

    def test_no_role_when_not_a_record_not_submit_not_configured_or_dm(self):
        # duplicate (second time): no role again
        st = self.state(runner=self.new_record_runner(), contributor_role=777)
        a = FakeInteraction(name="alice")
        self.run_async(bot.handle_check(st, a, attachment=self.att(10), submit=True))
        b = FakeInteraction(name="bob")
        self.run_async(bot.handle_check(st, b, attachment=self.att(10), submit=True))
        self.assertEqual((len(a.user.added), len(b.user.added)), (1, 0))
        # no submit flag
        st = self.state(runner=lambda p, job=None: good_result(n=8, length=1, sha256="12" * 32), contributor_role=777)
        c = FakeInteraction()
        self.run_async(bot.handle_check(st, c, attachment=self.att(10), submit=False))
        self.assertEqual(c.user.added, [])
        # role not configured
        st = self.state(runner=lambda p, job=None: good_result(n=7, length=1, sha256="34" * 32))
        d = FakeInteraction()
        self.run_async(bot.handle_check(st, d, attachment=self.att(10), submit=True))
        self.assertEqual(d.user.added, [])
        self.assertNotIn("Contributor", d.card.text)
        # invalid word
        st = self.state(runner=lambda p, job=None: {"ok": False, "valid": False, "n": 6, "length": 1}, contributor_role=777)
        e = FakeInteraction()
        self.run_async(bot.handle_check(st, e, attachment=self.att(10), submit=True))
        self.assertEqual(e.user.added, [])
        # run in a direct message (no guild): the server cannot be found, the card says so, no crash
        st = self.state(runner=lambda p, job=None: good_result(n=5, length=1, sha256="56" * 32), contributor_role=777)
        f = FakeInteraction(guild=False)
        self.run_async(bot.handle_check(st, f, attachment=self.att(10), submit=True))
        self.assertIn("Contributor role not given: use the server", f.card.text)

    def test_already_has_role_and_no_role_removal_anywhere(self):
        st = self.state(runner=self.new_record_runner(), contributor_role=777)
        i = FakeInteraction(name="alice", roles=[FakeRole(777)])
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), submit=True))
        self.assertEqual(i.user.added, [])
        self.assertIn("already have", i.card.text)
        for fn in BOT_MODULES + ("ui.py",):
            with open(os.path.join(BOT, fn), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("remove_roles", src)
            self.assertNotIn(".roles =", src)


# ----------------------------------------------------------------------------------------------- the real supervisor

STUB = '''
import json, sys, time
mode = sys.argv[1]
if mode == "ok":
    for k in (1, 2):
        print("#P " + json.dumps({"pass": k, "passes": 2, "letters": 1000 * k}), flush=True)
        time.sleep(0.3)
    print(json.dumps({"ok": True, "valid": True, "n": 3, "length": 9}), flush=True)
elif mode == "hang":
    print("#P " + json.dumps({"pass": 1, "passes": 1, "letters": 5}), flush=True)
    time.sleep(60)
elif mode == "nothing":
    pass
elif mode == "garbage":
    print("#P not json", flush=True)
    print("#P " + json.dumps({"pass": 1}), flush=True)
    print(json.dumps({"ok": True, "valid": True, "n": 3, "length": 9}), flush=True)
'''


class TestSupervise(Base):
    def setUp(self):
        super().setUp()
        self.stub = os.path.join(self.tmp, "stub.py")
        with open(self.stub, "w") as f:
            f.write(STUB)

    def job(self, est_pass_s=100.0, shards=1):
        plan = bot.Plan("word", 10, None, shards, 1000, est_pass_s, est_pass_s * shards)
        return bot.Job(1, 1, "u", "x", "p", plan)

    def cmd(self, mode):
        return [sys.executable, "-I", self.stub, mode]

    def test_progress_lines_and_result(self):
        job = self.job(shards=2)
        res = bot.supervise(self.cmd("ok"), job, bot.Config(records_path=self.rec))
        self.assertTrue(res["ok"])
        self.assertEqual(job.progress, (2, 2, 2000))

    def test_garbage_lines_ignored_and_no_result(self):
        job = self.job()
        self.assertTrue(bot.supervise(self.cmd("garbage"), job, bot.Config())["ok"])
        res = bot.supervise(self.cmd("nothing"), self.job(), bot.Config())
        self.assertFalse(res["ok"])
        self.assertIn("without a result", res["error"])

    def test_pass_timeout_kills_the_child(self):
        cfg = bot.Config(pass_factor=1.5, startup_s=0.0)
        job = self.job(est_pass_s=0.4)                       # limit 0.6 s
        t0 = time.time()
        res = bot.supervise(self.cmd("hang"), job, cfg)
        self.assertLess(time.time() - t0, 10)               # not the 60 s of the child
        self.assertTrue(res["timeout"])
        self.assertFalse(res["ok"])
        self.assertIn("pass 1/1 took more than", res["error"])
        self.assertIn("1.5 x its estimate", res["error"])

    def test_total_timeout_kills_the_child(self):
        cfg = bot.Config(max_hours=0.0002, pass_factor=1000, startup_s=1000)   # 0.72 s in all
        t0 = time.time()
        res = bot.supervise(self.cmd("hang"), self.job(est_pass_s=1000), cfg)
        self.assertLess(time.time() - t0, 10)
        self.assertTrue(res["timeout"])
        self.assertIn("SUPERPERM_MAX_HOURS", res["error"])

    def test_cancel_kills_the_child(self):
        job = self.job()
        threading.Timer(0.8, job.cancel, args=("carol",)).start()
        t0 = time.time()
        res = bot.supervise(self.cmd("hang"), job, bot.Config())
        self.assertLess(time.time() - t0, 10)
        self.assertTrue(res["cancelled"])
        self.assertIn("cancelled by carol", res["error"])

    def test_timeout_reported_on_the_card(self):
        timeout = {"ok": False, "valid": False, "timeout": True, "error": "timed out: pass 1/1 took more than 1 min"}
        st = self.state(runner=lambda p, job=None: timeout)
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
        self.assertIn("Stopped, no verdict", i.card.text)
        self.assertIn("timed out", i.card.text)
        self.assertEqual(ui.accent(i.card.view), ui.COLOURS["stopped"])

    def test_real_runner_sharded_on_n10_word(self):
        """check_any.py as a child with --shards 3 --progress, through supervise, on the real n = 10 word."""
        cfg = bot.Config(records_path=self.rec, mem_gb=3, mbps=1.0)
        plan = bot.plan_job(W10, cfg)
        plan.shards = 3
        job = bot.Job(1, 1, "u", "w", W10, plan)
        res = bot.subprocess_runner(cfg)(W10, job)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["n"], res["length"], res["shards"]), (10, 4034855, 3))
        self.assertEqual(res["sha256"], "542a3778662f972a4ea7b9461a9334775f31303a0d8b1a7913169d8beea444ff")
        self.assertEqual(job.progress[:2], (3, 3))

    def test_real_runner_on_n10_word_through_handler(self):
        st = bot.State(bot.Config(records_path=self.rec, workdir=self.tmp, mem_gb=3, progress_s=60, min_edit_s=0), None,
                       lambda u, t: self.fail("network"))
        i = FakeInteraction()
        self.run_async(bot.handle_check(st, i, attachment=FakeAttachment("w10.txt.xz", W10)))
        self.assertIn("Valid superpermutation", i.card.text, [m.text for m in i.world.log])
        self.assertIn("4,034,855", i.card.text)

    @need_n14
    def test_real_runner_refuses_too_little_memory(self):
        r = bot.subprocess_runner(bot.Config(mem_gb=2))
        res = r(N14)
        self.assertFalse(res["ok"])
        self.assertIn("passes", res["error"])


class TestClient(Base):
    def test_commands_registered(self):
        c = bot.build_client(self.state())
        self.assertEqual({x.name for x in c.tree.get_commands()}, {"check", "queue", "cancel", "records", "about", "panel", "settings", "history"})
        for n in ("check", "queue", "cancel", "records", "about", "settings", "history"):       # usable in a direct message with the bot
            ctxs = c.tree.get_command(n).allowed_contexts
            self.assertTrue(ctxs.guild and ctxs.dm_channel and not ctxs.private_channel, n)
        p = c.tree.get_command("panel")
        self.assertTrue(p.guild_only)
        self.assertTrue(p.default_permissions.manage_guild)

    def test_no_privileged_intents(self):
        c = bot.build_client(self.state())
        self.assertFalse(c.intents.members or c.intents.presences or c.intents.message_content)

    def test_dynamic_items_are_registered_for_restarts(self):
        src = inspect.getsource(bot.build_client)
        call = src[src.index("add_dynamic_items("):src.index(")", src.index("add_dynamic_items("))]
        for name in ("JobButton", "PanelButton", "RecordsSelect", "SettingsSelect", "XButton", "XSelect", "XUserSelect"):
            self.assertIn("ui.%s" % name, call)
        for cls, cid in ((ui.JobButton, "sp:cancel:12"), (ui.PanelButton, "sp:panel:check"), (ui.RecordsSelect, "sp:records:n")):
            self.assertTrue(cls.__discord_ui_compiled_template__.fullmatch(cid), cid)

    def test_every_message_has_allowed_mentions_none(self):
        # the fakes refuse any send or edit without allowed_mentions none; this checks the source for the other calls
        src = bot_source()
        calls = re.findall(r"\b(?:send_message|followup\.send|channel\.send|ch\.send|\.edit)\([^\n]*", src)
        self.assertTrue(calls)
        for c in calls:
            self.assertIn("allowed_mentions", src[src.index(c):src.index(c) + 400], c)

    def test_token_only_from_environment(self):
        old = os.environ.pop("DISCORD_TOKEN", None)
        try:
            with self.assertRaises(SystemExit) as c:
                bot.main()
            self.assertIn("DISCORD_TOKEN", str(c.exception))
        finally:
            if old is not None:
                os.environ["DISCORD_TOKEN"] = old
        for fn in ["bot/" + m for m in BOT_MODULES] + ["bot/ui.py", "checker/check_any.py", "checker/core.py"]:
            with open(os.path.join(ROOT, fn), encoding="utf-8") as f:
                src = f.read()
            self.assertIsNone(re.search(r"[MNO][A-Za-z\d_-]{23,25}\.[\w-]{6}\.[\w-]{27,}", src), "token-like string in code")
        self.assertEqual(bot_source().count("os.environ.get(\"DISCORD_TOKEN\")"), 1)
        with open(os.path.join(BOT, "discord_bot.py"), encoding="utf-8") as f:
            self.assertEqual(f.read().count("os.environ.get(\"DISCORD_TOKEN\")"), 1)          # in main()

    def test_no_logging_of_the_token(self):
        src = bot_source()
        for line in re.findall(r"log\.\w+\([^\n]*", src):
            self.assertNotIn("token", line.lower())


class TestPrivate(Base):
    """visibility: public by default; private = card by direct message plus a small note in the channel; a check run in a
    direct message with the bot; /settings; the queue as seen by others, staff and admins; the audit copy."""
    SECRET = "secret_name_w10.txt"

    def pstate(self, runner=None, audit=True, **kw):
        st = self.state(runner=runner, **kw)
        self.audit_world_channel = None
        if audit:
            st.cfg.audit_channel_id = 555

            async def audit_channel():
                return FakeChannel(self.world, "audit")
            st.audit_channel = audit_channel
        return st

    def setUp(self):
        super().setUp()
        self.world = World()

    def sec(self, n=10):
        return self.att(n, self.SECRET)

    def gate_runner(self, gate):
        return lambda p, job=None: gate.wait(10) and good_result(n=job.n)

    # ---- settings
    def test_default_public_and_settings_file(self):
        import settings
        st = self.state()
        self.assertEqual(st.visibility_of(7), "public")                 # a new user: public
        self.assertEqual(settings.DEFAULT, "public")
        self.assertFalse(os.path.exists(st.cfg.settings_path))
        self.assertEqual(os.path.dirname(st.cfg.settings_path), os.path.dirname(self.rec))     # next to the records table
        i = FakeInteraction(uid=7)
        self.run_async(bot.handle_settings(st, i))
        self.assertIn("Default visibility of my checks: **public**", i.private)
        sel = [x for x in ui.walk(i.private_view) if isinstance(x, discord.ui.DynamicItem)][0].item
        self.assertEqual([(o.value, o.default) for o in sel.options], [("private", False), ("public", True)])
        i = FakeInteraction(uid=7)
        self.run_async(bot.handle_settings_set(st, "private", i))
        self.assertIn("Saved", i.private)
        self.assertIn("private by default", i.private)
        self.assertEqual(st.visibility_of(7), "private")
        self.assertEqual(st.visibility_of(8), "public")
        with open(st.cfg.settings_path, encoding="utf-8") as f:
            data = __import__("json").load(f)
        self.assertEqual(data, {"format": "superperm-settings/1", "users": {"7": {"visibility": "private"}}})   # one field only
        self.assertFalse(os.path.exists(st.cfg.settings_path + ".tmp"))                    # written atomically
        i = FakeInteraction(uid=7)
        self.run_async(bot.handle_settings_set(st, "everybody", i))                        # nonsense is refused
        self.assertIn("Not saved", i.private)
        self.assertEqual(st.visibility_of(7), "private")
        self.assertEqual(bot.Config(settings_path="/x/s.json").settings_path, "/x/s.json")
        self.assertEqual(bot.Config(records_path="/data/records.json").settings_path.replace("\\", "/").split("/")[-1], "settings.json")

    def test_visibility_option_overrides_the_saved_default(self):
        import settings
        st = self.state()
        settings.set_visibility(st.cfg.settings_path, 7, "private")
        i = FakeInteraction(uid=7, world=World())
        self.run_async(bot.handle_check(st, i, attachment=self.att(10)))                    # no option: the saved default
        self.assertIsNotNone(i.dm_card)
        i = FakeInteraction(uid=7, world=World())
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), visibility="public"))
        self.assertIsNone(i.dm_card)
        self.assertIn("Valid", i.card.text)
        i = FakeInteraction(uid=8, world=World())                                           # another user: public default
        self.run_async(bot.handle_check(st, i, attachment=self.att(10)))
        self.assertIsNone(i.dm_card)
        i = FakeInteraction(uid=8, world=World())
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), visibility="private"))
        self.assertIsNotNone(i.dm_card)
        # the form is preset to the saved default
        i = FakeInteraction(uid=7, world=World())
        self.run_async(bot.handle_check(st, i))
        opts = i.world.modals[0].vis.component.options
        self.assertEqual([(o.value, o.default) for o in opts], [("public", False), ("private", True)])
        i = FakeInteraction(uid=9, world=World())
        self.run_async(bot.handle_check(st, i, visibility="private"))                       # the option presets it too
        self.assertTrue([o for o in i.world.modals[0].vis.component.options if o.value == "private"][0].default)

    def test_form_choice(self):
        st = self.state()
        m = bot.CheckModal(st)
        m.file.component._values = [FakeAttachment("form.txt.xz", W10)]
        m.vis.component._values = ["private"]
        i = FakeInteraction(uid=4, name="dora")
        self.run_async(m.on_submit(i))
        self.assertIn("Valid", i.dm_card.text)
        m = bot.CheckModal(st)
        m.file.component._values = [FakeAttachment("form.txt.xz", W10)]
        i = FakeInteraction(uid=5, name="eve", world=World())                               # nothing chosen: the default
        self.run_async(m.on_submit(i))
        self.assertIsNone(i.dm_card)
        self.assertIn("Valid", i.card.text)

    # ---- the private flow
    def test_private_flow_dm_card_and_note_in_the_channel(self):
        st = self.state(runner=TestProgress.slow_runner(None, 0.6), progress_s=0.1)
        i = FakeInteraction(uid=7, name="login", display_name="Dora D")
        self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="private"))
        dm = i.dm_card
        self.assertIsNotNone(dm)
        self.assertIn("Valid superpermutation", dm.text)
        self.assertIn(self.SECRET, "\n".join(dm.texts))
        self.assertIn("\U0001f512 private", dm.texts[0])
        self.assertGreaterEqual(len(dm.history), 2)                    # edited in place, like a public card
        self.assertEqual(len(i.of("dm")), 1)
        # the channel got ONE small note, edited at the state changes only
        notes = i.of("channel")
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note.texts[0], "\U0001f512 Private check by <@7> · n = 10 · queued")
        self.assertEqual(note.texts[-1], "\U0001f512 Private check by <@7> · n = 10 · finished")
        self.assertLessEqual(len(note.history), 3)                      # queued, running, finished: no progress edits
        everything = "\n".join(note.texts)
        for never in (self.SECRET, "secret", "4,034,855", "ab" * 32, "Valid", "valid", "letters", "▰", "Alphabet"):
            self.assertNotIn(never, everything)
        self.assertEqual(ui.buttons(note.view), [])
        self.assertEqual(ui.accent(note.view), ui.COLOURS["queued"])
        # the user got the usual ephemeral answer, saying where the card is, with a link to the direct message
        priv = i.of("followup")[0]
        self.assertTrue(priv.ephemeral)
        self.assertIn("in your direct messages", priv.text)
        self.assertEqual([b.url for b in ui.buttons(priv.view)], [dm.jump_url])
        self.assertEqual(st.sched.jobs, {})

    def test_private_job_cancel_note_says_finished_without_verdict(self):
        gate = threading.Event()
        st = self.state(runner=lambda p, job=None: self._cancellable(job, gate))
        i = FakeInteraction(uid=7, name="dora")

        async def go():
            t = asyncio.ensure_future(bot.handle_check(st, i, attachment=self.sec(), visibility="private"))
            await asyncio.sleep(0.4)
            c = FakeInteraction(uid=7, name="dora")
            await bot.handle_job_button(st, "cancel", 1, c)           # the Cancel button on the DM card
            await t
        self.run_async(go())
        self.assertIn("Cancelled by dora", i.dm_card.text)
        note = i.of("channel")[0]
        self.assertTrue(note.texts[-1].endswith("finished"))
        self.assertNotIn("ancel", "\n".join(note.texts))

    def _cancellable(self, job, gate):
        while not gate.is_set():
            if job.cancel_event.is_set():
                return {"ok": False, "valid": False, "cancelled": True, "error": "cancelled by %s" % job.cancelled_by}
            time.sleep(0.02)
        return good_result(n=job.n)

    def test_closed_dms_refuse_and_queue_nothing(self):
        st = self.state()
        i = FakeInteraction(uid=7)
        i.world.dm_error = forbidden()
        with self.assertLogs("superperm", "INFO") as logs:
            self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="private"))
        t = i.private
        self.assertIn("Could not start", t)
        self.assertIn("I cannot send you a direct message.", t)
        self.assertIn("Allow direct messages from server members (Server menu > Privacy settings), or run the check as public.", t)
        self.assertEqual(i.world.log[-1].where, "followup")
        self.assertEqual([m for m in i.world.log if m.where != "followup"], [])      # nothing posted anywhere
        self.assertEqual((self.calls, st.sched.jobs, st.cards), ([], {}, {}))        # the job was not queued
        self.assertTrue(any("direct message" in x for x in logs.output))
        self.assertNotIn(self.SECRET, "\n".join(logs.output))                        # file names of private jobs: debug only
        # HTTPException too
        i = FakeInteraction(uid=7, world=World())
        i.world.dm_error = discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom")
        self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="private"))
        self.assertIn("I cannot send you a direct message.", i.private)
        # the user can still run it as public afterwards
        i = FakeInteraction(uid=7, world=World())
        self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="public"))
        self.assertIn("Valid", i.card.text)

    def test_file_name_only_at_debug_level(self):
        st = self.state()
        i = FakeInteraction(uid=7)
        with self.assertLogs("superperm", "DEBUG") as logs:
            self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="private"))
        named = [r for r in logs.records if self.SECRET in r.getMessage()]
        self.assertTrue(named)
        self.assertTrue(all(r.levelno == logging.DEBUG for r in named))

    def test_private_per_user_limit_still_applies_before_the_dm(self):
        gate = threading.Event()
        st = self.state(runner=self.gate_runner(gate))

        async def go():
            a = FakeInteraction(uid=1, world=World())
            t = asyncio.ensure_future(bot.handle_check(st, a, attachment=self.att(10), visibility="private"))
            await asyncio.sleep(0.3)
            b = FakeInteraction(uid=1, world=World())
            await bot.handle_check(st, b, attachment=self.att(10), visibility="private")
            gate.set()
            await t
            return b
        b = self.run_async(go())
        self.assertIn("already have a check", b.private)
        self.assertIsNone(b.dm_card)                                  # no DM was sent for the refused job

    # ---- the queue
    def queue_scenario(self, viewer):
        gate = threading.Event()
        st = self.state(runner=self.gate_runner(gate))
        seen = {}

        async def go():
            ia = FakeInteraction(uid=1, name="ann", display_name="Ann A", world=World())
            ib = FakeInteraction(uid=2, name="bob", display_name="Bob B", world=World())
            ic = FakeInteraction(uid=3, name="cy", display_name="Cy C", world=World())
            ta = asyncio.ensure_future(bot.handle_check(st, ia, attachment=self.att(10, self.SECRET), visibility="private"))
            await asyncio.sleep(0.3)
            tb = asyncio.ensure_future(bot.handle_check(st, ib, attachment=self.att(11, "bobs_private.txt"), visibility="private"))
            await asyncio.sleep(0.3)
            tc = asyncio.ensure_future(bot.handle_check(st, ic, attachment=self.att(12, "cys_public.txt"), visibility="public"))
            await asyncio.sleep(0.3)
            for key, v in viewer.items():
                q = v
                await bot.handle_queue(st, q)
                seen[key] = q.of("response")[0]
            seen["dm"] = ia.dm_card.jump_url
            return st, gate, (ta, tb, tc), (ia, ib, ic)
        return go, seen, gate

    def test_queue_other_user_sees_a_masked_line(self):
        viewers = {"other": FakeInteraction(uid=9, name="zed", display_name="Zed Z", world=World())}
        go, seen, gate = self.queue_scenario(viewers)

        async def run():
            st, gate_, tasks, _ = await go()
            gate_.set()
            await asyncio.gather(*tasks)
        self.run_async(run())
        m = seen["other"]
        t = m.text
        self.assertIn("\U0001f512 private check · n = 10", t)                 # the running one
        self.assertIn("1. \U0001f512 private check · n = 11", t)               # the waiting one
        self.assertIn("<@3>", t)                                                  # the public one is shown as usual
        for hidden in ("<@1>", "<@2>", "Ann", "Bob", self.SECRET, "secret", "bobs_private", "letters"):
            self.assertNotIn(hidden, t)
        self.assertEqual(ui.buttons(m.view), [])                                  # no link, no Cancel
        self.assertTrue(m.ephemeral)

    def test_queue_submitter_staff_and_admin_views(self):
        viewers = {"sub": FakeInteraction(uid=1, name="ann", world=World()),
                   "staff": FakeInteraction(uid=20, name="mod", perms={"manage_messages": True}, world=World()),
                   "admin": FakeInteraction(uid=21, name="root", perms={"administrator": True}, world=World())}
        go, seen, gate = self.queue_scenario(viewers)
        out = {}

        async def run():
            st, gate_, tasks, ix = await go()
            # staff cancel the waiting private job #2 from the queue
            staff = FakeInteraction(uid=20, name="mod", display_name="Mod M", perms={"manage_messages": True}, world=World())
            other = FakeInteraction(uid=9, name="zed", world=World())
            await bot.handle_job_button(st, "cancel", 2, other)
            out["other"] = other.private
            await bot.handle_job_button(st, "cancel", 2, staff)
            out["staff"] = staff.private
            out["ib"] = ix[1]
            gate_.set()
            await asyncio.gather(*tasks)
            # staff cannot read the result of a private check
            d = FakeInteraction(uid=20, name="mod", perms={"manage_messages": True}, world=World())
            await bot.handle_job_button(st, "details", 1, d)
            out["details"] = d.private
        self.run_async(run())
        dm = seen["dm"]
        # the submitter: everything, with the link to the direct-message card
        t = seen["sub"].text
        self.assertIn("#1 \U0001f512 · n = 10 · <@1>", t)
        self.assertIn(self.SECRET, t)
        self.assertEqual([b.url for b in ui.buttons(seen["sub"].view) if b.url], [dm])
        self.assertIn("private check · n = 11", t)                            # another person's private job: masked
        self.assertNotIn("bobs_private", t)
        # staff: the user, n, size and progress; Cancel buttons; not the file name; no link
        t = seen["staff"].text
        self.assertIn("<@1>", t)
        self.assertIn("<@2>", t)
        self.assertIn("n = 10", t)
        self.assertIn("letters", t)
        self.assertIn("%", t)
        self.assertNotIn(self.SECRET, t)
        self.assertNotIn("bobs_private", t)
        self.assertEqual([b.label for b in ui.buttons(seen["staff"].view)], ["Cancel #1", "Cancel #2"])
        self.assertEqual([b.url for b in ui.buttons(seen["staff"].view) if b.url], [])
        # admin: also the file name
        t = seen["admin"].text
        self.assertIn(self.SECRET, t)
        self.assertIn("bobs_private.txt", t)
        self.assertEqual([b.label for b in ui.buttons(seen["admin"].view)], ["Cancel #1", "Cancel #2"])
        self.assertTrue(ui.check_limits(seen["admin"].view))
        # Cancel from the queue: the permission check applies
        self.assertIn("Not yours", out["other"])
        self.assertIn("being cancelled", out["staff"])
        self.assertIn("Cancelled by Mod M", out["ib"].dm_card.text)
        self.assertIn("Only the submitter", out["details"])

    def test_full_queue_with_staff_buttons_fits_discord_limits(self):
        run = pc_job(1, 10, private=True)
        waiting = [pc_job(10 + k, 11, private=True, uid=200 + k) for k in range(6)]
        sched = SimpleNamespace(lanes={"fast": SimpleNamespace(running=run, waiting=waiting),
                                       "slow": SimpleNamespace(running=pc_job(2, 13, private=True, uid=99),
                                                               waiting=[pc_job(30 + k, 14, private=True, uid=300 + k) for k in range(6)])})
        for kw in ({}, {"staff": True}, {"admin": True}, {"viewer_id": 1}):
            v = ui.queue_card(sched, now=time.time(), **kw)
            self.assertTrue(ui.check_limits(v), kw)

    # ---- audit
    def test_audit_copy_and_details_only_for_admins(self):
        st = self.pstate()
        i = FakeInteraction(uid=7, name="dora", world=self.world)
        self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="private"))
        copies = i.of("audit")
        self.assertEqual(len(copies), 1)
        a = copies[0]
        self.assertIn(self.SECRET, a.texts[0])                       # the audit copy shows the card in full
        self.assertIn("<@7>", a.texts[0])
        self.assertIn("Valid superpermutation", a.text)              # edited with the final state
        self.assertLessEqual(len(a.history), 3)                      # start and result are enough
        self.assertEqual([b.label for b in ui.buttons(a.view)], ["Details"])
        self.assertEqual(ui.buttons(a.view)[0].custom_id, "sp:audit:1")
        n = FakeInteraction(uid=30, name="mod", perms={"manage_messages": True}, world=World())
        self.run_async(bot.handle_job_button(st, "audit", 1, n))
        self.assertIn("Administrators only", n.private)
        self.assertNotIn("ab" * 32, n.private)
        x = FakeInteraction(uid=31, name="root", perms={"administrator": True}, world=World())
        self.run_async(bot.handle_job_button(st, "audit", 1, x))
        self.assertIn("ab" * 32, x.private)
        # a public check has no audit copy; without SUPERPERM_AUDIT_CHANNEL a private one has none either
        j = FakeInteraction(uid=8, world=World())
        self.run_async(bot.handle_check(st, j, attachment=self.att(10), visibility="public"))
        self.assertEqual(j.of("audit"), [])
        st2 = self.pstate(audit=False)
        k = FakeInteraction(uid=9, world=World())
        self.run_async(bot.handle_check(st2, k, attachment=self.att(10), visibility="private"))
        self.assertEqual(k.of("audit"), [])

    def test_audit_failure_does_not_stop_the_check(self):
        st = self.pstate()

        async def broken():
            raise forbidden()
        st.audit_channel = broken
        i = FakeInteraction(uid=7, world=self.world)
        with self.assertLogs("superperm", "WARNING") as logs:
            self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="private"))
        self.assertTrue(any("audit" in x for x in logs.output))
        self.assertIn("Valid", i.dm_card.text)

    # ---- add to records from a private check
    def test_publish_needs_a_second_yes(self):
        st = self.pstate(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32),
                         contributor_role=777, channel_id=900)

        async def go():
            i = FakeInteraction(uid=1, name="ann", display_name="Ann A", world=self.world)
            i.guild = FakeGuild([FakeRole(777)])
            await bot.handle_check(st, i, attachment=self.sec(9), visibility="private")
            self.assertEqual([b.label for b in ui.buttons(i.dm_card.view)], ["Add to records", "Details"])
            click = FakeInteraction(uid=1, name="ann", world=World())
            await bot.handle_job_button(st, "add", 1, click)                          # asks first
            t = click.private
            self.assertIn("This makes the result public: entry in the table and an announcement in <#900>.", t)
            self.assertEqual([b.label for b in ui.buttons(click.private_view)], ["Publish", "Keep private"])
            self.assertIsNone(records.best_verified(records.load(self.rec), 9))
            self.assertEqual(self.announced, [])
            # someone else cannot publish it
            thief = FakeInteraction(uid=2, name="bob", world=World())
            await bot.handle_job_button(st, "publish", 1, thief)
            self.assertIn("Only the submitter", thief.private)
            # Keep private: nothing happens
            keep = FakeInteraction(uid=1, name="ann", world=World())
            await bot.handle_job_button(st, "keep", 1, keep)
            self.assertIn("Nothing was published", keep.private)
            self.assertIsNone(records.best_verified(records.load(self.rec), 9))
            self.assertEqual(self.announced, [])
            self.assertEqual([b.label for b in ui.buttons(i.dm_card.view)], ["Add to records", "Details"])
            # Publish: the normal public action
            pub = FakeInteraction(uid=1, name="ann", world=World())
            await bot.handle_job_button(st, "publish", 1, pub)
            return i, pub
        i, pub = self.run_async(go())
        e = records.best_verified(records.load(self.rec), 9)
        self.assertEqual((e["submitter"], e["submitter_id"]), ("Ann A", "1"))
        self.assertEqual(len(self.announced), 1)
        self.assertEqual(ui.buttons(self.announced[0]), [])                             # no link to a private card
        self.assertIn("New record for n = 9", "\n".join(ui.texts(self.announced[0])))
        self.assertEqual(len(i.user.added), 1)                                         # the role goes to the member
        self.assertIn("Added ✓", [b.label for b in ui.buttons(i.dm_card.view)])
        self.assertIn("new record", i.dm_card.text)
        self.assertIn("new record", pub.private)

    def test_submit_flag_on_a_private_check_is_an_explicit_yes(self):
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32))
        i = FakeInteraction(uid=1, name="ann")
        self.run_async(bot.handle_check(st, i, attachment=self.att(10), visibility="private", submit=True))
        self.assertIsNotNone(records.best_verified(records.load(self.rec), 9))
        self.assertEqual(ui.buttons(self.announced[0]), [])

    # ---- a check run in a direct message with the bot
    def test_check_in_a_direct_message(self):
        st = self.pstate()
        i = FakeInteraction(uid=7, name="dora", guild=False, world=self.world)
        self.run_async(bot.handle_check(st, i, attachment=self.sec(), visibility="public"))      # in a DM there is no "public"
        cards = i.of("channel")                                                            # interaction.channel IS the DM
        self.assertEqual(len(cards), 1)
        self.assertIn("Valid superpermutation", cards[0].text)
        self.assertEqual(i.of("dm"), [])                                                   # no extra DM, no note anywhere
        self.assertEqual(len(i.of("audit")), 1)                                            # only the audit copy for admins
        self.assertIn(self.SECRET, i.of("audit")[0].texts[0])
        self.assertIn("direct messages", i.of("followup")[0].text)
        # limits and lanes apply as usual: one check per user and lane
        gate = threading.Event()
        st = self.pstate(runner=self.gate_runner(gate))

        async def go():
            a = FakeInteraction(uid=7, guild=False, world=World())
            t = asyncio.ensure_future(bot.handle_check(st, a, attachment=self.att(10)))
            await asyncio.sleep(0.3)
            b = FakeInteraction(uid=7, guild=False, world=World())
            await bot.handle_check(st, b, attachment=self.att(11))
            q = FakeInteraction(uid=9, guild=False, world=World())
            await bot.handle_queue(st, q)                                                 # /queue works in a DM
            gate.set()
            await t
            return b, q
        b, q = self.run_async(go())
        self.assertIn("already have a check", b.private)
        self.assertIn("private check · n = 10", q.private)                           # others see it masked
        for cmd in (bot.handle_about, bot.handle_records, bot.handle_settings):
            d = FakeInteraction(uid=7, guild=False, world=World())
            self.run_async(cmd(self.state(), d))
            self.assertEqual(len(d.of("response")), 1)
        d = FakeInteraction(uid=7, guild=False, world=World())
        self.run_async(bot.handle_panel(self.state(), d))                                 # /panel: server only
        self.assertIn("Manage Server", d.private)
        self.assertEqual(d.of("channel"), [])

    def test_role_for_a_dm_check_through_fetch_member(self):
        runner = lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32)
        member = FakeMember(7, "dora")
        member.guild = FakeGuild([FakeRole(777)])
        st = self.state(runner=runner, contributor_role=777, channel_id=900)

        async def found(uid):
            self.assertEqual(uid, 7)
            return member
        st.find_member = found

        async def go(state):
            i = FakeInteraction(uid=7, name="dora", guild=False, world=World())
            await bot.handle_check(state, i, attachment=self.att(10))
            self.assertEqual([b.label for b in ui.buttons(i.card.view)], ["Add to records", "Details"])
            c = FakeInteraction(uid=7, name="dora", guild=False, world=World())
            await bot.handle_job_button(state, "add", 1, c)
            self.assertIn("This makes the result public", c.private)                       # confirmation step in a DM too
            p = FakeInteraction(uid=7, name="dora", guild=False, world=World())
            await bot.handle_job_button(state, "publish", 1, p)
            return i
        i = self.run_async(go(st))
        self.assertEqual(len(member.added), 1)                                           # found: role given
        self.assertIn("Contributor role given", i.card.text)
        self.assertEqual(len(self.announced), 1)
        # not found (NotFound) and no way to look: the card says so
        for finder in ("notfound", None):
            self.setUp()
            self.world = World()
            st = self.state(runner=runner, contributor_role=777, channel_id=900)
            if finder:
                async def nf(uid):
                    raise not_found()
                st.find_member = nf
            with self.assertLogs("superperm", "WARNING") if finder else _null():
                i = self.run_async(go(st))
            self.assertIn("Contributor role not given: use the server", i.card.text)
            self.assertIsNotNone(records.best_verified(records.load(self.rec), 9))        # the table entry is made anyway

    def test_cards_and_texts(self):
        n = FakeInteraction(uid=1, world=World())
        self.run_async(bot.handle_about(self.state(), n))
        t = n.of("response")[0].text
        self.assertIn("**Privacy**", t)
        self.assertIn("Checks are public by default", t)
        self.assertIn("run `/check` in a direct message with me", t)
        self.assertIn("Moderators (Manage Messages)", t)
        self.assertIn("audit channel", t)
        c = bot.Config()
        self.assertIsNone(c.audit_channel_id)
        old = os.environ.get("SUPERPERM_AUDIT_CHANNEL")
        os.environ["SUPERPERM_AUDIT_CHANNEL"] = "123"
        try:
            self.assertEqual(bot.Config().audit_channel_id, 123)
        finally:
            if old is None:
                os.environ.pop("SUPERPERM_AUDIT_CHANNEL")
            else:
                os.environ["SUPERPERM_AUDIT_CHANNEL"] = old


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def pc_job(jid, n, private=False, uid=None):
    plan = bot.Plan("word", n, 1000, 1, 1000, 1.0, 1.0)
    j = bot.Job(jid, uid or jid, "user%d" % jid, "secret%d.txt" % jid, "x", plan)
    j.private = private
    return j


class TestRefreshInterval(unittest.TestCase):
    """the card refresh follows the time left: every few seconds for short checks, about once a minute for long ones."""

    def job(self, status="running", est_total=60.0, length=1000, progress=(1, 1, 0), started=100.0):
        plan = SimpleNamespace(length=length, est_len=length, est_total_s=est_total)
        return SimpleNamespace(status=status, t_start=started, progress=progress, plan=plan)

    def test_pace(self):
        cfg = bot.Config(records_path="r.json")
        f = bot.refresh_interval
        self.assertEqual(f(self.job(status="queued", est_total=9000), cfg), 15.0)
        self.assertEqual(f(self.job(est_total=40), cfg, now=101), 5.0)              # nothing read yet: from the plan
        self.assertEqual(f(self.job(est_total=40, progress=(1, 1, 500)), cfg, now=120), 5.0)   # 20 s left
        # 30 s elapsed for 10 %: 270 s left -> 13.5 s
        self.assertAlmostEqual(f(self.job(progress=(1, 1, 100)), cfg, now=130), 13.5)
        # 15 min left -> 45 s; hours left -> the 60 s ceiling
        self.assertAlmostEqual(f(self.job(progress=(1, 1, 250)), cfg, now=400), 45.0)
        self.assertEqual(f(self.job(progress=(1, 3, 10)), cfg, now=700), 60.0)
        # three passes, the second half done after 150 s: 1.5 of 3 passes done, so 150 s left -> 7.5 s
        self.assertAlmostEqual(f(self.job(progress=(2, 3, 500), length=1000), cfg, now=250), 7.5)

    def test_bounds_follow_config(self):
        cfg = bot.Config(records_path="r.json", progress_s=0.2)                     # tests use tiny intervals
        self.assertEqual(bot.refresh_interval(self.job(est_total=9000), cfg, now=101), 0.2)
        self.assertEqual(bot.refresh_interval(self.job(status="queued"), cfg), 0.2)

    def test_running_card_shows_live_timestamp(self):
        job = SimpleNamespace(progress=(1, 1, 100), plan=SimpleNamespace(length=1000, est_len=1000), t_start=100.0)
        lines = ui.running_lines(job, now=130.0)
        self.assertIn("<t:130:R>", lines[-1])


class BoardChannel:
    """the records channel as board.py sees it: messages in order, history(limit=1), fetch_message, send; messages can
    be edited and deleted. Every send and edit must allow no mention at all."""

    def __init__(self):
        self.msgs, self.next_id, self.sends, self.edits, self.deletes = [], 100, 0, 0, 0

    def post(self, text):                          # a message by somebody else (or an announcement)
        return self._add(text)

    def _add(self, view):
        ch = self

        class Msg:
            def __init__(m):
                m.id, m.view = ch.next_id, view

            async def edit(m, *, view=None, allowed_mentions=None, **kw):
                check_sent(None, view, allowed_mentions)
                m.view = view
                ch.edits += 1

            async def delete(m):
                ch.msgs.remove(m)
                ch.deletes += 1
        self.next_id += 1
        m = Msg()
        self.msgs.append(m)
        return m

    async def send(self, content=None, *, view=None, allowed_mentions=None, **kw):
        check_sent(content, view, allowed_mentions)
        self.sends += 1
        return self._add(view)

    async def fetch_message(self, mid):
        for m in self.msgs:
            if m.id == mid:
                return m
        raise LookupError("unknown message %s" % mid)

    def history(self, limit=1):
        async def gen():
            for m in list(reversed(self.msgs))[:limit]:
                yield m
        return gen()


class TestBoard(Base):
    def test_parse_n_roles(self):
        r = board.parse_n_roles("1-6:11, 7:22,15-99:33")
        self.assertEqual(r, [(1, 6, 11), (7, 7, 22), (15, 99, 33)])
        self.assertEqual([board.role_for_n(r, n) for n in (5, 7, 8, 16)], [11, 22, None, 33])
        self.assertEqual(board.parse_n_roles(""), [])
        for bad in ("7", "a:1", "9-7:1", "7:x"):
            with self.assertRaises(ValueError):
                board.parse_n_roles(bad)

    def test_new_record_pings_the_role_of_its_n(self):
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32),
                        n_roles="1-8:11,9:22")
        i = FakeInteraction(uid=7, name="alice")
        self.run_async(bot.handle_check(st, i, attachment=self.att(9), submit=True))
        self.assertEqual(self.pinged, [22])
        self.assertIn("<@&22>", ui.texts(self.announced[0])[0])
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=407000, sha256="ce" * 32))
        self.run_async(bot.handle_check(st, FakeInteraction(uid=7, name="alice"), attachment=self.att(9), submit=True))
        self.assertEqual(self.pinged, [22, None])                       # no SUPERPERM_N_ROLES: no ping
        self.assertNotIn("<@&", "\n".join(ui.texts(self.announced[1])))

    def test_send_announcement_allows_only_that_role(self):
        seen = []

        class Ch:
            async def send(self, content=None, *, view=None, allowed_mentions=None, **kw):
                seen.append(allowed_mentions)
        self.run_async(bot.send_announcement(Ch(), None, 22))
        self.run_async(bot.send_announcement(Ch(), None, None))
        a, b = seen
        self.assertFalse(a.everyone or a.users or a.replied_user)
        self.assertEqual([r.id for r in a.roles], [22])
        self.assertFalse(b.everyone or b.users or b.roles or b.replied_user)

    def test_board_stays_the_last_message(self):
        ch = BoardChannel()
        st = self.state(board=True, board_path=os.path.join(self.tmp, "board.json"))

        async def channel():
            return ch
        st.board_channel = channel
        ch.post("old talk")
        self.run_async(board.refresh(st))                     # first board: sent, id saved
        self.assertEqual((ch.sends, ch.edits, ch.deletes), (1, 0, 0))
        first = ch.msgs[-1].id
        self.assertEqual(board.load_id(st.cfg.board_path), first)
        self.assertIn("Shortest known superpermutations", "\n".join(ui.texts(ch.msgs[-1].view)))
        self.run_async(board.refresh(st))                     # still the last message: edited in place
        self.assertEqual((ch.sends, ch.edits, ch.deletes), (1, 1, 0))
        ch.post("announcement")                               # something came after it: old board deleted, new one sent
        self.run_async(board.refresh(st))
        self.assertEqual((ch.sends, ch.edits, ch.deletes), (2, 1, 1))
        self.assertNotIn(first, [m.id for m in ch.msgs])
        self.assertEqual(ch.msgs[-1].id, board.load_id(st.cfg.board_path))
        ch.msgs.remove(ch.msgs[-1])                           # somebody deleted the board: a new one is sent
        self.run_async(board.refresh(st))
        self.assertEqual((ch.sends, ch.deletes), (3, 1))

    def test_board_follows_a_new_record_and_is_off_by_default(self):
        ch = BoardChannel()

        async def channel():
            return ch
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32))
        st.board_channel = channel
        self.run_async(bot.handle_check(st, FakeInteraction(uid=7, name="alice"), attachment=self.att(9), submit=True))
        self.assertEqual(ch.sends, 0)                         # SUPERPERM_BOARD not set: nothing posted
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=407000, sha256="ce" * 32), board=True,
                        board_path=os.path.join(self.tmp, "b.json"))
        st.board_channel = channel
        self.run_async(bot.handle_check(st, FakeInteraction(uid=7, name="alice"), attachment=self.att(9), submit=True))
        self.assertEqual(ch.sends, 1)
        self.assertIn("407,000", "\n".join(ui.texts(ch.msgs[-1].view)))


class TestOnBehalf(Base):
    """v1.2: a verified claim keeps its holder; administrators can check a word for somebody (holder) and later make a
    member the holder of an entry (Set holder)."""

    def test_claim_verified_keeps_its_holder(self):
        st = self.state(runner=lambda p, job=None: good_result(n=7, length=5905, sha256="5a" * 32), contributor_role=777)
        i = FakeInteraction(uid=7, name="jakub")
        i.guild = FakeGuild([FakeRole(777)])
        self.run_async(bot.handle_check(st, i, attachment=self.att(7), submit=True))
        e = records.best_verified(records.load(self.rec), 7)
        self.assertEqual((e["length"], e["submitter"], e.get("submitter_id"), e["checked_by_id"]), (5905, "Teodorescu", None, "7"))
        self.assertEqual(i.user.added, [])                                   # no Contributor role for checking a claim
        self.assertIn("Entered for Teodorescu", i.card.text)
        a = "\n".join(ui.texts(self.announced[0]))
        self.assertIn("Record for n = 7 verified", a)
        self.assertIn("by Teodorescu", a)
        self.assertIn("checked by <@7>", a)
        self.assertNotIn("New record", a)

    def test_holder_option_is_for_administrators(self):
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32))
        i = FakeInteraction(uid=8, name="bob")
        self.run_async(bot.handle_check(st, i, attachment=self.att(9), holder="Someone Else"))
        self.assertIn("only administrators", i.private)
        self.assertIsNone(records.best_verified(records.load(self.rec), 9))
        i = FakeInteraction(uid=9, name="admin", perms={"administrator": True})
        self.run_async(bot.handle_check(st, i, attachment=self.att(9), holder="  Andrei   Teodorescu "))
        e = records.best_verified(records.load(self.rec), 9)              # submit implied by holder
        self.assertEqual((e["submitter"], e.get("submitter_id"), e["checked_by"], e["checked_by_id"]),
                         ("Andrei Teodorescu", None, "admin", "9"))
        a = "\n".join(ui.texts(self.announced[-1]))
        self.assertIn("Record for n = 9 verified", a)
        self.assertIn("by Andrei Teodorescu", a)
        self.assertIn("checked by <@9>", a)

    def test_set_holder_by_an_administrator(self):
        st = self.state(runner=lambda p, job=None: good_result(n=9, length=408000, sha256="cd" * 32), contributor_role=777)
        admin = FakeInteraction(uid=9, name="admin", perms={"administrator": True})
        self.run_async(bot.handle_check(st, admin, attachment=self.att(9), holder="Andrei Teodorescu"))
        t = records.load(self.rec)
        e = records.best_verified(t, 9)
        arg = ui.entry_ref_arg(9, e)
        card = ui.record_manage_card(t, "9", e, (9, True, True))
        self.assertIn("Set holder: choose the member", ui.plain(card))                  # the member select (admins)
        self.assertNotIn("Set holder", ui.plain(ui.record_manage_card(t, "9", e, (8, True, False))))
        andrei = FakeMember(55, "andrei_t", display_name="Andrei T.")
        other = FakeInteraction(uid=8, name="bob")
        self.run_async(bot.handle_x(st, "rh", arg, andrei, other))
        self.assertIn("Administrators only", other.private)
        g = FakeInteraction(uid=9, name="admin", perms={"administrator": True})
        g.guild = FakeGuild([FakeRole(777)])
        self.run_async(bot.handle_x(st, "rh", arg, andrei, g))
        e = records.best_verified(records.load(self.rec), 9)
        self.assertEqual((e["submitter"], e["submitter_id"], e["checked_by_id"]), ("Andrei T.", "55", "9"))
        self.assertEqual([r.id for r, _ in andrei.added], [777])           # the record holder gets the role
        self.assertIn("Holder set", g.private)
        d = "\n".join(ui.texts(ui.record_details_card(records.load(self.rec), "9")))
        self.assertIn("checked by <@9>", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)

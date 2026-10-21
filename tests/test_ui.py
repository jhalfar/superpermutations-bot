"""Offline tests of the Discord cards (ui.py): every card is built without a gateway and checked for its text, accent
colour, buttons, and the Discord limits (40 components, 4000 characters of text per message, label lengths).

    python -I tests/test_ui.py

tools/preview_cards.py builds the same samples and prints them as plain text.
"""
import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOT = os.path.join(ROOT, "bot")
CHECKER = os.path.join(ROOT, "checker")
sys.path.insert(0, CHECKER)
sys.path.insert(0, BOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))   # preview_cards: the sample cards
sys.path.insert(0, HERE)

import discord  # noqa: E402
from discord.components import _component_factory  # noqa: E402
from discord.ui.view import ViewStore  # noqa: E402

import discord_bot as bot  # noqa: E402
import preview_cards as pc  # noqa: E402
import records  # noqa: E402
import ui  # noqa: E402

C = ui.COLOURS
ALL = pc.build_all()
BY = {t: v for t, k, v in ALL}


def card(prefix):
    if prefix in BY:
        return BY[prefix]
    hits =[v for t, v in BY.items() if t.startswith(prefix)]
    assert len(hits) == 1, (prefix, [t for t in BY if t.startswith(prefix)])
    return hits[0]


def text(view):
    return "\n".join(ui.texts(view))


def labels(view):
    return [b.label for b in ui.buttons(view)]


class TestEveryCard(unittest.TestCase):
    def test_limits_on_every_card(self):
        self.assertGreaterEqual(len(ALL), 30)
        for title, kind, view in ALL:
            with self.subTest(title):
                self.assertTrue(ui.check_limits(view))
                self.assertLessEqual(ui.component_count(view), 40)
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(ui.text_length(view), 4000)
                self.assertLessEqual(max(len(t) for t in ui.texts(view)), 4000)
                self.assertTrue(view.has_components_v2())
                self.assertIsNone(view.timeout)                      # live cards never time out
                comps = view.to_components()
                self.assertEqual(len(comps), 1)                      # one Container
                self.assertEqual(comps[0]["type"], 17)
                json.dumps(comps)                                    # serialisable
                self.assertEqual(comps[0]["accent_color"], ui.accent(view))
                for b in ui.buttons(view):
                    self.assertLessEqual(len(b.label or ""), 80)
                    self.assertLessEqual(len(b.custom_id or ""), 100)
                    self.assertTrue(b.custom_id or b.url)
                for sel in [i for i in ui.walk(view) if isinstance(i, discord.ui.DynamicItem) and isinstance(i.item, discord.ui.Select)]:
                    self.assertLessEqual(len(sel.item.options), 25)
                self.assertNotIn("|", text(view))                    # no Markdown table anywhere
                self.assertNotIn("everyone", text(view))

    def test_accent_colours_and_emoji_come_from_one_table(self):
        expect = {"job card, queued": "queued", "job card, running": "running", "job card, valid": "valid",
                  "job card, not valid": "invalid", "job card, file refused": "refused", "job card, cancelled": "cancelled",
                  "job card, stopped": "stopped", "job card, internal": "refused", "accepted": "queued", "refused:": "refused",
                  "cancel answer": "refused", "/records": "record", "record announcement": "record", "/queue": "info",
                  "/about": "info", "/panel": "info", "confirm": "record", "settings": "info",
                  "history,": "info", "records manage": "record", "records confirm": "invalid", "refused": "refused"}
        for title, kind, view in ALL:
            key = max((k for k in expect if title.startswith(k)), key=len, default=None)
            if title.startswith(("details", "job card, valid", "note in", "audit copy", "history entry")):
                continue
            with self.subTest(title):
                self.assertIsNotNone(key, title)
                self.assertEqual(ui.accent(view), C[expect[key]], title)
                self.assertTrue(ui.texts(view)[0].startswith(ui.EMOJI[expect[key]]), ui.texts(view)[0])
        self.assertEqual({k: hex(v) for k, v in C.items() if k in ("queued", "running", "valid", "invalid", "refused", "record")},
                         {"queued": "0x99aab5", "running": "0x3b82f6", "valid": "0x22a06b", "invalid": "0xd64545",
                          "refused": "0x5c6670", "record": "0xf2b134"})
        self.assertEqual(ui.accent(card("job card, valid (SPRA")), C["valid"])
        self.assertEqual(ui.accent(card("details")), C["valid"])

    def test_mentions_are_only_the_submitters(self):
        for title, kind, view in ALL:
            with self.subTest(title):
                self.assertLessEqual(ui.mention_ids(view), {101, 102, 103})

    def test_plain_rendering_covers_everything(self):
        for title, kind, view in ALL:
            out = ui.plain(view, pc.NAMES)
            self.assertIn("accent #", out)
            self.assertNotIn("<@", out)
            self.assertNotIn("**", out)


class TestJobCards(unittest.TestCase):
    def test_queued(self):
        v = card("job card, queued (position 2")
        t = text(v)
        self.assertIn("⏳ **Check #12** · n = 14 · recipe", t)
        self.assertIn("`13_slow_n14_recipe.splan.json.xz` · by <@101>", t)
        self.assertIn("Position 2 in the slow lane (n = 13, 14) · estimated 2 h 36 min, 3 passes", t)
        self.assertIn("93,904,936,032 letters", t)
        self.assertEqual(labels(v), ["Cancel"])
        self.assertEqual(ui.buttons(v)[0].style, discord.ButtonStyle.danger)
        self.assertEqual(ui.buttons(v)[0].custom_id, "sp:cancel:12")
        self.assertIn("Starting now", text(card("job card, queued (starting")))
        self.assertIn("About 4,034,855 letters", text(card("job card, queued (starting")))       # a compressed word: estimated

    def test_running(self):
        v = card("job card, running (pass 1")
        t = text(v)
        self.assertIn("▰▰▰▱▱▱▱▱▱▱  31 %   pass 1 of 3", t)
        self.assertIn("29.1 G of 93.9 G letters", t)
        self.assertIn("12 min elapsed", t)
        self.assertIn("left", t)
        self.assertEqual(labels(v), ["Cancel"])
        e = text(card("job card, running, length not stated"))
        self.assertIn("~30 %", e)                               # an estimate says so
        self.assertIn("about 4.0 M", e)
        self.assertIn("estimated", e)

    def test_running_edge_values(self):
        j = pc.sample_job(1, 10, "word", 1000, 1, "w.txt", status="running", started=100, progress=(1, 1, 1000))
        t = text(ui.job_card(j, now=pc.NOW))
        self.assertIn("99 %", t)                                # never 100 % before the verdict
        j.progress = (0, 1, 0)
        t = text(ui.job_card(j, now=pc.NOW))
        self.assertIn("0 %", t)
        self.assertNotIn("left", t)                             # no ETA before anything was read
        self.assertEqual(ui.bar(0), "▱" * 10)
        self.assertEqual(ui.bar(1), "▰" * 10)
        self.assertEqual(ui.bar(0.5), "▰" * 5 + "▱" * 5)
        self.assertEqual(ui.bar(7), "▰" * 10)              # clamped
        self.assertEqual(ui.bar(-1), "▱" * 10)

    def test_valid_spra(self):
        v = card("job card, valid (SPRA")
        t = text(v)
        self.assertIn("✅ **Valid superpermutation** · n = 13", t)
        self.assertIn("**Length** 6,747,802,319 · **Alphabet** `123456789ABCD`", t)
        self.assertIn("**SHA-256** `aec1e713…5b2c9fd3` (full value under Details)", t)
        self.assertNotIn("0" * 20, t)                           # the full SHA is not on the card
        self.assertIn("SPRA 0.2, six-bit fields · header length matches", t)
        self.assertIn("Checked in 4 min 17 s", t)
        self.assertEqual(labels(v), ["Add to records", "Details"])
        self.assertEqual([b.style for b in ui.buttons(v)], [discord.ButtonStyle.success, discord.ButtonStyle.secondary])

    def test_valid_recipe_and_added(self):
        t = text(card("job card, valid (recipe"))
        self.assertIn("Recipe: generator `gen14` · length matches · word hash not stated", t)
        self.assertIn("2 passes; all saw the same length and SHA-256", t)
        self.assertEqual(labels(card("job card, valid (recipe")), ["Details"])        # nothing to add
        v = card("job card, valid, after Add")
        self.assertEqual(labels(v), ["Added ✓", "Details"])
        self.assertTrue(ui.buttons(v)[0].disabled)
        self.assertIn("**new record** for n = 13", text(v))
        self.assertIn("Contributor role given.", text(v))

    def test_not_valid(self):
        v = card("job card, not valid")
        t = text(v)
        self.assertIn("❌ **Not a superpermutation** · n = 6", t)
        self.assertIn("**Missing permutations** 4 of 720", t)
        self.assertIn("**Length** 872 · **Alphabet** `012345`", t)
        self.assertIn("Checked in 0.1 s", t)
        self.assertEqual(labels(v), ["Details"])
        t = text(card("job card, not valid (3 passes"))
        self.assertIn("5 of 40,320", t)
        self.assertIn("**Foreign bytes** 3", t)
        self.assertIn("3 passes", t)

    def test_header_mismatch_is_not_called_missing(self):
        j = pc.sample_job(1, 6, "recipe", 872, 1, "r.splan.json.xz", status="done")
        res = pc.result_valid(6, 900, "recipe", ok=False, generator="gen14", recipe_length=872, length_matches=False,
                              sha_matches=None)
        t = text(ui.job_card(j, res, now=pc.NOW))
        self.assertIn("Word and header disagree", t)
        self.assertIn("DIFFERS from the recipe's 872", t)
        self.assertNotIn("Missing", t)

    def test_refused_file_only_the_reason(self):
        v = card("job card, file refused")
        t = text(v)
        self.assertIn("⛔ **File refused**", t)
        self.assertIn("SPRA decoder: extra bytes after the last token (at letter 46,225)", t)
        for never in ("40,320", "Missing", "Length", "DIFFERS", "tuple", "spra:"):
            self.assertNotIn(never, t)
        self.assertEqual(labels(v), ["Details"])

    def test_cancelled_stopped_internal(self):
        t = text(card("job card, cancelled while running"))
        self.assertIn("Cancelled by alice after 3 min (pass 1 of 3, 10.6 %)", t)
        t = text(card("job card, cancelled while waiting"))
        self.assertIn("Cancelled by Jakub Halfar before it started", t)
        t = text(card("job card, stopped"))
        self.assertIn("Stopped, no verdict", t)
        self.assertIn("timed out", t)
        t = text(card("job card, internal"))
        self.assertIn("Not checked", t)
        self.assertIn("internal error: boom", t)
        for p in ("job card, cancelled while running", "job card, cancelled while waiting", "job card, stopped", "job card, internal"):
            self.assertEqual(labels(card(p)), ["Details"], p)

    def test_buttons_per_state(self):
        for title, kind, view in ALL:
            if not title.startswith("job card"):
                continue
            with self.subTest(title):
                ls = labels(view)
                if title.startswith("job card, queued") or title.startswith("job card, running"):
                    self.assertEqual(ls, ["Cancel"])
                else:
                    self.assertNotIn("Cancel", ls)
                    self.assertIn("Details", ls)
                    self.assertEqual(ls.count("Details"), 1)
                    self.assertLessEqual(len(ls), 2)
                self.assertEqual("Add to records" in ls or "Added ✓" in ls,
                                 title.startswith("job card, valid") and "recipe" not in title)

    def test_display_name_fallback_and_hostile_names(self):
        j = pc.sample_job(1, 6, "word", 100, 1, "a b`c\n*.txt", uid=0, uname="Eve `**@everyone**`\n\nx" + "y" * 100,
                          status="queued")
        t = text(ui.job_card(j, position=1, now=pc.NOW))
        self.assertIn("`a_b_c__.txt`", t)                       # file name reduced to [A-Za-z0-9._-]
        self.assertLessEqual(len(t.split("\n")[1]), 140)
        self.assertEqual(ui.mention_ids(ui.job_card(j, position=1, now=pc.NOW)), set())


class TestMessages(unittest.TestCase):
    def test_refusal_cards(self):
        for title, kind, view in ALL:
            if not title.startswith("refused:"):
                continue
            with self.subTest(title):
                self.assertEqual(kind, "ephemeral")
                lines = text(view).split("\n")
                self.assertTrue(lines[0].startswith("⛔ **"))
                self.assertEqual(len(lines), 3)                 # title, reason, what to do
                self.assertEqual(labels(view), [])
        self.assertIn("n = 15 is above this bot's limit (n ≤ 14). Nothing was run.", text(card("refused: n above")))
        self.assertIn("File refused", text(card("refused: file in the wrong")))

    def test_accepted(self):
        v = card("accepted (ephemeral)")
        self.assertIn("Check #12 accepted", text(v))
        self.assertLess(len(text(v)), 200)
        self.assertEqual([b.url for b in ui.buttons(v)], [pc.URL])
        self.assertEqual(len(ui.texts(ui.accepted_card(pc.sample_job(1, 6, "word", 1, 1, "w"), None))), 1)   # no link: no button

    def test_details(self):
        t = text(card("details"))
        self.assertIn("```", t)
        self.assertIn("sha256", t)
        self.assertIn("aec1e713", t)
        self.assertIn("5b2c9fd3", t)
        self.assertIn("6,747,802,319", t)
        big = {"k%d" % i: "x" * 300 for i in range(60)}
        v = ui.details_card(pc.sample_job(1, 6, "word", 1, 1, "w"), dict(big, ok=True, n=6))
        self.assertTrue(ui.check_limits(v))
        # the legend explains the fields that are present (and only those), and says the card closes by itself
        self.assertIn("`missing` permutations that never occur", t)
        self.assertNotIn("`pass_mismatch`", t)
        self.assertIn("closes by itself", t)
        every = {k: 1 for k in ui.FIELD_HELP}
        self.assertTrue(ui.check_limits(ui.details_card(pc.sample_job(1, 13, "word", 1, 1, "w"), dict(every, sha256="ab" * 32))))

    def test_announcement(self):
        v = card("record announcement")
        t = text(v)
        self.assertIn("\U0001f3c6 **New record for n = 13**", t)
        self.assertIn("6,747,801,994 letters · 399 shorter than the previous record (6,747,802,393, Jakub Halfar)", t)
        self.assertIn("by <@101> · `n13_new.txt.xz` · SHA-256 `%s`" % ("ab" * 32), t)
        b = ui.buttons(v)[0]
        self.assertEqual((b.label, b.url, b.style), ("Open the check", pc.URL, discord.ButtonStyle.link))
        self.assertIn("first verified word", text(card("record announcement, first")))
        self.assertEqual(ui.buttons(ui.announcement_card(5, 1, None, 1, "x", "f.txt", "ab" * 32, None)), [])

    def test_panel(self):
        v = card("/panel")
        self.assertEqual(labels(v), ["Check a word"])
        self.assertEqual(ui.buttons(v)[0].custom_id, "sp:panel:check")
        self.assertTrue(v.is_persistent())

    def test_about(self):
        v = card("/about")
        t = text(v)
        for head in ("What it checks", "Formats", "Limits", "Commands"):
            self.assertIn("**%s**" % head, t)
        self.assertIn("Send Messages, Embed Links, Read Message History (and Manage Roles, only with the Contributor role)", t)
        self.assertNotIn("Thread", t)
        self.assertEqual(labels(v), ["Repository", "Recipe format", "SPRA format"])
        self.assertTrue(all(b.url.startswith("https://github.com/") for b in ui.buttons(v)))
        self.assertIn("RECIPE.md", ui.buttons(v)[1].url)

    def test_permissions_integer(self):
        perms = discord.Permissions(send_messages=True, embed_links=True, read_message_history=True)
        self.assertEqual(ui.permission_value(False), perms.value)
        self.assertEqual(ui.permission_value(False), 2048 + 16384 + 65536)
        self.assertEqual(ui.permission_value(False), 83968)
        perms.manage_roles = True
        self.assertEqual(ui.permission_value(True), perms.value)
        self.assertEqual(ui.permission_value(True), 83968 + 268435456)
        self.assertEqual(ui.permission_value(True), 268519424)
        # the threads permissions are gone
        flags = [n for n, b in ui.PERMISSION_BITS] + [ui.ROLE_PERMISSION[0]]
        self.assertFalse([f for f in flags if "Thread" in f])

    def test_formatters(self):
        self.assertEqual(ui.fmt_dur(0), "under a second")
        self.assertEqual(ui.fmt_dur(0.4), "under a second")
        self.assertEqual(ui.fmt_dur(1), "1 s")
        self.assertEqual(ui.fmt_dur(89), "89 s")
        self.assertEqual(ui.fmt_dur(120), "2 min")
        self.assertEqual(ui.fmt_dur(2 * 3600 + 36 * 60), "2 h 36 min")
        self.assertEqual(bot.fmt_dur(0), "under a second")
        self.assertEqual(ui.fmt_took(0.1), "0.1 s")
        self.assertEqual(ui.fmt_took(257), "4 min 17 s")
        self.assertEqual(ui.fmt_took(3900), "1 h 05 min")
        self.assertEqual(ui.fmt_took(0.01), "under 0.1 s")
        self.assertEqual(ui.fmt_letters(29.1e9), "29.1 G")
        self.assertEqual(ui.fmt_letters(1.2e6), "1.2 M")
        self.assertEqual(ui.fmt_letters(950), "950")
        self.assertEqual(ui.short_sha("a" * 64), "a" * 8 + "…" + "a" * 8)
        self.assertEqual(ui.short_sha("abc"), "abc")
        self.assertEqual(ui.clean_name("..\\a b/c d.txt"), "c_d.txt")
        self.assertEqual(ui.clean_name(""), "file")
        self.assertEqual(ui.refusal_reason("recipe refused: bad seed"), "recipe: bad seed")
        self.assertEqual(ui.refusal_reason("SPRA decoder refused the file: spra: x (at letter 1234567, tuple 3)"),
                         "SPRA decoder: x (at letter 1,234,567)")


class TestRecords(unittest.TestCase):
    def test_block_is_aligned_monospace(self):
        t = pc.sample_table()
        block = ui.records_block(t)
        lines = block.split("\n")
        self.assertEqual(lines[0].split(), ["n", "length", "holder", "status"])
        self.assertEqual(len({len(l.rstrip()) for l in lines[1:] if "verified" in l}), 1)       # same width: aligned
        col = lines[0].index("status")
        for l in lines[1:]:
            self.assertEqual(l[col - 2:col], "  ")
            self.assertIn(l[col], "✓○")
        end_of_length = lines[0].index("length") + len("length")
        for l in lines[1:]:
            self.assertTrue(l[end_of_length - 1].isdigit(), l)               # numbers right-aligned under "length"
        self.assertIn("14   93,904,804,425  Jakub Halfar", block)            # a shorter claim above the verified record
        self.assertIn("\n     93,904,936,032  William Echols", block)
        self.assertNotIn("|", block)
        v = ui.records_card(t)
        tx = text(v)
        self.assertIn("\U0001f3c6 **Shortest known superpermutations**", tx)
        self.assertIn("```\n n ", tx)
        self.assertIn("✓ verified", tx)
        self.assertIn("○ claim", tx)
        sel = [i.item for i in ui.walk(v) if isinstance(i, discord.ui.DynamicItem)][0]
        self.assertEqual([o.value for o in sel.options], ["5", "7", "8", "9", "10", "11", "12", "13", "14"])
        self.assertEqual(sel.custom_id, "sp:records:n")

    def test_rows_shorter_claim_above_verified(self):
        rows = ui.record_rows(pc.sample_table())
        r9 = [(n, e["length"], e["verified"]) for n, e in rows if e["length"] in (407999, 408000)]   # the rows of n = 9
        self.assertEqual(r9, [("9", 407999, False), (None, 408000, True)])
        self.assertEqual([n for n, e in rows if n is not None], ["5", "7", "8", "9", "10", "11", "12", "13", "14"])

    def test_real_table(self):
        t = records.load(os.path.join(BOT, "records.json"))
        v = ui.records_card(t)
        self.assertTrue(ui.check_limits(v))
        self.assertIn("4,034,855", text(v))
        # 25 options at most, whatever the table holds
        t2 = {"records": {str(n): [{"length": n, "sha256": None, "submitter": "x", "source": "s", "date": "d",
                                    "verified": False, "kind": "claim"}] for n in range(2, 40)}}
        self.assertTrue(ui.check_limits(ui.records_card(t2)))
        self.assertEqual(len([i for i in ui.walk(ui.records_card(t2)) if isinstance(i, discord.ui.DynamicItem)][0].item.options), 25)

    def test_details_of_one_n(self):
        t = pc.sample_table()
        v = ui.record_details_card(t, "9")
        tx = text(v)
        self.assertIn("407,999 letters", tx)
        self.assertIn("○ claim", tx)
        self.assertIn("408,000 letters", tx)
        self.assertIn("✓ verified", tx)
        self.assertIn("`%s`" % ("cd" * 32), tx)
        self.assertIn("[source](<https://github.com/a/b/blob/main/w9.txt.xz>)", tx)
        self.assertIn("<@101>", text(ui.record_details_card(t, "5")))       # the stored user id is shown as a mention
        self.assertIn("No entries", text(ui.record_details_card(t, "99")))
        many = {"records": {"7": [{"length": 5000 + k, "sha256": "ab" * 32, "submitter": "x" * 80, "source": "https://x.y/" + "z" * 400,
                                   "date": "2026", "verified": True, "kind": "word"} for k in range(50)]}}
        self.assertTrue(ui.check_limits(ui.record_details_card(many, "7")))


class TestQueue(unittest.TestCase):
    def test_queue_card(self):
        v = card("/queue (ephemeral)")
        t = text(v)
        self.assertIn("**fast lane (n ≤ 12)**", t)
        self.assertIn("**slow lane (n = 13, 14)**", t)
        self.assertIn("\U0001f504 #5 · n = 10 · <@102> · 30 %", t)
        self.assertIn("pass 2 of 3", t)
        self.assertIn("⏳ 1. #6 · n = 11 · <@103>", t)
        self.assertIn("in memory", t)
        self.assertEqual(labels(v), ["Open", "Open"])
        self.assertTrue(all(b.url == pc.URL for b in ui.buttons(v)))
        sections = [i for i in ui.walk(v) if isinstance(i, discord.ui.Section)]
        self.assertEqual(len(sections), 2)
        self.assertIn("idle", text(card("/queue, idle")))
        self.assertEqual(labels(card("/queue, idle")), [])

    def test_full_queue_fits(self):
        run = pc.sample_job(1, 10, "word", 100, 1, "w", status="running", started=5, progress=(1, 1, 10))
        waiting = [pc.sample_job(10 + k, 11, "word", 100, 1, "w", uid=200 + k, uname="u%d" % k) for k in range(6)]
        sched = SimpleNamespace(lanes={"fast": SimpleNamespace(running=run, waiting=waiting),
                                       "slow": SimpleNamespace(running=run, waiting=waiting)})
        v = ui.queue_card(sched, now=pc.NOW, urls={1: pc.URL})
        self.assertTrue(ui.check_limits(v))


class TestPrivateCards(unittest.TestCase):
    def test_note_in_the_channel_has_no_secrets(self):
        for phase in ("queued", "running", "finished"):
            v = card("note in the channel, private check, %s" % phase)
            t = text(v)
            self.assertEqual(t, "🔒 Private check by <@101> · n = 13 · %s" % phase)
            for never in ("secret", ".txt", "letters", "SHA", "Valid", "alid", "▰", "%", "job #"):
                self.assertNotIn(never, t)
            self.assertEqual(ui.buttons(v), [])
            self.assertEqual(ui.accent(v), C["queued"])
            self.assertEqual(ui.component_count(v), 2)

    def test_dm_cards_are_marked_and_audit_copy_has_details_only(self):
        for title in ("job card, queued, private (direct message)", "job card, running, private (direct message)",
                      "job card, valid, private (direct message, can be added)"):
            self.assertIn("🔒 private", ui.texts(card(title))[0], title)
        self.assertEqual(labels(card("job card, valid, private (direct message, can be added)")), ["Add to records", "Details"])
        for title in ("audit copy of a private check, finished", "audit copy of a private check, queued"):
            v = card(title)
            self.assertEqual(labels(v), ["Details"])
            self.assertEqual(ui.buttons(v)[0].custom_id, "sp:audit:%s" % ("30" if "finished" in title else "31"))
        self.assertIn("secret_word_n13.spra.xz", text(card("audit copy of a private check, finished")))

    def test_accepted_and_confirm_and_settings(self):
        t = text(card("accepted, private"))
        self.assertIn("Private: the card is in your direct messages.", t)
        self.assertEqual(ui.buttons(card("accepted, private"))[0].label, "Open the direct message")
        v = card("confirm publish")
        self.assertIn("This makes the result public: entry in the table and an announcement in <#900>.", text(v))
        self.assertEqual(labels(v), ["Publish", "Keep private"])
        self.assertEqual([b.custom_id for b in ui.buttons(v)], ["sp:publish:30", "sp:keep:30"])
        self.assertIn("I cannot send you a direct message.", text(card("refused: closed direct messages")))
        v = card("settings")
        self.assertIn("**public**", text(v))
        sel = [i.item for i in ui.walk(v) if isinstance(i, discord.ui.DynamicItem)][0]
        self.assertEqual((sel.custom_id, [(o.value, o.default) for o in sel.options]), ("sp:settings:vis",
                         [("private", False), ("public", True)]))

    def test_queue_views(self):
        other = text(card("/queue with private checks, as another user"))
        self.assertIn("🔒 private check · n = 13", other)
        self.assertIn("1. 🔒 private check · n = 11", other)
        self.assertIn("<@101>", other)                                      # the public one in the fast lane
        for hidden in ("<@102>", "<@103>", "secret", "bobs", "letters"):
            self.assertNotIn(hidden, other)
        self.assertEqual(labels(card("/queue with private checks, as another user")), [])
        sub = card("/queue with private checks, as the submitter")
        self.assertIn("secret_n13.txt.xz", text(sub))
        self.assertEqual(labels(sub), ["Open"])                             # the link to the DM card, only for the submitter
        self.assertNotIn("bobs", text(sub))
        staff = card("/queue with private checks, as staff")
        self.assertIn("<@102>", text(staff))
        self.assertIn("6.7 G letters", text(staff))
        self.assertIn("31 %", text(staff))
        self.assertNotIn("secret", text(staff))
        self.assertEqual(labels(staff), ["Cancel #5", "Cancel #6"])
        self.assertTrue(all(b.style == discord.ButtonStyle.danger for b in ui.buttons(staff)))
        admin = card("/queue with private checks, as admin")
        self.assertIn("secret_n13.txt.xz", text(admin))
        self.assertIn("bobs_n11.txt", text(admin))
        self.assertEqual(labels(admin), ["Cancel #5", "Cancel #6"])
        for v in (staff, admin, sub):
            self.assertEqual([b.url for b in ui.buttons(v) if b.url and v is not sub], [])

    def test_privacy_texts(self):
        t = text(card("/about"))
        self.assertIn("**Privacy**", t)
        for need in ("Checks are public by default", "leaves only a small note here (who and n, no result)",
                     "run `/check` in a direct message with me", "Moderators (Manage Messages) see that a private check runs",
                     "audit channel", "the host of the bot can see all checks", "never reposted", "/settings"):
            self.assertIn(need, t)
        p = text(card("/panel"))
        self.assertIn("public by default", p)
        self.assertIn("direct message", p)
        self.assertIn("/settings", p)
        self.assertLess(len(p), len(ui.PRIVACY_TEXT))                        # the panel has the shorter version


class TestWiring(unittest.TestCase):
    """the cards plug into discord.py's own machinery: a message is turned back into a view and the right handler runs."""

    def dispatch(self, view, custom_id, ctype, data=None):
        seen = []

        async def go():
            ui.HANDLERS.update({"job": lambda a, j, i: _rec(seen, ("job", a, j)), "panel": lambda i: _rec(seen, ("panel",)),
                                "records": lambda n, i: _rec(seen, ("records", n)),
                                "settings": lambda v, i: _rec(seen, ("settings", v)),
                                "x": lambda k, a, v, i: _rec(seen, ("x", k, a, v))})
            store = ViewStore(SimpleNamespace())
            store.add_dynamic_items(ui.JobButton, ui.PanelButton, ui.RecordsSelect, ui.SettingsSelect, ui.XButton, ui.XSelect)
            comps = [_component_factory(c) for c in view.to_components()]
            msg = SimpleNamespace(components=comps, flags=SimpleNamespace(components_v2=True), id=5)
            d = {"custom_id": custom_id, "component_type": ctype}
            d.update(data or {})
            store.dispatch_dynamic_items(ctype, custom_id, SimpleNamespace(message=msg, data=d))
            await asyncio.sleep(0.2)
        asyncio.run(go())
        return seen

    def test_buttons_work_from_the_message_alone(self):
        v = card("job card, valid (SPRA")
        self.assertEqual(self.dispatch(v, "sp:details:15", 2), [("job", "details", 15)])
        self.assertEqual(self.dispatch(v, "sp:add:15", 2), [("job", "add", 15)])
        self.assertEqual(self.dispatch(card("job card, running (pass"), "sp:cancel:12", 2), [("job", "cancel", 12)])
        self.assertEqual(self.dispatch(card("/panel"), "sp:panel:check", 2), [("panel",)])

    def test_select_works_from_the_message_alone(self):
        v = card("/records")
        self.assertEqual(self.dispatch(v, "sp:records:n", 3, {"values": ["13"]}), [("records", "13")])

    def test_new_private_buttons_and_the_settings_select_work_from_the_message_alone(self):
        self.assertEqual(self.dispatch(card("confirm publish"), "sp:publish:30", 2), [("job", "publish", 30)])
        self.assertEqual(self.dispatch(card("confirm publish"), "sp:keep:30", 2), [("job", "keep", 30)])
        self.assertEqual(self.dispatch(card("audit copy of a private check, finished"), "sp:audit:30", 2), [("job", "audit", 30)])
        self.assertEqual(self.dispatch(card("/queue with private checks, as staff"), "sp:cancel:5", 2), [("job", "cancel", 5)])
        self.assertEqual(self.dispatch(card("settings"), "sp:settings:vis", 3, {"values": ["private"]}), [("settings", "private")])

    def test_history_and_records_buttons_work_from_the_message_alone(self):
        v = card("history, own page 1 of 3 (ephemeral)")
        self.assertEqual(self.dispatch(v, "sp:x:hp:102:1", 2), [("x", "hp", "102:1", None)])
        self.assertEqual(self.dispatch(v, "sp:y:hs:102", 3, {"values": ["30"]}), [("x", "hs", "102", "30")])
        e = card("history entry, own valid private check (ephemeral)")
        self.assertEqual(self.dispatch(e, "sp:x:hd:31", 2), [("x", "hd", "31", None)])
        self.assertEqual(self.dispatch(e, "sp:x:hr:31", 2), [("x", "hr", "31", None)])
        c = card("records confirm removing the record (ephemeral)")
        ids = [b.custom_id for b in ui.buttons(c)]
        self.assertEqual([i.split(":")[2] for i in ids], ["ry", "rn"])
        self.assertEqual(self.dispatch(c, ids[0], 2), [("x", "ry", ids[0].split(":", 3)[3], None)])
        m = card("/records, details for n = 9, as admin (Manage entry) (ephemeral)")
        self.assertEqual(self.dispatch(m, "sp:y:rm:9", 3, {"values": ["408000-"]}), [("x", "rm", "9", "408000-")])
        for title, kind, view in ALL:
            for b in ui.buttons(view):
                if b.custom_id and b.custom_id.startswith(("sp:x:", "sp:y:")):
                    self.assertLessEqual(len(b.custom_id), 100)

    def test_history_cards(self):
        v = card("history, own page 1 of 3 (ephemeral)")
        t = text(v)
        self.assertIn("page 1 of 3", t)
        self.assertIn("<t:1761052800:f>", t)                          # Discord timestamps
        self.assertIn("`n12_candidate.txt` \u00b7 \u274c \u00b7 522,737,175 letters \u00b7 #30", t)
        self.assertIn("\U0001f512", t.split("\n")[1])                 # the private row is marked
        self.assertEqual(labels(v), ["Previous", "Next"])
        self.assertEqual([b.disabled for b in ui.buttons(v)], [True, False])
        self.assertEqual([b.disabled for b in ui.buttons(card("history, page 3 of 3 (ephemeral)"))], [False, True])
        self.assertEqual(labels(card("history, empty (ephemeral)")), [])
        masked = text(card("history, as staff looking at another user (private checks masked) (ephemeral)"))
        self.assertIn("\U0001f512 private check", masked)
        for hidden in ("secret_n13", "dm_word", "6,747,802,393", "4,034,855"):
            self.assertNotIn(hidden, masked)
        self.assertIn("n12_candidate.txt", masked)                    # public rows stay visible to staff
        e = card("history entry, own valid private check (ephemeral)")
        self.assertIn("`%s`" % ("1f" * 32), text(e))                   # the full SHA-256
        self.assertEqual(labels(e), ["Delete from history", "Rename"])
        em = card("history entry, as staff: private check of another user, masked (ephemeral)")
        self.assertNotIn("1f" * 32, text(em))
        self.assertNotIn("secret_n13", text(em))
        self.assertEqual(ui.accent(em), C["queued"])                   # colour and emoji must not give the verdict away
        self.assertTrue(ui.texts(em)[0].startswith("🔒"))
        self.assertEqual(labels(em), ["Delete from history"])

    def test_records_management_cards(self):
        t = text(card("/records, details for n = 9, as the submitter alice (with Manage entry) (ephemeral)"))
        self.assertIn("<t:1761052800:f>", t)
        self.assertIn("name `alice_n9.txt`", t)
        self.assertIn("2026-10", t)                                    # the seed claim keeps its date
        v = card("/records, details for n = 9, as admin (Manage entry) (ephemeral)")
        sel = [i.item for i in ui.walk(v) if isinstance(i, discord.ui.DynamicItem)][0]
        self.assertEqual(sel.placeholder, "Manage entry")
        self.assertEqual(labels(card("records manage entry, the record, as admin (ephemeral)")), ["Delete", "Rename"])
        self.assertEqual(labels(card("records manage entry, a non-record, as its submitter (ephemeral)")), ["Delete", "Rename"])
        c = card("records confirm removing the record (ephemeral)")
        self.assertIn("It is the current record", text(c))
        self.assertEqual(labels(c), ["Remove", "Keep"])
        self.assertNotIn("current record", text(card("records confirm removing a non-record (ephemeral)")))

    def test_custom_ids_are_the_persistent_ones(self):
        for title, kind, view in ALL:
            for b in ui.buttons(view):
                if b.custom_id:
                    self.assertRegex(b.custom_id, r"^sp:(cancel|add|details|publish|keep|audit):\d+$|^sp:panel:check$|^sp:[xy]:[a-z]+:[0-9a-z:\-]+$")

    def test_modal_fields_exist_in_discord_py(self):
        for name in ("Modal", "Label", "FileUpload", "TextInput", "LayoutView", "Container", "TextDisplay", "Section",
                     "Separator", "ActionRow", "Thumbnail", "Select", "DynamicItem"):
            self.assertTrue(hasattr(discord.ui, name), name)


async def _rec(seen, item):
    seen.append(item)


class TestEphemeralTTL(unittest.TestCase):
    """Details and record look-ups are ephemeral cards that remove themselves (cards.reply delete_after)."""

    def test_first_response_gets_delete_after(self):
        import asyncio
        import cards
        got = {}

        class Resp:
            def is_done(self):
                return False

            async def send_message(self, **kw):
                got.update(kw)

        class Inter:
            response = Resp()
        asyncio.run(cards.reply(Inter(), "VIEW", delete_after=600))
        self.assertEqual(got.get("delete_after"), 600)
        self.assertTrue(got.get("ephemeral"))
        got.clear()
        asyncio.run(cards.reply(Inter(), "VIEW"))
        self.assertNotIn("delete_after", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)

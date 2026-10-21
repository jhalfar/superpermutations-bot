"""tests of the SPRA decoder (gens/spra.c, spra.py) and of SPRA words in check_any and the bot. Offline.

    python -I tests/test_spra.py

The encoder below is written from the format description (docs/reduced_binary.md of github.com/Tholsh/SPRA), as a
second, independent reading of the format: every word it writes must decode to the same letters.
    SUPERPERM_SPRA_CORPUS   folder with .spra.xz words (e.g. the words/ folder of Tholsh/SPRA): each is decoded and
                            checked, and must be valid with the header length (n <= 12 only, to keep it short)
"""
import glob
import hashlib
import io
import lzma
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOT = os.path.join(ROOT, "bot")
CHECKER = os.path.join(ROOT, "checker")
sys.path.insert(0, CHECKER)
sys.path.insert(0, BOT)
sys.path.insert(0, HERE)
import check_any  # noqa: E402
import core  # noqa: E402
import discord_bot as bot  # noqa: E402
import registry  # noqa: E402
import spra  # noqa: E402
from test_check import greedy_word, ALPH, W10  # noqa: E402

CORPUS = os.environ.get("SUPERPERM_SPRA_CORPUS")


# ----------------------------------------------------------------------------------------------- reference encoder

def encode(ids, n, bits=4, zero_based=False, rle=True, version=2):
    """SPRA bytes of the word given as symbol ids 0..n-1."""
    L = len(ids)
    M = (1 << bits) - 1
    tuples, depth = [], 0
    for i in range(n - 1, L):
        if len(set(ids[i - n + 1:i + 1])) == n:
            depth += 1
            continue
        s = ids[i]
        dist = next((d for d in range(1, i + 1) if ids[i - d] == s), None)
        if dist is None:
            raise ValueError("first appearance in a dirty window")
        o = n - dist
        if depth > M or not -(1 << (bits - 1)) <= o < (1 << (bits - 1)):
            raise ValueError("field out of range")
        tuples.append((depth, o))
        depth = 0
    tokens = []
    i = 0
    while i < len(tuples):
        j = i
        while rle and j + 1 < len(tuples) and tuples[j + 1] == tuples[i]:
            j += 1
        tokens.append(tuples[i])
        extra = j - i
        while extra:
            r = min(extra, M - 1)
            tokens.append((r, 0))
            extra -= r
        i = j + 1
    vals = [(d << bits) | (o & M) for d, o in tokens]
    if bits == 4:
        body = bytes(vals)
    else:
        out = bytearray()
        for k in range(0, len(vals), 2):
            a = vals[k]
            if k + 1 < len(vals):
                b = vals[k + 1]
                out += bytes([a >> 4, ((a & 15) << 4) | (b >> 8), b & 255])
            else:
                out += bytes([a >> 4, (a & 15) << 4])
        body = bytes(out)
    head = b"SPRA" + bytes([version, 0x44 if bits == 4 else 0x66, 1 if zero_based else 0, n])
    head += L.to_bytes(8, "little") + len(tuples).to_bytes(8, "little")
    return head + bytes(ids[:min(L, n - 1)]) + body


def decode(data, *args):
    p = subprocess.run([registry.binary("spra")] + list(args), input=data, capture_output=True)
    return p.returncode, p.stdout, p.stderr.decode(errors="replace")


def ids_of(word, alph=ALPH):
    return [alph.index(chr(c)) for c in word]


H = bytes.fromhex("5350524102")


class TestVectors(unittest.TestCase):
    """the examples of the format description, byte for byte."""

    def test_four_bit(self):
        v = H + bytes.fromhex("440003") + (9).to_bytes(8, "little") + (1).to_bytes(8, "little") + bytes.fromhex("000131")
        self.assertEqual(decode(v)[:2], (0, b"123121321"))
        self.assertEqual(encode([0, 1, 2, 0, 1, 0, 2, 1, 0], 3), v)

    def test_six_bit(self):
        v = H + bytes.fromhex("660003") + (9).to_bytes(8, "little") + (1).to_bytes(8, "little") + bytes.fromhex("00010c10")
        self.assertEqual(decode(v)[:2], (0, b"123121321"))
        self.assertEqual(encode([0, 1, 2, 0, 1, 0, 2, 1, 0], 3, bits=6), v)

    def test_repeat_markers(self):
        for bits, body in ((4, "0220"), (6, "002080")):
            v = H + bytes([0x44 if bits == 4 else 0x66, 0, 3]) + (5).to_bytes(8, "little") + (3).to_bytes(8, "little") \
                + bytes.fromhex("0000" + body)
            self.assertEqual(decode(v)[:2], (0, b"11111"))
            self.assertEqual(encode([0] * 5, 3, bits=bits), v)

    def test_info(self):
        v = H + bytes.fromhex("440103") + (9).to_bytes(8, "little") + (1).to_bytes(8, "little") + bytes.fromhex("000131")
        rc, out, _ = decode(v, "--info")
        self.assertEqual(out.split(), [b"3", b"9", b"1", b"4", b"1", b"2"])
        self.assertEqual(decode(v)[1], b"012010210")          # zero-based labels


class TestRoundTrip(unittest.TestCase):
    def test_greedy_words_both_widths_and_labels(self):
        for n in range(3, 9):
            word = greedy_word(n)
            ids = ids_of(word)
            for bits in (4, 6):
                for zb in (False, True):
                    for rle in (True, False):
                        try:
                            data = encode(ids, n, bits, zb, rle)
                        except ValueError:
                            continue                        # outside the four-bit limits
                        rc, out, err = decode(data)
                        self.assertEqual(rc, 0, (n, bits, zb, err))
                        lab = spra.LABELS[1 if zb else 0]
                        self.assertEqual(out, "".join(lab[i] for i in ids).encode(), (n, bits, zb, rle))

    def test_version_1_without_markers(self):
        ids = ids_of(greedy_word(6))
        rc, out, _ = decode(encode(ids, 6, rle=False, version=1))
        self.assertEqual(rc, 0)
        self.assertEqual(len(out), len(ids))

    def test_limit(self):
        data = encode(ids_of(greedy_word(7)), 7)
        rc, out, _ = decode(data, "--limit", "1000")
        self.assertEqual((rc, len(out)), (0, 1000))


class TestRefused(unittest.TestCase):
    """every broken rule is refused (exit 1), never decoded into something else."""

    def setUp(self):
        self.ids = ids_of(greedy_word(6))
        self.good = encode(self.ids, 6)
        self.good6 = encode(self.ids, 6, bits=6)

    def refused(self, data, why=""):
        rc, out, err = decode(data)
        self.assertEqual(rc, 1, why + " " + err)
        return err

    def test_header(self):
        g = bytearray(self.good)
        for pos, val, why in ((0, ord("X"), "magic"), (4, 3, "version"), (5, 0x55, "layout"), (6, 2, "flags"),
                              (7, 0, "n = 0"), (7, 33, "n = 33")):
            b = bytearray(g)
            b[pos] = val
            self.refused(bytes(b), why)
        self.refused(self.good[:20], "short header")
        v1six = encode(self.ids, 6, bits=6, rle=False, version=1)     # version 1 also had six-bit fields
        rc, out, err = decode(v1six)
        self.assertEqual((rc, len(out)), (0, len(self.ids)), err)

    def test_body(self):
        self.refused(self.good[:-1], "truncated")
        self.refused(self.good + b"\x00", "extra byte")
        b = bytearray(self.good)
        b[24] = 9
        self.refused(bytes(b), "seed symbol >= n")
        b = bytearray(self.good)
        b[16:24] = (int.from_bytes(self.good[16:24], "little") + 1).to_bytes(8, "little")
        self.refused(bytes(b), "one tuple more than in the body")
        b = bytearray(self.good)
        b[8:16] = (len(self.ids) - 1).to_bytes(8, "little")     # the final clean run is one shorter: still a file
        rc, out, _ = decode(bytes(b))
        self.assertEqual((rc, len(out)), (0, len(self.ids) - 1))
        b[8:16] = (10).to_bytes(8, "little")                     # the tuples do not fit into 10 letters
        self.refused(bytes(b), "header length too short for the tuples")

    def test_markers(self):
        n = 3
        base = H + bytes([0x44, 0, n]) + (5).to_bytes(8, "little")
        self.refused(base + (3).to_bytes(8, "little") + bytes.fromhex("0000" + "2002"), "marker first")
        self.refused(base + (16).to_bytes(8, "little") + bytes.fromhex("0000" + "02f0"), "reserved (15, 0)")
        self.refused(base + (2).to_bytes(8, "little") + bytes.fromhex("0000" + "0220"), "marker overshoots")

    def test_padding(self):
        ids = [0] * 5                                     # one literal + one marker: two tokens, no padding
        data = encode(ids, 3, bits=6, rle=False)          # three tokens: an odd count, so a half byte of padding
        self.assertEqual(decode(data)[0], 0)
        b = bytearray(data)
        b[-1] |= 1
        self.refused(bytes(b), "padding bits")

    def test_clean_window_copy(self):
        # (1, +0) is not a marker in version 1: one clean step, then a copy at distance n, which is clean: refused
        v = b"SPRA" + bytes([1, 0x44, 0, 3]) + (4).to_bytes(8, "little") + (1).to_bytes(8, "little") + bytes.fromhex("0001" + "10")
        self.refused(v, "dirty tuple that makes a clean window")


class TestCheckAny(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spra-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data, xz=True):
        p = os.path.join(self.tmp, name)
        with (lzma.open(p, "wb") if xz else open(p, "wb")) as f:
            f.write(data)
        return p

    def test_valid_word_xz_and_raw(self):
        ids = ids_of(greedy_word(8))
        for xz in (True, False):
            p = self.write("w8.spra" + (".xz" if xz else ""), encode(ids, 8, bits=6), xz)
            self.assertEqual(check_any.peek_full(p), ("spra", 8, len(ids)))
            res = check_any.check_any(p, threads=1)
            self.assertTrue(res["ok"], res)
            self.assertEqual((res["kind"], res["n"], res["length"], res["spra_version"]), ("spra", 8, len(ids), 2))
            self.assertTrue(res["length_matches"])
            lab = spra.LABELS[0][:8]
            self.assertEqual(res["sha256"], hashlib.sha256("".join(lab[i] for i in ids).encode()).hexdigest())

    def test_sharded(self):
        ids = ids_of(greedy_word(8))
        p = self.write("w8.spra.xz", encode(ids, 8, bits=6))
        res = check_any.check_any(p, threads=1, shards=3)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["shards"], 3)

    def test_word_with_a_missing_permutation_is_invalid(self):
        ids = ids_of(greedy_word(6))
        cut = ids[:300] + ids[301:]                       # one letter deleted: some permutation goes missing
        try:
            data = encode(cut, 6, bits=6)
        except ValueError:
            self.skipTest("the cut word is outside the SPRA limits")
        res = check_any.check_any(self.write("cut.spra.xz", data), threads=1)
        self.assertFalse(res["ok"])
        self.assertGreater(res["missing"], 0)

    def test_broken_file_is_refused_with_reason(self):
        data = encode(ids_of(greedy_word(6)), 6)
        res = check_any.check_any(self.write("bad.spra.xz", data + b"\x00"), threads=1)
        self.assertFalse(res["ok"])
        self.assertIn("extra bytes", res["error"])
        res = check_any.check_any(self.write("bad2.spra.xz", b"NOPE" + data[4:]), threads=1)
        self.assertFalse(res["ok"])
        self.assertIn("SPRA file refused", res["error"])

    def test_limit_never_ok(self):
        p = self.write("w7.spra.xz", encode(ids_of(greedy_word(7)), 7))
        res = check_any.check_any(p, threads=1, limit=500)
        self.assertFalse(res["ok"])
        self.assertEqual(res["letters_read"], 500)

    def test_bot_refuses_a_bomb_header(self):
        """a 24-byte SPRA file that claims a huge length (it would decode forever) is refused before any work."""
        cfg = bot.Config(records_path=os.path.join(self.tmp, "r.json"))
        for n, length in ((1, 2 ** 64 - 1), (5, 601), (13, 13 * 6227020800 + 1)):
            seed = bytes(range(min(length, n - 1)))
            head = b"SPRA" + bytes([2, 0x44, 0, n]) + length.to_bytes(8, "little") + (0).to_bytes(8, "little")
            p = self.write("bomb%d.spra.xz" % n, head + seed)
            with self.assertRaises(bot.UserError) as cm:
                bot.plan_job(p, cfg)
            self.assertTrue("n x n!" in str(cm.exception) or "above this bot" in str(cm.exception) or
                            "needs n >= 2" in str(cm.exception), str(cm.exception))
        ok = self.write("w5.spra.xz", encode(ids_of(greedy_word(5)), 5))
        self.assertEqual(bot.plan_job(ok, cfg).n, 5)                       # a real word of n = 5 still passes

    def test_bot_plan_and_extension(self):
        p = self.write("w8.spra.xz", encode(ids_of(greedy_word(8)), 8, bits=6))
        plan = bot.plan_job(p, bot.Config(records_path=os.path.join(self.tmp, "r.json")))
        self.assertEqual((plan.kind, plan.n, plan.lane, plan.shards), ("spra", 8, "fast", 1))
        self.assertEqual(bot.ext_of("x.SPRA.XZ"), ".spra.xz")
        with self.assertRaises(bot.UserError):
            bot.plan_job(self.write("bad.spra.xz", b"SPRA\x07" + b"\x00" * 30), bot.Config(records_path=os.path.join(self.tmp, "r.json")))

    @unittest.skipUnless(os.path.exists(W10), "n = 10 word not found: %s (set SUPERPERM_WORDS)" % W10)
    def test_n10_word_through_spra(self):
        with lzma.open(W10, "rb") as f:
            word = f.read().strip()
        alph = core.alphabet_of(W10)
        lab = "".join(sorted(alph))
        ids = [lab.index(chr(c)) for c in word]
        p = self.write("w10.spra.xz", encode(ids, 10, bits=6, zero_based=(lab[0] == "0")))
        res = check_any.check_any(p, threads=2)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["length"], 4034855)


@unittest.skipUnless(CORPUS and os.path.isdir(CORPUS), "set SUPERPERM_SPRA_CORPUS to a folder of .spra.xz words")
class TestCorpus(unittest.TestCase):
    def test_every_word_up_to_12(self):
        files = sorted(glob.glob(os.path.join(CORPUS, "**", "*.spra.xz"), recursive=True))
        self.assertTrue(files)
        done = 0
        for p in files:
            h = spra.header(p)
            if not 3 <= h["n"] <= 12:
                continue
            res = check_any.check_any(p, threads=2)
            self.assertTrue(res["ok"], (p, res))
            done += 1
        self.assertGreater(done, 0)


if __name__ == "__main__":
    unittest.main()

"""Offline tests of decode / check_any / records with the local tools (no network). The n = 14 full checks are
not here: tools/run_n14.py (15 to 25 minutes and 11 GB each).

    python -I tests/test_check.py

Tests that need data files skip themselves when the file is missing. Environment variables:
    SUPERPERM_WORDS         folder with superpermutation-10-4034855.txt.xz, ...-11-43930578.txt.xz (default testdata/words)
    SUPERPERM_TESTDATA      folder with n14_echols_93904936032.splan.json.xz (default testdata)
    SUPERPERM_N14_RECIPE    an n = 14 recipe of your own (default TESTDATA/n14_recipe.splan.json.xz)
    SUPERPERM_N14_LENGTH    the length that recipe states
    SUPERPERM_ECHOLS_SPC    Echols's .spc.xz file, for the importer test
    SUPERPERM_WORD8, SUPERPERM_WORD9   optional extra words of n = 8 and 9 for the shard tests
    SUPERPERM_SLOW=1        the n = 13 test (needs superpermutation-13-6747802393.txt.xz in SUPERPERM_WORDS)
"""
import copy
import hashlib
import io
import itertools
import json
import math
import lzma
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BOT = os.path.join(ROOT, "bot")
CHECKER = os.path.join(ROOT, "checker")
sys.path.insert(0, CHECKER)
sys.path.insert(0, BOT)

import check_any  # noqa: E402
import core  # noqa: E402
import decode  # noqa: E402
import records  # noqa: E402
import registry  # noqa: E402

TESTDATA = os.environ.get("SUPERPERM_TESTDATA") or os.path.join(ROOT, "testdata")
WORDS = os.environ.get("SUPERPERM_WORDS") or os.path.join(TESTDATA, "words")
OURS = os.environ.get("SUPERPERM_N14_RECIPE") or os.path.join(TESTDATA, "n14_recipe.splan.json.xz")
OURS_LENGTH = int(os.environ.get("SUPERPERM_N14_LENGTH") or 0)
ECHOLS = os.path.join(TESTDATA, "n14_echols_93904936032.splan.json.xz")
ECHOLS_SPC = os.environ.get("SUPERPERM_ECHOLS_SPC") or ""
W10 = os.path.join(WORDS, "superpermutation-10-4034855.txt.xz")
W11 = os.path.join(WORDS, "superpermutation-11-43930578.txt.xz")


def need(path, what="data file"):
    """skip a test or class when a data file is missing (never fail)."""
    return unittest.skipUnless(bool(path) and os.path.exists(path), "%s not found: %s" % (what, path or "(env variable not set)"))


need_w10 = need(W10, "n = 10 word (set SUPERPERM_WORDS)")
need_w11 = need(W11, "n = 11 word (set SUPERPERM_WORDS)")
need_echols = need(ECHOLS, "Echols recipe (set SUPERPERM_TESTDATA)")
need_ours = unittest.skipUnless(os.path.exists(OURS) and OURS_LENGTH > 0,
                                "n = 14 recipe not found or length unknown (set SUPERPERM_N14_RECIPE and SUPERPERM_N14_LENGTH)")


def sha(b):
    return hashlib.sha256(b).hexdigest()


class TestRegistry(unittest.TestCase):
    def test_sources_match_accepted_hashes(self):
        for p in registry.SOURCES:
            registry.check_source(p)
            registry.binary(p)


class TestWords(unittest.TestCase):
    @need_w10
    def test_n10(self):
        r = check_any.check_any(W10, threads=1)
        self.assertTrue(r["ok"])
        self.assertEqual((r["n"], r["length"], r["kind"]), (10, 4034855, "word"))
        self.assertEqual(r["sha256"], "542a3778662f972a4ea7b9461a9334775f31303a0d8b1a7913169d8beea444ff")

    @need_w11
    def test_n11(self):
        r = check_any.check_any(W11, threads=2)
        self.assertTrue(r["ok"])
        self.assertEqual((r["n"], r["length"]), (11, 43930578))
        self.assertEqual(r["sha256"], "f7d3122f350d56d41945961f9abf36453420bd3068a3941b424f9c0447c7aef5")

    @need_w10
    def test_bad_words(self):
        d = tempfile.mkdtemp(prefix="bad-")
        try:
            w = lzma.open(W10, "rb").read().rstrip(b"\n")
            cases = {"truncated": w[:-100], "foreign": w[:5000] + b"Z" + w[5001:], "swapped": w[:777] + w[778:779] + w[777:778] + w[779:],
                     "empty": b"", "one_symbol": b"0000"}
            for name, data in cases.items():
                p = os.path.join(d, name + ".txt")
                with open(p, "wb") as f:
                    f.write(data)
                r = check_any.check_any(p, threads=1)
                self.assertFalse(r["ok"], name)
            r = check_any.check_any(os.path.join(d, "truncated.txt"), threads=1)
            self.assertEqual(r["missing"] > 0, True)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @need_w10
    def test_trailing_newline_not_in_hash(self):
        d = tempfile.mkdtemp(prefix="nl-")
        try:
            w = lzma.open(W10, "rb").read().rstrip(b"\n")
            a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.txt")
            open(a, "wb").write(w)
            open(b, "wb").write(w + b"\n\n")
            ra, rb = check_any.check_any(a, 1), check_any.check_any(b, 1)
            self.assertTrue(ra["ok"] and rb["ok"])
            self.assertEqual(ra["sha256"], rb["sha256"])
            self.assertEqual(ra["sha256"], sha(w))
        finally:
            shutil.rmtree(d, ignore_errors=True)


@need_echols
class TestRecipeValidation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="rcp-")
        self.rec = json.loads(lzma.open(ECHOLS, "rb").read())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, rec, name="x.splan.json"):
        p = os.path.join(self.d, name)
        open(p, "w").write(json.dumps(rec))
        return p

    def refused(self, rec, text):
        r = check_any.check_any(self.write(rec), threads=1, limit=1000)
        self.assertFalse(r["ok"])
        self.assertIn("recipe refused", r["error"])
        self.assertIn(text, r["error"])

    def test_good_header_loads(self):
        decode.load(ECHOLS)
        if os.path.exists(OURS):
            decode.load(OURS)

    def test_tampered(self):
        r = copy.deepcopy(self.rec)
        r["seed"]["text"] = r["seed"]["text"].replace("7890123456 S", "7890123456 F", 1)
        self.refused(r, "SHA-256 does not match")
        r = copy.deepcopy(self.rec)
        r["generator"]["id"] = "run-my-script"
        self.refused(r, "unknown generator")
        r = copy.deepcopy(self.rec)
        r["generator"]["source_sha256"] = "0" * 64
        self.refused(r, "source_sha256")
        r = copy.deepcopy(self.rec)
        r["generator"]["version"] = "2"
        self.refused(r, "version")
        r = copy.deepcopy(self.rec)
        r["alphabet"] = "123456789ABCD"
        self.refused(r, "alphabet")
        r = copy.deepcopy(self.rec)
        r["alphabet"] = "123456789ABCD "
        self.refused(r, "alphabet")
        r = copy.deepcopy(self.rec)
        r["n"] = 13
        r["alphabet"] = "123456789ABCD"
        self.refused(r, "n = 14")
        r = copy.deepcopy(self.rec)
        r["word_sha256"] = "XYZ"
        self.refused(r, "word_sha256")
        r = copy.deepcopy(self.rec)
        r["format"] = "other/1"
        self.refused(r, "format")
        r = copy.deepcopy(self.rec)
        del r["seed"]
        self.refused(r, "seed missing")
        r = copy.deepcopy(self.rec)
        r["plan"] = {"text": "x", "sha256": sha(b"x")}
        self.refused(r, "takes no plan")

    def test_file_refs_only_in_directory_and_inside_it(self):
        r = copy.deepcopy(self.rec)
        r["seed"] = {"file": "seed.txt", "sha256": "0" * 64}
        self.refused(r, "only allowed in a recipe directory")
        # directory form
        sd = os.path.join(self.d, "dir")
        os.mkdir(sd)
        seed = self.rec["seed"]["text"].encode()
        open(os.path.join(sd, "seed.txt"), "wb").write(seed)
        open(os.path.join(self.d, "secret.txt"), "wb").write(seed)    # outside the directory
        for name, ok in (("seed.txt", True), ("../secret.txt", False), ("..\\secret.txt", False),
                         (os.path.join(self.d, "secret.txt"), False), ("C:/x", False), ("nothere.txt", False)):
            r = copy.deepcopy(self.rec)
            r["seed"] = {"file": name, "sha256": sha(seed)}
            open(os.path.join(sd, "recipe.json"), "w").write(json.dumps(r))
            res = check_any.check_any(sd, threads=1, limit=2000, shards=8)   # 8 shards: the n = 14 table is 1.3 GB, not 10.9 GB
            if ok:
                self.assertEqual(res.get("letters_read"), 2000, res)
            else:
                self.assertIn("recipe refused", res["error"], name)

    def test_xz_bomb_and_junk(self):
        p = os.path.join(self.d, "bomb.splan.json.xz")
        with lzma.open(p, "wb") as f:
            for _ in range(70):
                f.write(b" " * (1 << 20))
        r = check_any.check_any(p, threads=1)
        self.assertIn("larger than", r["error"])
        p = os.path.join(self.d, "junk.splan.json.xz")
        open(p, "wb").write(b"not xz")
        self.assertFalse(check_any.check_any(p, threads=1)["ok"])
        p = os.path.join(self.d, "junk2.splan.json")
        open(p, "wb").write(b"{")
        self.assertIn("not JSON", check_any.check_any(p, threads=1)["error"])


class TestRecipeStreams(unittest.TestCase):
    def prefix(self, path, limit):
        r, st = decode.open_stream(path, limit=limit)
        data = b""
        while True:
            b = st.read(1 << 20)
            if not b:
                break
            data += b
        rc, err = st.close(kill=True)
        return r, data, err

    @need_echols
    def test_echols_import_prefix_has_his_alphabet(self):
        r, data, err = self.prefix(ECHOLS, 200000)
        self.assertEqual(len(data), 200000)
        self.assertEqual(set(data), set(b"123456789ABCDE"))
        self.assertTrue(data.startswith(b"124356798ABCDE124356798ABCD1E24356798ABC"))

    @need_ours
    def test_our_prefix_and_limit_never_ok(self):
        r, data, err = self.prefix(OURS, 50000)
        self.assertEqual(len(data), 50000)
        self.assertEqual(set(data), set(b"0123456789ABCD"))
        res = check_any.check_any(OURS, threads=1, limit=3000000, shards=8)
        self.assertFalse(res["ok"])
        self.assertEqual(res["letters_read"], 3000000)
        self.assertFalse(res["length_matches"])
        self.assertEqual(res["recipe_length"], OURS_LENGTH)

    @need(ECHOLS_SPC, "Echols .spc.xz (set SUPERPERM_ECHOLS_SPC)")
    def test_importer_rejects_bad_seed(self):
        d = tempfile.mkdtemp(prefix="imp-")
        try:
            e = json.loads(lzma.open(ECHOLS_SPC, "rb").read())
            e["seed"] = e["seed"] + "x"
            p = os.path.join(d, "e.spc")
            open(p, "w").write(json.dumps(e))
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import make_recipe
            with self.assertRaises(SystemExit):
                make_recipe.import_echols(p, os.path.join(d, "o.splan.json.xz"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestRecords(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="rec-")
        self.p = os.path.join(self.d, "r.json")
        shutil.copyfile(os.path.join(BOT, "records.json"), self.p)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def res(self, n, length, sha_, ok=True):
        return {"ok": ok, "n": n, "length": length, "sha256": sha_, "kind": "word"}

    def test_seed_table(self):
        t = records.load(self.p)
        self.assertEqual(sorted(t["records"], key=int), [str(i) for i in range(7, 15)])
        for n, length in ((8, 46174), (10, 4034855), (11, 43930578), (12, 522736969), (13, 6747802393)):
            b = records.best_verified(t, n)
            self.assertEqual(b["length"], length)
            self.assertTrue(b["verified"] and len(b["sha256"]) == 64)
        self.assertIsNone(records.best_verified(t, 9))
        self.assertEqual(records.best_verified(t, 14)["submitter"], "William Echols")
        # a shorter unverified claim is the best known entry but does not displace the verified record
        for n, claim in ((8, 46173), (14, 93904804425)):
            k = records.best_known(t, n)
            self.assertEqual((k["length"], k["verified"], k["sha256"]), (claim, False, None))

    def test_flow(self):
        r = records.submit(self.p, self.res(9, 408000, "a" * 64), "x", "src")
        self.assertEqual(r["status"], "new_record")
        self.assertEqual(r["claim"], None)
        self.assertEqual(records.submit(self.p, self.res(9, 408000, "a" * 64), "y", "src")["status"], "duplicate")
        self.assertEqual(records.submit(self.p, self.res(9, 408000, "b" * 64), "y", "src")["status"], "equal")
        r = records.submit(self.p, self.res(9, 408900, "c" * 64), "y", "src")
        self.assertEqual(r["status"], "longer")
        self.assertEqual(records.submit(self.p, self.res(9, 1, "d" * 64, ok=False), "y", "src")["status"], "invalid")
        # a verified word longer than an unverified claim is reported with the claim
        r = records.submit(self.p, self.res(7, 5910, "e" * 64), "z", "src")
        self.assertEqual(r["status"], "new_record")
        self.assertEqual(r["claim"]["length"], 5905)
        # ... also when it does not beat the verified record
        r = records.submit(self.p, self.res(8, 46200, "9" * 64), "z", "src")
        self.assertEqual(r["status"], "longer")
        self.assertEqual(r["claim"]["length"], 46173)
        # a verified entry for a length that was only claimed turns the claim into a verified one
        r = records.submit(self.p, self.res(7, 5905, "f" * 64), "w", "src")
        self.assertEqual(r["status"], "new_record")
        self.assertTrue(records.best_verified(records.load(self.p), 7)["sha256"] == "f" * 64)


# ----------------------------------------------------------------------------------------------- sharding

W8_EXT = os.environ.get("SUPERPERM_WORD8") or ""
W9_EXT = os.environ.get("SUPERPERM_WORD9") or ""
W13 = os.path.join(WORDS, "superpermutation-13-6747802393.txt.xz")
ALPH = "0123456789ABCD"


def rank_of(perm):
    """Lehmer rank of a sequence of distinct indices 0..n-1 (the same formula as the C checkers)."""
    n, left, r = len(perm), list(range(len(perm))), 0
    for j, x in enumerate(perm[:-1]):
        r += sum(1 for y in left if y < x) * math.factorial(n - 1 - j)
        left.remove(x)
    return r


def perm_of(rank, n):
    left, out = list(range(n)), []
    for j in range(n):
        f = math.factorial(n - 1 - j)
        out.append(left.pop(rank // f))
        rank %= f
    return out


def shard_range(n, i, k):
    f = math.factorial(n)
    return i * f // k, (i + 1) * f // k


def greedy_word(n):
    """a valid word by the greedy rule: always append the unvisited permutation with the longest overlap (n <= 8)."""
    perms = list(itertools.permutations(range(n)))
    index = [dict() for _ in range(n)]          # index[l][prefix of length l] -> permutations with that prefix
    for p in perms:
        for l in range(1, n):
            index[l].setdefault(p[:l], []).append(p)
    seen = {perms[0]}
    word = list(perms[0])
    while len(seen) < len(perms):
        tail = tuple(word[-(n - 1):])
        for ov in range(n - 1, 0, -1):
            cands = [p for p in index[ov].get(tail[len(tail) - ov:], ()) if p not in seen]
            if cands:
                p = cands[0]
                break
        else:
            p = next(q for q in perms if q not in seen)    # no overlap: append whole (never happens for n >= 3)
            ov = 0
        seen.add(p)
        word.extend(p[ov:])
    return "".join(ALPH[x] for x in word).encode()


def seen_ranks(data, alph):
    """ground truth by brute force: the ranks of all windows that are permutations (symbol order = alph)."""
    n = len(alph)
    idx = {ord(c): i for i, c in enumerate(alph)}
    v = [idx.get(b, -1) for b in data]
    out = set()
    for i in range(len(v) - n + 1):
        w = v[i:i + n]
        if len(set(w)) == n and min(w) >= 0:
            out.add(rank_of(w))
    return out


def run_shards(data, alph, k, threads=1):
    """k passes of the real checker over the bytes, combined as check_any does -> (combined dict, pass dicts)."""
    passes = [core.check_stream(io.BytesIO(data).read, alph, None, threads, None, shard=(i, k) if k > 1 else None)
              for i in range(k)]
    return (check_any.combine(passes) if k > 1 else passes[0]), passes


class TestShards(unittest.TestCase):
    def valid_words(self):
        """(label, n, bytes) for n = 6..11."""
        out = [("greedy6", 6, greedy_word(6)), ("greedy7", 7, greedy_word(7)), ("greedy8", 8, greedy_word(8))]
        for n, path in ((8, W8_EXT), (9, W9_EXT)):
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    out.append(("file%d" % n, n, f.read().rstrip(b"\r\n")))
        if os.path.exists(W10):
            out.append(("w10", 10, lzma.open(W10, "rb").read().rstrip(b"\n")))
        if os.path.exists(W11):
            out.append(("w11", 11, lzma.open(W11, "rb").read().rstrip(b"\n")))
        return out

    def test_valid_words_every_k_same_length_and_sha(self):
        for label, n, data in self.valid_words():
            alph = "".join(dict.fromkeys(data.decode("latin-1")))
            want = sha(data)
            for k in range(1, 8):
                res, passes = run_shards(data, alph, k)
                self.assertTrue(res["valid"], (label, k, res))
                self.assertEqual({p["length"] for p in passes}, {len(data)}, (label, k))
                self.assertEqual({p["sha256"] for p in passes}, {want}, (label, k))
                self.assertEqual(res["missing"], 0)
                self.assertTrue(all(p["checker_rc"] == 0 for p in passes))
                if k > 1:
                    lo_hi = [(p["lo"], p["hi"]) for p in passes]
                    self.assertEqual(lo_hi, [shard_range(n, i, k) for i in range(k)])
                    self.assertEqual(sum(p["distinct_seen"] for p in passes), math.factorial(n))

    def corrupt(self, data, how):
        mid = len(data) // 2
        if how == "deleted":
            return data[:mid] + data[mid + 1:]
        if how == "changed":
            c = data[mid:mid + 1]
            return data[:mid] + (b"1" if c != b"1" else b"2") + data[mid + 1:]
        if how == "truncated":
            return data[:-max(10, len(data) // 50)]
        raise ValueError(how)

    def test_corrupted_words_every_k_invalid_and_missing_by_shard(self):
        for label, n, data in self.valid_words():
            if n > 9:
                continue
            alph = "".join(dict.fromkeys(data.decode("latin-1")))
            full = set(range(math.factorial(n)))
            brute = len(data) < 100000      # the brute-force ground truth is slow in Python
            for how in ("deleted", "changed", "truncated"):
                bad = self.corrupt(data, how)
                missing = full - seen_ranks(bad, alph) if brute else None
                single = None
                for k in range(1, 8):
                    res, passes = run_shards(bad, alph, k)
                    self.assertFalse(res["valid"], (label, how, k))
                    if single is None:
                        single = res["missing"]
                    self.assertEqual(res["missing"], single, (label, how, k))
                    if missing is not None:
                        self.assertGreater(len(missing), 0, (label, how))
                        for i, p in enumerate(passes):
                            lo, hi = shard_range(n, i, k)
                            want = sum(1 for r in missing if lo <= r < hi)
                            self.assertEqual(p["missing"], want, (label, how, k, i))
                            self.assertEqual(p["valid"], want == 0 and p["foreign_bytes"] == 0, (label, how, k, i))
                        self.assertEqual(res["missing"], len(missing))
                    # the passes still agree on length and hash: the word is just not a superpermutation
                    self.assertEqual(len({p["sha256"] for p in passes}), 1)
                    self.assertEqual(res["sha256"], sha(bad))

    @need_w10
    @need_w11
    def test_corrupted_real_words_n10_n11(self):
        for path, hows, ks in ((W10, ("deleted", "truncated"), (1, 2, 7)), (W11, ("deleted",), (1, 3))):
            with lzma.open(path, "rb") as f:
                data = f.read().rstrip(b"\n")
            alph = core.alphabet_of(path)
            for how in hows:
                bad = self.corrupt(data, how)
                for k in ks:
                    res, passes = run_shards(bad, alph, k)
                    self.assertFalse(res["valid"], (path, how, k))
                    if k == 1:
                        single = res["missing"]
                    self.assertEqual(res["missing"], single, (path, how, k))
                    self.assertTrue(any(not p["valid"] for p in passes))

    def test_boundary_ranks_each_shard(self):
        """the first and the last rank of every shard are counted by that shard and by no other."""
        for n, ks in ((3, (1, 2, 5, 6)), (4, (1, 5, 7, 24)), (5, (1, 2, 3, 4, 5, 6, 7)), (6, (1, 2, 3, 4, 5, 6, 7)),
                      (7, (1, 3, 7))):
            a = ALPH[:n]
            f = math.factorial(n)
            for k in ks:
                ranks = sorted({r for i in range(k) for r in (shard_range(n, i, k)[0], shard_range(n, i, k)[1] - 1)}
                               | {0, f - 1})
                # each permutation alone, with a foreign-looking neighbour pattern that cannot form a window
                word = "".join("".join(a[x] for x in perm_of(r, n)) for r in ranks).encode()
                truth = seen_ranks(word, a)
                self.assertTrue(set(ranks) <= truth)
                for i in range(k):
                    lo, hi = shard_range(n, i, k)
                    r = core.check_stream(io.BytesIO(word).read, a, None, 1, None, shard=(i, k))
                    self.assertEqual((r["lo"], r["hi"]), (lo, hi))
                    self.assertEqual(r["distinct_seen"], sum(1 for x in truth if lo <= x < hi), (n, k, i))
                    self.assertEqual(r["missing"], (hi - lo) - r["distinct_seen"], (n, k, i))
                    self.assertEqual(r["valid"], r["missing"] == 0 and r["foreign_bytes"] == 0)

    def test_shards_cover_everything_once_with_both_checkers(self):
        """k = n! gives one permutation per shard; a word with every permutation is valid in all of them, in both
        checkers (verify_stream with 1 and 3 threads, vfast single and two-thread mode)."""
        n = 4
        word = b"123412314231243121342132413214321"
        k = math.factorial(n)
        res, passes = run_shards(word, "1234", k)
        self.assertTrue(res["valid"])
        self.assertEqual([p["distinct_seen"] for p in passes], [1] * k)
        for prog, extra in (("verify_stream", ["2"]), ("verify_stream", ["1"]), ("vfast", []), ("vfast", ["1"])):
            exe = registry.binary(prog)
            import subprocess
            tot = 0
            for i in range(0, 24, 5):
                out = subprocess.run([exe, "4", "1234"] + extra + ["--shard", "%d/24" % i], input=word,
                                     capture_output=True, check=False)
                j = json.loads(out.stdout.decode().strip().splitlines()[-1])
                self.assertEqual(out.returncode, 0, (prog, extra, i))
                self.assertEqual((j["distinct_seen"], j["missing"], j["shard"]), (1, 0, "%d/24" % i))
                tot += j["distinct_seen"]
            self.assertEqual(tot, 5)

    def test_unsharded_output_unchanged_and_bad_shard_args(self):
        import subprocess
        for prog, extra in (("verify_stream", ["1"]), ("vfast", ["1"])):
            exe = registry.binary(prog)
            out = subprocess.run([exe, "3", "123"] + extra, input=b"123121321\n", capture_output=True)
            j = json.loads(out.stdout.decode())
            self.assertNotIn("shard", j)
            self.assertEqual((j["missing"], j["distinct_seen"], out.returncode), (0, 6, 0))
            for bad in ("3/3", "0/0", "x", "1/", "0/7", "-1/2", "0/100001"):
                r = subprocess.run([exe, "3", "123"] + extra + ["--shard", bad], input=b"123121321", capture_output=True)
                self.assertEqual(r.returncode, 2, (prog, bad))
            r = subprocess.run([exe, "3", "123"] + extra + ["--shard"], input=b"123121321", capture_output=True)
            self.assertEqual(r.returncode, 2)

    def test_combine_rejects_mismatch_between_passes(self):
        base = {"valid": True, "length": 100, "sha256": "a" * 64, "missing": 0, "foreign_bytes": 0, "distinct_seen": 3,
                "first_occurrences": 3, "repeats": 0, "n": 4}
        good = check_any.combine([dict(base), dict(base)])
        self.assertTrue(good["valid"])
        self.assertEqual(good["shards"], 2)
        for key, val in (("sha256", "b" * 64), ("length", 101)):
            bad = check_any.combine([dict(base), dict(base, **{key: val}), dict(base)])
            self.assertFalse(bad["valid"], key)
            self.assertTrue(bad["pass_mismatch"])
            self.assertIn("different", bad["error"])
        one_bad = check_any.combine([dict(base), dict(base, valid=False, missing=2), dict(base)])
        self.assertFalse(one_bad["valid"])
        self.assertEqual(one_bad["missing"], 2)
        self.assertEqual(one_bad["shard_valid"], [True, False, True])

    @need_w10
    def test_check_any_sharded_files(self):
        """the whole path (file, passes, combine, ok) for a word and for a corrupted word; k = 1 equals k = 3."""
        d = tempfile.mkdtemp(prefix="shard-")
        try:
            data = lzma.open(W10, "rb").read().rstrip(b"\n")
            good, bad = os.path.join(d, "good.txt"), os.path.join(d, "bad.txt")
            with open(good, "wb") as fh:
                fh.write(data)
            with open(bad, "wb") as fh:
                fh.write(data[:2000] + data[2001:])
            a = check_any.check_any(good, threads=1, shards=1)
            b = check_any.check_any(good, threads=1, shards=3)
            for key in ("ok", "valid", "n", "length", "sha256", "missing"):
                self.assertEqual(a[key], b[key], key)
            self.assertEqual(b["shards"], 3)
            c = check_any.check_any(bad, threads=1, shards=3)
            self.assertFalse(c["ok"])
            self.assertGreater(c["missing"], 0)
            self.assertEqual(check_any.check_any(bad, threads=1, shards=1)["missing"], c["missing"])
            seen = []
            check_any.check_any(good, threads=1, shards=2, progress=lambda i, k, n: seen.append((i, k, n)))
            self.assertEqual([x[0] for x in seen if x[2] == 0], [1, 2])
            self.assertEqual(max(x[2] for x in seen), len(data))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @need_w10
    def test_check_any_sharded_mismatch_between_passes(self):
        """a pass that sees a different word (here: the second pass is fed a modified copy) makes the word invalid."""
        d = tempfile.mkdtemp(prefix="shard-")
        calls = []
        real = check_any._pass
        data = lzma.open(W10, "rb").read().rstrip(b"\n")
        path = os.path.join(d, "w.txt")
        with open(path, "wb") as fh:
            fh.write(data)

        def fake(path_, threads, limit, mem_gb, shard, progress):
            calls.append(shard)
            r = real(path_, threads, limit, mem_gb, shard, progress)
            if shard and shard[0] == 1:
                r = dict(r, sha256="0" * 64)
            return r
        check_any._pass = fake
        try:
            res = check_any.check_any(path, threads=1, shards=3)
        finally:
            check_any._pass = real
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(calls, [(0, 3), (1, 3), (2, 3)])
        self.assertFalse(res["ok"])
        self.assertTrue(res["pass_mismatch"])
        self.assertIn("different SHA-256", res["error"])

    @need_echols
    def test_check_any_sharded_recipe_prefix(self):
        """a recipe is expanded once per pass; with --limit the stream is cut, so the passes agree and none is ok."""
        res = check_any.check_any(ECHOLS, threads=1, limit=300000, shards=2)
        self.assertEqual(res["shards"], 2)
        self.assertFalse(res["ok"])
        self.assertEqual(res["letters_read"], 300000)
        self.assertNotIn("pass_mismatch", res)    # the generator is deterministic: same prefix, same hash twice


class TestPlanner(unittest.TestCase):
    def test_shards_for_memory(self):
        f = check_any.plan_shards
        self.assertEqual(f(14, 14.0, 8, recipe=True), 1)
        self.assertEqual(f(6, 0.01 + 1.0, 8), 1)
        k = f(14, 8.0, 8, recipe=True)
        self.assertGreater(k, 1)
        self.assertLessEqual(check_any.ram_gb(14, k, True), 8.0)
        self.assertGreater(check_any.ram_gb(14, k - 1, True), 8.0)
        self.assertEqual(f(14, 3.9, 8, recipe=False), 4)
        with self.assertRaises(ValueError) as c:
            f(14, 2.0, 8, recipe=True)
        self.assertIn("SUPERPERM_MAX_SHARDS", str(c.exception))
        self.assertEqual(f(14, 2.9, 64, recipe=False), 6)
        # more shards allowed -> a smaller limit works
        self.assertLessEqual(check_any.ram_gb(14, f(14, 3.0, 64, True), True), 3.0)

    def test_estimates(self):
        self.assertAlmostEqual(check_any.estimate_letters(14) / 9.39e10, 1.0, places=2)
        one, tot = check_any.estimate_seconds(93.9e9, 4, 30.0)
        self.assertAlmostEqual(one, 93.9e9 / 30e6, delta=5)
        self.assertAlmostEqual(tot, 4 * one)


@unittest.skipUnless(os.environ.get("SUPERPERM_SLOW") == "1" and os.path.exists(W13),
                     "slow: set SUPERPERM_SLOW=1 (n = 13, about 15 minutes, 1 GB)")
class TestSlowN13(unittest.TestCase):
    def test_n13_k2_equals_k1(self):
        r1 = check_any.check_any(W13, threads=1, shards=1)
        r2 = check_any.check_any(W13, threads=1, shards=2)
        for key in ("ok", "valid", "n", "length", "sha256", "missing"):
            self.assertEqual(r1[key], r2[key], key)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(r2["shards"], 2)


FUZZ = os.path.join(HERE, "data", "fuzz")


class TestFuzzRegressions(unittest.TestCase):
    """cases found by fuzzing on 2026-10-21: each must be a clean refusal, never a crash or a traceback."""

    def setUp(self):
        # a missing file is refused too, so without this check the tests would pass on nothing
        for name in ("min.sel", "unhashable_id.splan.json", "surrogate_seed.splan.json"):
            self.assertTrue(os.path.isfile(os.path.join(FUZZ, name)), name + " missing from tests/data/fuzz")

    def test_gen14_refuses_a_row_outside_0_9(self):
        import subprocess
        p = subprocess.run([registry.binary("gen14"), "echols", os.path.join(FUZZ, "min.sel"), "--alpha",
                            "123456789ABCDE", "--nosha", "--limit", "1000"], capture_output=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"outside 0-9", p.stderr + p.stdout)
        self.assertEqual(p.stdout, b"")                       # refused before writing any letter

    def test_recipe_with_a_list_as_generator_id(self):
        r = check_any.check_any(os.path.join(FUZZ, "unhashable_id.splan.json"), threads=1)
        self.assertFalse(r["ok"])
        self.assertIn("recipe refused", r["error"])

    def test_recipe_with_a_lone_surrogate_in_the_seed(self):
        r = check_any.check_any(os.path.join(FUZZ, "surrogate_seed.splan.json"), threads=1)
        self.assertFalse(r["ok"])
        self.assertIn("recipe refused", r["error"])


@unittest.skipUnless(os.name == "nt", "guard.py is the Windows memory limit")
class TestGuardOutput(unittest.TestCase):
    """24.10.2026: guard.py printed its summary on stdout, which joined the word stream of a generator it wrapped
    (an n = 14 recipe read 74 bytes too many). The summary must go to stderr and stdout must be the command's alone."""

    def test_stdout_is_only_the_command(self):
        import subprocess
        cmd = [sys.executable, "-I", os.path.join(CHECKER, "guard.py"), "--gb", "1", "--",
               sys.executable, "-I", "-c", "import sys; sys.stdout.write('0123')"]
        r = subprocess.run(cmd, capture_output=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b"0123")
        self.assertIn(b"[guard] exit 0", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

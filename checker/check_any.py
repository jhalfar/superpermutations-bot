"""check_any.py - check a plain word (.txt, .txt.xz), a recipe (splan/1) or a SPRA word (.spra, .spra.xz) and print one
JSON line.

    python -I checker/check_any.py PATH [--threads N] [--limit L] [--mem-gb G] [--shards K] [--progress]

Result keys: kind ("word" | "recipe" | "spra"), n, alphabet, length, sha256 (of the letters), valid (every permutation occurs,
nothing else is in the stream), and for a recipe: recipe_length / length_matches, recipe_word_sha256 / sha_matches
(null if the recipe has no hash), generator, gen_rc; for SPRA: spra_length / length_matches, spra_version, spra_bits,
decoder_rc. `ok` is the verdict: valid and no mismatch with the footer.
Exit code 0 iff ok. --limit makes the generator stop after L letters (a test of the plumbing; never ok).

--shards K (default 1) trades time for memory: K passes over the word (a recipe is expanded K times, the generators
are deterministic), pass i tracks only the permutations with rank in [floor(i*n!/K), floor((i+1)*n!/K)) (memory about
n!/(8K)). The word is valid iff every pass is ok AND all passes saw the same length and the same SHA-256; extra keys:
shards, shard_missing (per pass), shard_valid, shard_seconds. K = 1 is the plain single pass.

--progress prints lines `#P {"pass": i, "passes": K, "letters": L}` (at most one per second, one at the start of
every pass) before the final JSON line; the Discord bot reads them. The JSON line is the last line starting with `{`.
"""
import json
import os
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder
import core
import decode
import registry
import spra

GB = 2 ** 30
OVERHEAD_GB = 1.0          # checker buffers (0.6 to 0.85 GB), the Python process, margin
GENERATOR_GB = 0.6         # a recipe generator (n = 14: about 0.6 GB, README)
DEFAULT_MBPS = 30.0        # million letters per second of one pass: the n = 14 checks took 3430 s for 94 G (loaded desktop)
MAX_SHARDS_HARD = 64


def factorial(n):
    f = 1
    for i in range(2, n + 1):
        f *= i
    return f


def ram_gb(n, shards=1, recipe=False):
    """memory of one pass, in GB: the bit table of its range (n!/(8 shards) bytes) plus the generator (recipes) and the
    buffers of the checker. Used to choose the number of shards and to refuse a job."""
    return factorial(n) / 8 / shards / GB + OVERHEAD_GB + (GENERATOR_GB if recipe else 0.0)


def plan_shards(n, mem_gb, max_shards=8, recipe=False):
    """the smallest k with ram_gb(n, k, recipe) <= mem_gb; ValueError (with a plain explanation) if k > max_shards."""
    k = 1
    while ram_gb(n, k, recipe) > mem_gb:
        k += 1
        if k > max_shards or k > MAX_SHARDS_HARD:
            raise ValueError(
                "n = %d needs more than %d passes to fit into %.1f GB (even %d shards need about %.1f GB; the limits "
                "are SUPERPERM_MEM_GB and SUPERPERM_MAX_SHARDS)"
                % (n, max_shards, mem_gb, max_shards, ram_gb(n, max_shards, recipe)))
    return k


def estimate_letters(n):
    """length of a word of n symbols when it is not known (a .txt.xz): the length of the best known words, n! + (n-1)! +
    (n-2)! + (n-3)! + n - 3 (used only for the time estimate)."""
    if n < 4:
        return factorial(n) * n
    return factorial(n) + factorial(n - 1) + factorial(n - 2) + factorial(n - 3) + n - 3


def estimate_seconds(letters, shards=1, mbps=DEFAULT_MBPS):
    """(seconds of one pass, seconds of all passes) at mbps million letters per second."""
    one = letters / (mbps * 1e6)
    return one, one * shards


def is_recipe(path):
    b = os.path.basename(path.rstrip("/\\"))
    return os.path.isdir(path) or b.endswith(".splan.json.xz") or b.endswith(".splan.json")


def count_letters(path):
    """letters of a plain (uncompressed) word file: its bytes without white space (the checker drops white space too)."""
    total = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(core.CHUNK)
            if not b:
                return total
            total += len(b.translate(None, core.WHITE))


def peek(path):
    """-> (kind, n) without running anything big: n from the recipe header, or from the first symbols of the word."""
    return peek_full(path)[:2]


def peek_full(path):
    """-> (kind, n, length or None). A recipe and a SPRA word state their length; a plain .txt is counted in letters
    (white space is not a letter, so a trailing newline does not count); a .xz word does not say (None)."""
    if is_recipe(path):
        r, _ = decode.load(path)
        return "recipe", r["n"], r["length"]
    if spra.is_spra(path):
        h = spra.header(path)
        return "spra", h["n"], h["length"]
    n = len(core.alphabet_of(path))
    return "word", n, (None if path.endswith(".xz") else count_letters(path))


def _pass(path, threads, limit, mem_gb, shard, progress):
    if is_recipe(path):
        return check_recipe(path, threads, limit, mem_gb, shard, progress)
    if spra.is_spra(path):
        return check_spra(path, threads, limit, mem_gb, shard, progress)
    return core.check(path, None, threads, mem_gb, shard, progress)


def check_recipe(path, threads=1, limit=None, mem_gb=None, shard=None, progress=None):
    t0 = time.time()
    r, st = decode.open_stream(path, limit=limit, mem_gb=mem_gb)
    try:
        res = core.check_stream(st.read, r["alphabet"], None, threads, mem_gb, shard=shard, progress=progress)
    finally:
        rc, err = st.close(kill=True)
    res.update({"kind": "recipe", "file": os.path.basename(path.rstrip("/\\")), "gen_rc": rc,
                "generator": r["generator"]["id"], "recipe_length": r["length"],
                "recipe_word_sha256": r.get("word_sha256")})
    if rc != 0 and limit is None:
        res["valid"] = False
        res["error"] = "generator ended with code %d: %s" % (rc, err.strip().splitlines()[-1] if err.strip() else "")
    res["length_matches"] = res.get("length") == r["length"]
    res["sha_matches"] = None if r.get("word_sha256") is None else res.get("sha256") == r["word_sha256"]
    res["seconds"] = round(time.time() - t0, 1)
    return res


def check_spra(path, threads=1, limit=None, mem_gb=None, shard=None, progress=None):
    t0 = time.time()
    h = spra.header(path)
    st = spra.Stream(path, limit=limit)
    try:
        res = core.check_stream(st.read, h["alphabet"], None, threads, mem_gb, shard=shard, progress=progress)
    finally:
        rc, err = st.close(kill=True)
    res.update({"kind": "spra", "file": os.path.basename(path), "decoder_rc": rc, "spra_length": h["length"],
                "spra_version": h["version"], "spra_bits": h["bits"]})
    if rc != 0 and limit is None:
        res["valid"] = False
        res["error"] = "SPRA decoder refused the file: %s" % (err or "exit code %d" % rc)
    res["length_matches"] = res.get("length") == h["length"]
    res["seconds"] = round(time.time() - t0, 1)
    return res


def combine(passes):
    """the verdict of k passes: valid iff every pass is valid and all passes saw the same length and SHA-256."""
    first = dict(passes[0])
    k = len(passes)
    problems = []
    for key, what in (("length", "length"), ("sha256", "SHA-256")):
        vals = [p.get(key) for p in passes]
        if len(set(vals)) != 1:
            problems.append("the passes saw different %s (%s)" % (what, ", ".join(str(v) for v in vals)))
    valid = all(p.get("valid") for p in passes) and not problems
    for key in ("first_occurrences", "repeats", "distinct_seen", "missing"):
        if all(key in p for p in passes):
            first[key] = sum(p[key] for p in passes)
    first["foreign_bytes"] = max(p.get("foreign_bytes", 0) for p in passes)
    first["valid"] = valid
    first["shards"] = k
    first["shard_missing"] = [p.get("missing") for p in passes]
    first["shard_valid"] = [bool(p.get("valid")) for p in passes]
    first["shard_seconds"] = [p.get("seconds") for p in passes]
    first["seconds"] = round(sum(p.get("seconds") or 0 for p in passes), 1)
    for key in ("shard", "lo", "hi"):
        first.pop(key, None)
    if problems:
        first["error"] = "; ".join(problems)
        first["pass_mismatch"] = True
    elif not valid:
        errs = [p["error"] for p in passes if p.get("error")]
        if errs:
            first["error"] = errs[0]
    for key in ("length_matches", "sha_matches"):
        if key in first and False in [p.get(key) for p in passes]:
            first[key] = False
    return first


def check_any(path, threads=2, limit=None, mem_gb=None, shards=1, progress=None):
    """progress(pass_no, passes, letters) is called about once per 4 MiB read (pass_no from 1)."""
    try:
        if shards <= 1:
            cb = (lambda total: progress(1, 1, total)) if progress else None
            if progress:
                progress(1, 1, 0)
            res = _pass(path, threads, limit, mem_gb, None, cb)
        else:
            passes = []
            for i in range(shards):
                cb = (lambda total, i=i: progress(i + 1, shards, total)) if progress else None
                if progress:
                    progress(i + 1, shards, 0)
                r = _pass(path, threads, limit, mem_gb, (i, shards), cb)
                passes.append(r)
                if r.get("missing") is None:        # no checker result at all (crash, out of memory): stop
                    break
            res = combine(passes) if len(passes) == shards else dict(passes[-1], valid=False, shards=shards)
    except spra.SpraError as e:
        return {"file": os.path.basename(path), "kind": "spra", "valid": False, "ok": False,
                "error": "SPRA file refused: %s" % e}
    except decode.RecipeError as e:
        return {"file": os.path.basename(path.rstrip("/\\")), "kind": "recipe", "valid": False, "ok": False,
                "error": "recipe refused: %s" % e}
    except (OSError, RuntimeError) as e:
        return {"file": os.path.basename(path.rstrip("/\\")), "valid": False, "ok": False, "error": str(e)}
    except Exception as e:      # anything a hostile file can still provoke is a refusal of that file, never a traceback
        return {"file": os.path.basename(path.rstrip("/\\")), "valid": False, "ok": False,
                "error": "the file could not be read (%s: %s)" % (type(e).__name__, str(e)[:200])}
    res["ok"] = bool(res.get("valid")) and res.get("length_matches", True) and res.get("sha_matches") is not False \
        and limit is None
    return res


class ProgressPrinter:
    """prints `#P {...}` lines, at most one per `every` seconds, always on the first call of a pass."""

    def __init__(self, out=None, every=1.0):
        self.out, self.every, self.last, self.pass_no = out or sys.stdout, every, 0.0, None

    def __call__(self, pass_no, passes, letters):
        now = time.time()
        if pass_no != self.pass_no or now - self.last >= self.every:
            self.pass_no, self.last = pass_no, now
            self.out.write("#P %s\n" % json.dumps({"pass": pass_no, "passes": passes, "letters": letters}))
            self.out.flush()


def main(argv):
    if not argv:
        sys.exit(__doc__)
    opt = {"--threads": 2, "--limit": None, "--mem-gb": None, "--shards": 1}
    for k in opt:
        if k in argv:
            i = argv.index(k)
            opt[k] = float(argv[i + 1]) if k == "--mem-gb" else int(argv[i + 1])
    prog = ProgressPrinter() if "--progress" in argv else None
    res = check_any(argv[0], opt["--threads"], opt["--limit"], opt["--mem-gb"], opt["--shards"], prog)
    print(json.dumps(res), flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

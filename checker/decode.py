"""decode.py - turn a "splan/1" recipe (see docs/RECIPE.md) into a byte stream of the word.

    python -I checker/decode.py RECIPE [--limit L] > word.txt        (the word goes to stdout; the footer check is check_any.py)
    python -I checker/decode.py RECIPE --info                        (validated header, no generator run)

RECIPE is NAME.splan.json.xz, NAME.splan.json, or a directory holding recipe.json (+ files it names).
Only generators of the fixed registry are run, as binaries built from the bot's own sources; the recipe supplies data
only (seed, plan, alphabet), checked against the SHA-256 values it states.
"""
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder
import limits
import registry

MAX_JSON = 64 << 20      # decompressed size of the recipe JSON
MAX_SEED = 16 << 20
MAX_PLAN = 64 << 20
HEX = re.compile(r"^[0-9a-f]{64}$")


class RecipeError(ValueError):
    pass


def _read_limited(f, cap, what):
    data = f.read(cap + 1)
    if len(data) > cap:
        raise RecipeError("%s larger than %d bytes" % (what, cap))
    return data


def load(path):
    """-> (recipe dict, base directory or None). Validates the structure and the generator; not the data hashes."""
    base = None
    try:
        if os.path.isdir(path):
            base = os.path.abspath(path)
            with open(os.path.join(base, "recipe.json"), "rb") as f:
                raw = _read_limited(f, MAX_JSON, "recipe.json")
        elif path.endswith(".xz"):
            with lzma.open(path, "rb") as f:
                raw = _read_limited(f, MAX_JSON, "recipe")
        else:
            with open(path, "rb") as f:
                raw = _read_limited(f, MAX_JSON, "recipe")
    except (OSError, lzma.LZMAError, EOFError) as e:
        raise RecipeError("cannot read the recipe: %s" % e)
    try:
        r = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise RecipeError("recipe is not JSON: %s" % e)
    validate(r, allow_files=base is not None)
    return r, base


def validate(r, allow_files):
    if not isinstance(r, dict) or r.get("format") != "splan/1":
        raise RecipeError('format must be "splan/1"')
    n, alph, length = r.get("n"), r.get("alphabet"), r.get("length")
    if not isinstance(n, int) or isinstance(n, bool) or not 2 <= n <= registry.MAX_N:
        raise RecipeError("n must be an integer in 2..%d" % registry.MAX_N)
    if not isinstance(alph, str) or len(alph) != n or len(set(alph)) != n or \
            not all(33 <= ord(c) < 127 for c in alph):
        raise RecipeError("alphabet must be n distinct printable ASCII characters, no space")
    if not isinstance(length, int) or isinstance(length, bool) or length < n:
        raise RecipeError("length must be an integer >= n")
    w = r.get("word_sha256")
    if w is not None and not (isinstance(w, str) and HEX.match(w)):
        raise RecipeError("word_sha256 must be 64 lowercase hex digits or null")
    g = r.get("generator")
    if not isinstance(g, dict):
        raise RecipeError("generator missing")
    try:
        spec = registry.generator(g.get("id"), g.get("version"), g.get("source_sha256"))
    except ValueError as e:
        raise RecipeError(str(e))
    if spec["n"] != n:
        raise RecipeError("generator %s makes words for n = %d, not %d" % (g["id"], spec["n"], n))
    for key, needed in (("seed", True), ("plan", spec["needs_plan"])):
        v = r.get(key)
        if v is None:
            if needed:
                raise RecipeError("%s missing" % key)
            continue
        if not needed:
            raise RecipeError("generator %s takes no %s" % (g["id"], key))
        if not isinstance(v, dict) or not HEX.match(str(v.get("sha256"))):
            raise RecipeError('%s must be {"text"|"file": ..., "sha256": 64 hex}' % key)
        if ("text" in v) == ("file" in v):
            raise RecipeError("%s needs exactly one of text, file" % key)
        if "text" in v and not isinstance(v["text"], str):
            raise RecipeError("%s.text must be a string" % key)
        if "file" in v and not allow_files:
            raise RecipeError("%s.file is only allowed in a recipe directory" % key)


def _safe_join(base, rel):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or ".." in re.split(r"[\\/]", rel) or ":" in rel:
        raise RecipeError("file name %r not allowed" % (rel,))
    p = os.path.abspath(os.path.join(base, rel))
    if os.path.commonpath([p, base]) != base or not os.path.isfile(p):
        raise RecipeError("file %r not found inside the recipe directory" % rel)
    return p


def part_bytes(r, base, key, cap):
    """the bytes of seed/plan after the hash check (a file ending .xz in a directory is decompressed first; the hash is
    of the decompressed bytes)."""
    v = r[key]
    if "text" in v:
        try:
            data = v["text"].encode("utf-8")
        except UnicodeEncodeError as e:
            raise RecipeError("%s.text is not valid UTF-8 text: %s" % (key, e))
        if len(data) > cap:
            raise RecipeError("%s larger than %d bytes" % (key, cap))
    else:
        p = _safe_join(base, v["file"])
        op = lzma.open if p.endswith(".xz") else open
        with op(p, "rb") as f:
            data = _read_limited(f, cap, key)
    if hashlib.sha256(data).hexdigest() != v["sha256"]:
        raise RecipeError("%s: SHA-256 does not match the recipe" % key)
    return data


class Stream:
    """the running generator: read(n) gives bytes; close() returns (returncode, stderr text) and removes the temp dir."""

    def __init__(self, proc, tmp, errf):
        self.proc, self.tmp, self.errf = proc, tmp, errf

    def read(self, n=-1):
        return self.proc.stdout.read(n)

    def close(self, kill=False):
        if kill and self.proc.poll() is None:
            self.proc.kill()
        self.proc.stdout.close()
        rc = self.proc.wait()
        self.errf.close()
        try:
            with open(os.path.join(self.tmp, "gen.err"), "r", errors="replace") as f:
                err = f.read()[-4000:]
        except OSError:
            err = ""
        shutil.rmtree(self.tmp, ignore_errors=True)
        return rc, err

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close(kill=True)


def open_stream(path, limit=None, mem_gb=None):
    """validate the recipe, check the hashes of seed and plan, start the registry's generator. Returns (recipe, Stream)."""
    r, base = load(path)
    g = r["generator"]
    spec = registry.generator(g["id"], g["version"], g["source_sha256"])
    seed = part_bytes(r, base, "seed", MAX_SEED)
    plan = part_bytes(r, base, "plan", MAX_PLAN) if spec["needs_plan"] else None
    exe = registry.binary(spec["program"])
    tmp = tempfile.mkdtemp(prefix="splan-")
    try:
        names = {"alpha": r["alphabet"], "seed": os.path.join(tmp, "seed.txt")}
        with open(names["seed"], "wb") as f:
            f.write(seed)
        if plan is not None:
            names["plan"] = os.path.join(tmp, "plan.uplan")
            with open(names["plan"], "wb") as f:
                f.write(plan)
        argv = [exe] + [a.format(**names) for a in spec["args"]]
        if limit is not None:
            argv += ["--limit", str(int(limit))]
        errf = open(os.path.join(tmp, "gen.err"), "wb")
        proc = limits.popen(argv, mem_gb=mem_gb, stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL, cwd=tmp)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return r, Stream(proc, tmp, errf)


def main(argv):
    if not argv:
        sys.exit(__doc__)
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    if "--info" in argv:
        r, base = load(argv[0])
        info = {k: r[k] for k in ("format", "n", "alphabet", "length", "word_sha256", "generator") if k in r}
        print(json.dumps(info, indent=1))
        return 0
    r, st = open_stream(argv[0], limit)
    out = sys.stdout.buffer
    try:
        while True:
            b = st.read(1 << 22)
            if not b:
                break
            out.write(b)
    finally:
        rc, err = st.close(kill=True)
    sys.stderr.write(err)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except RecipeError as e:
        sys.exit("recipe refused: %s" % e)

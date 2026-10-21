"""make_recipe.py - write splan/1 recipes (docs/RECIPE.md).

    python -I tools/make_recipe.py uplan SEL(.txt|.xz) UPLAN(.uplan|.xz) OUT.splan.json.xz [--alphabet 0123456789ABCD] [--credit TEXT]
        gen14-uplan recipe; length and word_sha256 come from the plan's "# expect length L sha256 H" line.
    python -I tools/make_recipe.py import-echols FILE.spc.xz OUT.splan.json.xz [--word-sha256 H]
        reads William Echols's superperm-2lift/1 JSON as data (his decode.py is never run) and writes a pantone-2lift
        recipe: his seed (hash checked against his seed_sha256) and his alphabet and length. He gives no hash of the
        word; --word-sha256 adds one that somebody else computed (marked in "provenance").
"""
import hashlib
import json
import lzma
import re
import sys

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # python -I drops script folders
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "bot"))
import registry


def _read(path):
    op = lzma.open if path.endswith(".xz") else open
    with op(path, "rb") as f:
        return f.read()


def _write(path, rec):
    raw = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if path.endswith(".xz"):
        raw = lzma.compress(raw, preset=9 | lzma.PRESET_EXTREME)
    with open(path, "wb") as f:
        f.write(raw)


def _gen(gid):
    from registry import GENERATORS
    g = GENERATORS[gid]
    return {"id": gid, "version": g["version"], "source_sha256": registry.source_sha256(g["program"])}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def from_uplan(sel_path, plan_path, out, alphabet="0123456789ABCD", credit=None):
    seed, plan = _read(sel_path), _read(plan_path)
    m = re.search(rb"^# expect length (\d+) sha256 ([0-9a-f]{64})\s*$", plan, re.M)
    if not m:
        raise SystemExit("the plan has no '# expect length L sha256 H' line")
    rec = {"format": "splan/1", "n": 14, "alphabet": alphabet, "length": int(m.group(1)),
           "word_sha256": m.group(2).decode(), "generator": _gen("gen14-uplan"),
           "seed": {"text": seed.decode("ascii"), "sha256": sha(seed)},
           "plan": {"text": plan.decode("ascii"), "sha256": sha(plan)}}
    if alphabet != "0123456789ABCD":
        rec["word_sha256"] = None    # the hash in the plan is for the plan's own alphabet
    if credit:
        rec["credits"] = credit
    _write(out, rec)
    return rec


def import_echols(spc_path, out, word_sha256=None):
    e = json.loads(_read(spc_path).decode("utf-8"))
    if e.get("format") != "superperm-2lift/1":
        raise SystemExit("not a superperm-2lift/1 file")
    seed = e["seed"].encode("ascii")
    if sha(seed) != e["seed_sha256"]:
        raise SystemExit("seed does not match seed_sha256")
    rec = {"format": "splan/1", "n": len(e["alphabet"]), "alphabet": e["alphabet"], "length": int(e["length"]),
           "word_sha256": word_sha256, "generator": _gen("pantone-2lift"),
           "seed": {"text": e["seed"], "sha256": e["seed_sha256"]},
           "credits": {"imported_from": "superperm-2lift/1", "source": e.get("source"),
                       "recipe_author": "William Echols (idea and word); construction: Jay Pantone"}}
    if word_sha256:
        rec["provenance"] = "word_sha256 supplied at import time, not part of the original file"
    _write(out, rec)
    return rec


def main(a):
    if len(a) >= 4 and a[0] == "uplan":
        opt = dict(zip(a[4::2], a[5::2]))
        r = from_uplan(a[1], a[2], a[3], opt.get("--alphabet", "0123456789ABCD"), opt.get("--credit"))
    elif len(a) >= 3 and a[0] == "import-echols":
        opt = dict(zip(a[3::2], a[4::2]))
        r = import_echols(a[1], a[2], opt.get("--word-sha256"))
    else:
        sys.exit(__doc__)
    print(json.dumps({k: r[k] for k in ("n", "alphabet", "length", "word_sha256")}))


if __name__ == "__main__":
    main(sys.argv[1:])

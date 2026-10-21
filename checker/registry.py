"""registry.py - the fixed list of programs the bot may run. Nothing that comes with a submission is ever run.

Every program is built from a C source in gens/ at the repository root (env SUPERPERM_SRC overrides it) whose SHA-256
must be one of the accepted values below; the binary is looked up in bin/ at the repository root (env SUPERPERM_BIN
overrides it) and its build record (bin/BUILD.json, written by checker/build.py) must name an accepted source hash.

A recipe names a generator id and the SHA-256 of the generator source it was made with. The recipe is accepted only if
that hash is the one accepted here for the id.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # the repository: bin/ and gens/ are next to checker/
EXE = ".exe" if os.name == "nt" else ""

# source name -> accepted SHA-256 values (changed by hand when a source changes on purpose; the values
# of vfast and verify_stream are those with --shard I/K, so that a binary without --shard is never started)
SOURCES = {
    # gen14: the first value is the source built now (rows of a selection must be permutations of 0-9; found by
    # fuzzing on 2026-10-21); the second is the source before that check, kept so that recipes made with it are
    # accepted: on every valid selection both give the same word
    "gen14": ["5043a01c0df84abfb085e851985283543c8651e02d34e1c3061baa53337463ac",
              "ccd0d1ce84f73aee03b6eec8079c9e1f8bae79c15ece76644a00d20a72b424f2"],
    "vfast": ["98d32e9edea4c97b87787ab95cbd512737c773b9ddfbdbac10d974d0da8c140d"],
    "verify_stream": ["2b7b0422f29658ccbfe674cacd6e319d88fcfb3f6edfaa7b26526373069eba5a"],
    "spra": ["51ffb38b3bb437e026a917e3e6096b30b69d38a6da3fddec95be80a162de69f6"],     # decoder of SPRA 0.1 / 0.2 words (format of Theo H.)
}

# generator id -> description. "args" use {seed} and {plan} (paths of the files written by decode.py), {alpha}.
GENERATORS = {
    "gen14-uplan": {
        "version": "1", "program": "gen14", "n": 14, "needs_plan": True,
        "args": ["plan", "{seed}", "{plan}", "--alpha", "{alpha}", "--nosha"],
        "about": "n = 14 from a selection C-type seed (362,880 rows) and an N14-UNITPLAN 1 plan",
    },
    "pantone-2lift": {
        "version": "1", "program": "gen14", "n": 14, "needs_plan": False,
        "args": ["echols", "{seed}", "--alpha", "{alpha}", "--nosha"],
        "about": "n = 14, the 2-lift of a selection in Echols's order (also decodes superperm-2lift/1 files)",
    },
}

CHECKERS = {"verify_stream": "verify_stream", "vfast": "vfast"}

MAX_N = 14   # the checker table has n!/8 bytes: 10.9 GB at n = 14, 175 GB at n = 15


def bin_dir():
    return os.environ.get("SUPERPERM_BIN") or os.path.join(ROOT, "bin")


def src_dir():
    return os.environ.get("SUPERPERM_SRC") or os.path.join(ROOT, "gens")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def source_path(program):
    return os.path.join(src_dir(), program + ".c")


def source_sha256(program):
    """SHA-256 of the source the registry accepts for this program (the one that is built)."""
    return SOURCES[program][0]


def check_source(program):
    got = sha256_file(source_path(program))
    if got not in SOURCES[program]:
        raise RuntimeError("source of %s has SHA-256 %s, not an accepted value" % (program, got))
    return got


def binary(program):
    """path of the built program; refuses a binary whose build record does not name an accepted source."""
    exe = os.path.join(bin_dir(), program + EXE)
    rec = os.path.join(bin_dir(), "BUILD.json")
    if not os.path.exists(exe):
        raise RuntimeError("%s is not built: run  python -I checker/build.py" % exe)
    try:
        with open(rec, encoding="utf-8") as f:
            built = json.load(f).get(program)
    except (OSError, ValueError):
        built = None
    if built not in SOURCES[program]:
        raise RuntimeError("build record of %s missing or not an accepted source: run  python -I checker/build.py" % program)
    return exe


def generator(gid, version, source_sha):
    """the registry entry for a recipe's generator, or ValueError saying why it is refused."""
    if not isinstance(gid, str):
        raise ValueError("generator id must be a string, not %r" % (gid,))
    g = GENERATORS.get(gid)
    if g is None:
        raise ValueError("unknown generator id %r (known: %s)" % (gid, ", ".join(sorted(GENERATORS))))
    if str(version) != g["version"]:
        raise ValueError("generator %s: version %r not supported (have %s)" % (gid, version, g["version"]))
    if source_sha not in SOURCES[g["program"]]:
        raise ValueError("generator %s: source_sha256 %r is not the one this bot runs" % (gid, source_sha))
    return g


def pick_checker(n):
    """(program, extra args after N ALPHABET) for a given n: one-thread vfast for large n (80 MB/s), else verify_stream."""
    if n >= 13:
        return "vfast", ["1"]
    return "verify_stream", None   # None: pass the thread count


if __name__ == "__main__":
    for p in SOURCES:
        print(p, source_sha256(p), sha256_file(source_path(p)))

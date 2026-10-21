"""core.py - shared part of the superpermutation bots (GitHub Action and Discord): check one word.

A word is checked by streaming it (plain text or .xz, or the output of a recipe decoder) through the C checker
`verify_stream` / `vfast` (gens/verify_stream.c, gens/vfast.c), while its SHA-256 is computed on the fly. The symbols of a plain word
are found from the word itself (they must be n distinct characters); white space anywhere is ignored. Nothing is written
to disk.

    python -I checker/core.py check WORD [VERIFY_STREAM_EXE] [THREADS]      -> one JSON line
"""
import hashlib
import json
import lzma
import os
import subprocess
import sys
import time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder
import limits
import registry

CHUNK = 1 << 22
WHITE = b" \t\r\n\x0b\x0c"       # dropped anywhere in a word, so wrapped lines are fine
MAX_LETTERS = 200_000_000_000    # cap on a plain word (n = 14 words have 9.4e10); stops decompression bombs


def _open(path):
    if path.endswith(".xz"):
        return lzma.open(path, "rb")
    return open(path, "rb")


def alphabet_of(path, sample=1 << 24):
    """the distinct symbols of the word, in order of first appearance (from the first `sample` bytes; a symbol that
    appears later is counted by the checker as a foreign byte)."""
    seen = []
    with _open(path) as f:
        data = f.read(sample)
    for b in data:
        if b in WHITE:
            continue
        if b not in seen:
            seen.append(b)
    return bytes(seen).decode("latin-1")


def check_stream(read, alph, exe=None, threads=2, mem_gb=None, max_letters=MAX_LETTERS, shard=None, progress=None):
    """feed read(CHUNK) -> bytes into the checker for the alphabet alph; returns the checker's JSON dict plus
    sha256 (of the letters; white space anywhere is dropped before the checker and the hash), letters_read (bytes read),
    checker_rc.
    shard=(i, k): the checker tracks only the permutations of rank range i of k (its `missing` is for that range).
    progress(total_bytes_read) is called once per chunk (not per byte); it must be quick."""
    n = len(alph)
    if not 2 <= n <= registry.MAX_N:
        return {"valid": False, "error": "n = %d is outside 2..%d" % (n, registry.MAX_N)}
    if exe:
        cmd = [exe, str(n), alph, str(threads)]
    else:
        prog, extra = registry.pick_checker(n)
        cmd = [registry.binary(prog), str(n), alph] + (extra if extra else [str(threads)])
    if shard is not None:
        cmd += ["--shard", "%d/%d" % (shard[0], shard[1])]
    proc = limits.popen(cmd, mem_gb=mem_gb, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    h = hashlib.sha256()
    total, too_long = 0, False
    try:
        while True:
            b = read(CHUNK)
            if not b:
                break
            total += len(b)
            if progress is not None:
                progress(total)
            if total > max_letters:
                too_long = True
                break
            b = b.translate(None, WHITE)
            proc.stdin.write(b)
            h.update(b)
    except (BrokenPipeError, OSError):
        pass    # the checker ended early; its output line below says why
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
    out = proc.stdout.read().decode(errors="replace")
    proc.stdout.close()
    rc = proc.wait()
    lines = [x for x in out.strip().splitlines() if x.startswith("{")]
    if not lines:
        return {"valid": False, "error": "checker gave no result (rc %d, out of memory?)" % rc, "checker_rc": rc}
    res = json.loads(lines[-1])
    res.update({"alphabet": alph, "sha256": h.hexdigest(), "checker_rc": rc, "letters_read": total,
                "valid": rc == 0 and not too_long})
    if too_long:
        res["error"] = "word longer than %d letters" % max_letters
    return res


def check(path, exe=None, threads=2, mem_gb=None, shard=None, progress=None):
    """check the plain word at path (.txt or .xz); returns a dict with n, length, sha256, valid and the checker's counts."""
    alph = alphabet_of(path)
    t0 = time.time()
    if not 2 <= len(alph) <= registry.MAX_N:
        return {"file": os.path.basename(path), "valid": False, "error": "found %d symbols" % len(alph)}
    if exe and os.path.exists(exe):
        exe = os.path.abspath(exe)
    with _open(path) as f:
        res = check_stream(f.read, alph, exe, threads, mem_gb, shard=shard, progress=progress)
    res.update({"file": os.path.basename(path), "kind": "word", "seconds": round(time.time() - t0, 1)})
    return res


def main(argv):
    if len(argv) >= 2 and argv[0] == "check":
        exe = argv[2] if len(argv) > 2 else None
        thr = int(argv[3]) if len(argv) > 3 else 2
        print(json.dumps(check(argv[1], exe, thr)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

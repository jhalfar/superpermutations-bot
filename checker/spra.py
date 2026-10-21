"""spra.py - words in the SPRA format of Theo H. (github.com/Tholsh/SPRA, docs/reduced_binary.md): .spra or .spra.xz.

The header (24 bytes) gives n, the length and the label set; the body is decoded by the C program gens/spra.c, which
reads the raw SPRA bytes on stdin and writes the letters to stdout. Python only reads the header and feeds the file
(decompressing a .spra.xz on the way) into the decoder, so a SPRA file is data: nothing in it is run.

    python -I checker/spra.py FILE.spra[.xz]        print the header as JSON
"""
import json
import lzma
import os
import subprocess
import sys
import tempfile
import threading

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder
import limits
import registry

EXTS = (".spra.xz", ".spra")
LABELS = {0: "123456789ABCDEFGHIJKLMNOPQRSTUVW", 1: "0123456789ABCDEFGHIJKLMNOPQRSTUV"}
CHUNK = 1 << 20


class SpraError(ValueError):
    """the file is not a SPRA file this bot accepts."""


def is_spra(path):
    return os.path.basename(path).lower().endswith(EXTS)


def _open(path):
    return lzma.open(path, "rb") if path.lower().endswith(".xz") else open(path, "rb")


def header(path):
    """-> dict(n, length, tuples, bits, zero_based, version, alphabet); SpraError if the header is not valid."""
    try:
        with _open(path) as f:
            h = f.read(24)
    except (lzma.LZMAError, EOFError) as e:
        raise SpraError("cannot decompress: %s" % e)
    if len(h) < 24 or h[:4] != b"SPRA":
        raise SpraError("not a SPRA file (no SPRA header)")
    ver, layout, flags, n = h[4], h[5], h[6], h[7]
    if ver not in (1, 2):
        raise SpraError("SPRA version %d is not supported (1 and 2 are)" % ver)
    if layout not in (0x44, 0x66):
        raise SpraError("unknown SPRA layout 0x%02x" % layout)
    if flags & ~1:
        raise SpraError("reserved SPRA flag bits are set")
    if not 1 <= n <= 32:
        raise SpraError("n = %d outside 1..32" % n)
    length = int.from_bytes(h[8:16], "little")
    tuples = int.from_bytes(h[16:24], "little")
    return {"n": n, "length": length, "tuples": tuples, "bits": 4 if layout == 0x44 else 6,
            "zero_based": bool(flags & 1), "version": ver, "alphabet": LABELS[flags & 1][:n]}


class Stream:
    """the decoder process; read(n) gives letters. close() -> (exit code, last stderr line)."""

    def __init__(self, path, limit=None, mem_gb=None):
        argv = [registry.binary("spra")] + (["--limit", str(int(limit))] if limit is not None else [])
        self.err = tempfile.TemporaryFile()
        self.proc = limits.popen(argv, mem_gb=mem_gb, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err)
        self.src = _open(path)
        self.feed_error = None
        self.feeder = threading.Thread(target=self._feed, daemon=True)
        self.feeder.start()

    def _feed(self):
        try:
            while True:
                b = self.src.read(CHUNK)
                if not b:
                    break
                self.proc.stdin.write(b)
        except (BrokenPipeError, OSError, ValueError):
            pass                       # the decoder stopped (an error, --limit, or a kill): its exit code tells
        except (lzma.LZMAError, EOFError) as e:
            self.feed_error = "cannot decompress: %s" % e
        finally:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.src.close()

    def read(self, n=-1):
        return self.proc.stdout.read(n)

    def close(self, kill=False):
        if kill and self.proc.poll() is None:
            self.proc.kill()
        self.proc.stdout.close()
        rc = self.proc.wait()
        self.feeder.join(timeout=30)
        self.err.seek(0)
        msg = self.err.read().decode(errors="replace").strip()
        self.err.close()
        if self.feed_error:
            msg = self.feed_error
        return rc, (msg.splitlines()[-1] if msg else "")


def main(argv):
    if not argv:
        sys.exit(__doc__)
    try:
        print(json.dumps(header(argv[0])))
    except SpraError as e:
        sys.exit("refused: %s" % e)


if __name__ == "__main__":
    main(sys.argv[1:])

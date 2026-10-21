"""magic.py - a quick look at the first bytes of an uploaded file, before any decoding, for a clear message.

This is for clear refusals ("this is not an xz file"), NOT the security boundary: the decoders and the checkers still parse
everything strictly and run with limits. A file that passes here is only probably what its name says.

    .xz endings        start with FD 37 7A 58 5A 00
    .spra              starts with "SPRA" (a .spra.xz after decompression)
    .splan.json(.xz)   decodes as UTF-8 JSON, an object with a "format" field
    .txt (.txt.xz)     printable ASCII plus white space in the first 1 MiB (after decompression for .txt.xz)
"""
import json
import lzma

XZ_MAGIC = b"\xfd7zXZ\x00"
TEXT_HEAD = 1 << 20
JSON_MAX = 64 << 20
WHITE = b" \t\r\n\x0b\x0c"
PRINTABLE = bytes(range(0x20, 0x7F)) + WHITE


class MagicError(Exception):
    """str(e) is the reason, e.hint what to do."""

    def __init__(self, msg, hint="Check that the file is complete and has the format its name says."):
        super().__init__(msg)
        self.hint = hint


def _xz_head(path, n):
    try:
        with lzma.open(path, "rb") as f:
            return f.read(n)
    except (lzma.LZMAError, EOFError, OSError) as e:
        raise MagicError("the xz data cannot be read (%s)" % (str(e) or type(e).__name__)[:80])


def check(path, name):
    """raise MagicError if the file does not look like what its name says; return the kind guessed from the name."""
    low = name.lower()
    xz = low.endswith(".xz")
    with open(path, "rb") as f:
        first = f.read(len(XZ_MAGIC))
    if xz and first != XZ_MAGIC:
        raise MagicError("this is not an xz file: it does not start with the xz signature (FD 37 7A 58 5A 00)")
    base = low[:-3] if xz else low
    if base.endswith(".splan.json"):
        data = _xz_head(path, JSON_MAX + 1) if xz else _read(path, JSON_MAX + 1)
        if len(data) > JSON_MAX:
            raise MagicError("the recipe is larger than 64 MiB once decompressed")
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise MagicError("this does not look like a recipe: it is not UTF-8 JSON")
        if not isinstance(obj, dict):
            raise MagicError("this does not look like a recipe: the JSON is not an object")
        if "format" not in obj:
            raise MagicError("this does not look like a recipe: the JSON object has no \"format\" field")
        return "recipe"
    if base.endswith(".spra"):
        head = _xz_head(path, 4) if xz else first4(path)
        if head != b"SPRA":
            raise MagicError("this is not a SPRA file: it does not start with \"SPRA\"" + (" once decompressed" if xz else ""))
        return "spra"
    if base.endswith(".txt"):
        head = _xz_head(path, TEXT_HEAD) if xz else _read(path, TEXT_HEAD)
        if head.translate(None, PRINTABLE):
            raise MagicError("this does not look like a text word: it has bytes that are neither printable ASCII nor white space",
                             hint="A word is plain text made of letters and digits.")
        return "word"
    return None


def _read(path, n):
    with open(path, "rb") as f:
        return f.read(n)


def first4(path):
    return _read(path, 4)

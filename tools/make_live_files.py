"""make_live_files.py - small files for a live test of the bot on Discord (docs/LIVE_TEST.md).

    python -I tools/make_live_files.py [OUT_DIR=live_test_files]
"""
import lzma, os, sys
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "live_test_files")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "checker"))
sys.path.insert(0, os.path.join(ROOT, "bot"))
sys.path.insert(0, os.path.join(ROOT, "tests"))   # the word and SPRA encoders of the tests
from test_check import greedy_word, ALPH  # noqa: E402
from test_spra import encode  # noqa: E402

os.makedirs(OUT, exist_ok=True)


def put(name, data, xz=False):
    p = os.path.join(OUT, name)
    with (lzma.open(p, "wb") if xz else open(p, "wb")) as f:
        f.write(data)
    print("%-32s %8d bytes" % (name, os.path.getsize(p)))

w5, w6, w7, w8 = (greedy_word(n) for n in (5, 6, 7, 8))
put("01_ok_n5.txt", w5 + b"\n")
put("02_ok_n7.txt.xz", w7, xz=True)
put("03_ok_n8.spra.xz", encode([ALPH.index(chr(c)) for c in w8], 8, bits=6), xz=True)
put("04_bad_n6_one_letter_deleted.txt", w6[:400] + w6[401:])
put("05_bad_n6_foreign_char.txt", w6[:300] + b"X" + w6[300:])
put("06_ok_n6_crlf_lines.txt", b"\r\n".join(w6[i:i + 60] for i in range(0, len(w6), 60)) + b"\r\n")
put("07_bad_n8_broken.spra.xz", encode([ALPH.index(chr(c)) for c in w8], 8, bits=6) + b"\x00", xz=True)
put("08_refused_n15.txt", b"0123456789ABCDE" * 3)
put("09_refused_wrong_ext.zip", b"PK not a word")
put("10_not_spra.spra.xz", b"hello, not a SPRA file", xz=True)
put("11_bad_n5_truncated.txt", w5[:100])

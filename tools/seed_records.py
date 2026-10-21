"""seed_records.py - build records.json from what is public (as of the last fetch of origin/main of
github.com/jhalfar/superpermutations, 2026-10-07) and the claims of others.

    python -I tools/seed_records.py OUT.json WORDS_DIR [--only 10,11]

Our four words are checked by this bot (check_any) and enter through records.submit, so they are `verified`. The claims
of others have no word here: they stay unverified (sha256 null). Echols's n = 14 word enters as a claim; it becomes
verified when his recipe (import-echols) is checked by the bot (see docs/REFERENCE.md)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # python -I drops script folders
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "bot"))

import json
import os
import sys

import check_any
import records

URL = "https://github.com/jhalfar/superpermutations/blob/main/words/"
OURS = [  # (n, file, date it entered origin/main)
    (10, "superpermutation-10-4034855.txt.xz", "2026-10-06"),
    (11, "superpermutation-11-43930578.txt.xz", "2026-10-06"),
    (12, "superpermutation-12-522737175.txt.xz", "2026-10-06"),
    (13, "superpermutation-13-6747802393.txt.xz", "2026-10-07"),
]
CLAIMS = [  # (n, length, who, source, date): public claims of others; no word at hand
    (7, 5905, "Teodorescu", "forum thread D5sKkV2jBuc (as in DISCOVERIES.md)", "2026-09"),
    (8, 46181, "Jay Pantone", "github.com/jaypantone/superperm-upper-43-80", "2026-09/10"),
    (9, 408731, "rumstd", "as in README.md", "2026-09/10"),
    (10, 4034873, "rumstd", "as in README.md", "2026-09/10"),
    (11, 43930624, "Theo H.", "github.com/Tholsh/superpermutation_study", "2026-10"),
    (12, 522745531, "Theo H.", "github.com/Tholsh/superpermutation_study", "2026-10"),
    (13, 6747918066, "Jay Pantone", "github.com/jaypantone/superperm-upper-43-80", "2026-09/10"),
    (14, 93904936032, "William Echols", "superperm-2lift/1 recipe, 2026-10-17", "2026-10-17"),
]


def main(a):
    if len(a) < 2:
        sys.exit(__doc__)
    out, words = a[0], a[1]
    only = set(a[a.index("--only") + 1].split(",")) if "--only" in a else None
    table = {"format": records.FORMAT, "records": {}}
    for n, length, who, src, date in CLAIMS:
        table["records"].setdefault(str(n), []).append(
            {"length": length, "sha256": None, "submitter": who, "source": src, "date": date, "verified": False,
             "kind": "claim"})
    records.save(table, out)
    for n, name, date in OURS:
        if only and str(n) not in only:
            continue
        res = check_any.check_any(os.path.join(words, name), threads=1)
        print(json.dumps({k: res.get(k) for k in ("file", "n", "length", "sha256", "valid", "ok", "seconds")}), flush=True)
        r = records.submit(out, res, "Jakub Halfar", URL + name, date=date)
        print(n, r["status"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])

"""scan.py - the GitHub Action part: find superpermutation files in a repository, check each one, and report.

A file is taken when its name matches one of the patterns (default: superpermutation-*.txt, superpermutation-*.txt.xz, superpermutation-*.spra.xz and *.splan.json.xz anywhere in the repository). If the name has the form superpermutation-N-L.(txt|txt.xz|spra|spra.xz|splan.json.xz), N and
L are compared with what the check finds. Results go to a JSON file and, inside GitHub Actions, to the job summary.
Exit code 1 if any file is invalid or does not match its name.

    python scan.py ROOT [--patterns "a/*.txt,b/*.xz"] [--out results.json] [--threads 2]

Words (.txt, .txt.xz, SPRA .spra, .spra.xz) and recipes (*.splan.json.xz, see docs/RECIPE.md) are checked by checker/check_any.py of
this repository; the programs are built from gens/ by checker/build.py (SUPERPERM_BIN, SUPERPERM_SRC point at the build
and the sources).
A recipe is data for the fixed decoders of registry.py; nothing in the checked repository is ever executed.
"""
import fnmatch
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checker"))  # the checker of this repository
import check_any  # noqa: E402

NAME = re.compile(r"superpermutation-(\d+)-(\d+)(?:-[0-9a-f]{6,64})?\.(?:txt(?:\.xz)?|spra(?:\.xz)?|splan\.json\.xz)$")
# (an optional -HASH after the length names one of several words of the same length, e.g. ties)


def find(root, patterns):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
            if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(f, p) for p in patterns):
                yield rel


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    root = argv[0]
    opts = dict(zip(argv[1::2], argv[2::2]))
    patterns = opts.get("--patterns", "superpermutation-*.txt,superpermutation-*.txt.xz,superpermutation-*.spra.xz,*.splan.json.xz").split(",")
    out = opts.get("--out", "superperm-results.json")
    thr = int(opts.get("--threads", "2"))
    rows, bad = [], 0
    for rel in sorted(find(root, patterns)):
        r = check_any.check_any(os.path.join(root, rel), thr)
        m = NAME.search(rel)
        r["path"] = rel
        r["name_ok"] = None
        if m and r.get("valid") and r.get("length") is not None:
            r["name_ok"] = int(m.group(1)) == r["n"] and int(m.group(2)) == r["length"]
        ok = r.get("ok") and r["name_ok"] is not False
        bad += not ok
        rows.append(r)
        print(("OK  " if ok else "BAD ") + json.dumps({k: r.get(k) for k in
              ("path", "kind", "n", "length", "missing", "foreign_bytes", "name_ok", "length_matches", "sha_matches", "sha256", "error")}), flush=True)
    json.dump(rows, open(out, "w", encoding="utf-8"), indent=1)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Superpermutation check\n\n| file | n | length | missing | valid | name matches | SHA-256 |\n")
            f.write("|---|---:|---:|---:|---|---|---|\n")
            for r in rows:
                f.write("| `%s` | %s | %s | %s | %s | %s | `%s` |\n" % (
                    r["path"], r.get("n", ""), "{:,}".format(r["length"]) if "length" in r else "",
                    r.get("missing", ""), "yes" if r.get("ok") else "**no**",
                    {True: "yes", False: "**no**", None: "-"}[r["name_ok"]], r.get("sha256", "")[:16]))
    print("%d file(s), %d bad" % (len(rows), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:])

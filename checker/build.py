"""build.py - build the registry's programs from the sources in gens/ (or SUPERPERM_SRC) into bin/ (or SUPERPERM_BIN).

    python -I checker/build.py

Each source is checked against the accepted SHA-256 values in registry.py first. gcc -O2 -std=c11, standard C and
pthreads only (the same line is used by the GitHub Action on Linux, which builds from this folder and gens/ too).
"""
import json
import os
import subprocess

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder
import registry


def build():
    out = registry.bin_dir()
    os.makedirs(out, exist_ok=True)
    rec = {}
    for prog in registry.SOURCES:
        sha = registry.check_source(prog)
        exe = os.path.join(out, prog + registry.EXE)
        cmd = [os.environ.get("CC", "gcc"), "-O2", "-std=c11", "-o", exe, registry.source_path(prog), "-lpthread"]
        subprocess.run(cmd, check=True)
        rec[prog] = sha
        print("built", exe)
    json.dump(rec, open(os.path.join(out, "BUILD.json"), "w"), indent=1)


if __name__ == "__main__":
    build()

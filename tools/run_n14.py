"""run_n14.py RECIPE OUT.json - full check of an n = 14 recipe (about 12 GB, 25 to 60 minutes); writes the result as JSON.

    python -I tools/run_n14.py RECIPE.splan.json.xz OUT.json

On Windows put `python -I checker/guard.py --gb 14 --` in front to limit the memory. On Linux use `ulimit -v` or a container.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checker"))
import check_any  # noqa: E402

res = check_any.check_any(sys.argv[1], threads=1)
with open(sys.argv[2], "w") as f:
    json.dump(res, f)
print(json.dumps(res))
sys.exit(0 if res.get("ok") else 1)

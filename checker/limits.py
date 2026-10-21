"""limits.py - run a command with a memory limit and low priority.

Windows: through guard.py (a job object with a committed-memory limit and idle priority; looked up next to this file
(checker/guard.py), then in ../guard.py, or env SUPERPERM_GUARD). Linux: RLIMIT_AS of the child (inherited by its children) and nice 19.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_guard():
    for p in (os.environ.get("SUPERPERM_GUARD"), os.path.join(HERE, "guard.py"), os.path.join(HERE, "..", "guard.py")):
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return None


def popen(cmd, mem_gb=None, **kw):
    """subprocess.Popen(cmd, **kw) with the limit applied; cmd is a list (never a shell string)."""
    if os.name == "nt":
        if mem_gb:
            g = find_guard()
            if g is None:
                raise RuntimeError("memory limit requested but guard.py not found (set SUPERPERM_GUARD)")
            cmd = [sys.executable, "-I", g, "--gb", str(mem_gb), "--"] + list(cmd)
        else:
            kw["creationflags"] = kw.get("creationflags", 0) | 0x40   # IDLE_PRIORITY_CLASS
        return subprocess.Popen(cmd, **kw)

    def pre():
        import resource
        os.nice(19)
        if mem_gb:
            lim = int(mem_gb * 2 ** 30)
            resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    return subprocess.Popen(cmd, preexec_fn=pre, **kw)

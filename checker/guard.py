#!/usr/bin/env python
"""guard.py [--gb G] [--log FILE] -- CMD ARGS...

Runs a command at idle priority inside a Windows job object whose committed memory is limited to G gigabytes
(default 16; it was 3 on the 32 GB machine).  The limit is enforced by the operating system for the command and all its children (the venv's
python.exe is a launcher that starts the real interpreter as a child, so watching one PID is not enough): an
allocation beyond the limit fails, and the command ends with a MemoryError / out-of-memory status instead of
squeezing the machine.  Prints the peak committed memory of the job at the end, on stderr: stdout belongs to the
command (it may be a word stream)."""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys

IDLE_PRIORITY_CLASS = 0x40
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class IOC(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("a", "b", "c", "d", "e", "f")]


class EXT(ctypes.Structure):
    _fields_ = [("Basic", BASIC), ("IoInfo", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def main():
    a = sys.argv[1:]
    gb, log = 16.0, None
    while a and a[0] != "--":
        if a[0] == "--gb":
            gb = float(a[1])
            a = a[2:]
        elif a[0] == "--log":
            log = a[1]
            a = a[2:]
        else:
            break
    if a and a[0] == "--":
        a = a[1:]
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = wt.HANDLE
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    k32.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
    k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    info = EXT()
    info.Basic.LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.JobMemoryLimit = int(gb * 2 ** 30)
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        sys.exit("guard: SetInformationJobObject failed (%d); not running the command" % ctypes.GetLastError())
    if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        sys.exit("guard: AssignProcessToJobObject failed (%d); not running the command" % ctypes.GetLastError())
    out = open(log, "w") if log else None
    p = subprocess.Popen(a, creationflags=IDLE_PRIORITY_CLASS, stdout=out, stderr=subprocess.STDOUT if out else None)
    rc = p.wait()
    q = EXT()
    k32.QueryInformationJobObject(job, 9, ctypes.byref(q), ctypes.sizeof(q), None)
    msg = "[guard] exit %d, peak committed memory of the job %.2f GB (limit %.1f GB)" % (rc, q.PeakJobMemoryUsed / 2 ** 30, gb)
    print(msg, file=sys.stderr, flush=True)   # never stdout: there it would join the output of the command (a word stream)
    if out:
        out.write(msg + "\n")
        out.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()

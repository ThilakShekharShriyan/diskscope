"""BranchLab guest, stdlib-only. Every write stays in one scoped test directory.

Invoked as a fresh process (local) or a fresh Archil microVM (remote) for every step:
    python3 -S -c <this file> ACTION ABSOLUTE_PATH SIZE_KIB WORKER WORKERS [PARAM]
Imports are lazy so a step's latency is not dominated by interpreter start-up.
Nothing here kills a host, deletes user data, or creates disk branches.
"""
import os
import sys
import time

MARKER = "BRANCHLAB_JSON="
ACTIONS = {
    "init", "write", "read", "sqlite-write", "sqlite-check",
    "parallel-write", "parallel-check",
    "sem-setup", "sem-probe", "sem-probe-checkout",
    "vis-setup", "vis-reader", "vis-writer", "cleanup-trial",
}
# The two contents of the visibility file `y`; different lengths so a stale size is detectable.
VIS_OLD = b"o" * 100
VIS_NEW = b"n" * 200
VIS_NEW_FILE = b"x" * 64


_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")


def safe_base(raw):
    # No `re`: importing it costs ~15-30 ms in a cold microVM, on every step.
    name = os.path.basename(raw)
    suffix = name[len("branchlab-"):]
    if (not os.path.isabs(raw) or os.path.basename(os.path.dirname(raw)) != ".diskscope"
            or not name.startswith("branchlab-") or not 8 <= len(suffix) <= 90
            or not set(suffix) <= _NAME_CHARS
            or ".." in raw.split("/")):
        raise ValueError("unsafe fixture path: expected absolute .../.diskscope/branchlab-<id>")
    return raw


def payload(size_kib, worker):
    import hashlib
    if not 1 <= size_kib <= 4096:
        raise ValueError("size_kib must be within 1..4096")
    if not 0 <= worker <= 8:
        raise ValueError("worker must be 0..8")
    seed = hashlib.sha256(f"BranchLab-worker-{worker}".encode()).digest()
    return (seed * (size_kib * 1024 // len(seed) + 1))[:size_kib * 1024]


def sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def durable_fsync(fd):
    """fsync that reaches stable storage. On macOS plain fsync() leaves data in the drive's cache;
    F_FULLFSYNC flushes it, matching Archil's "durable once fsync returns" (as DiskScope does)."""
    import fcntl
    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass  # e.g. a filesystem that doesn't support it: fall back to fsync
    os.fsync(fd)


def sync_dir(p):
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        durable_fsync(fd)
    finally:
        os.close(fd)


def synced_write(p, data, exclusive=True):
    # 'xb' refuses to overwrite a previous trial's file.
    with open(p, "xb" if exclusive else "wb") as out:
        out.write(data)
        out.flush()
        durable_fsync(out.fileno())
    sync_dir(os.path.dirname(p))


def read_file(p):
    with open(p, "rb") as f:
        return f.read()


def outcome(fn):
    """Run one filesystem operation; return 'ok' or the errno name / exception class."""
    try:
        fn()
        return "ok"
    except OSError as e:
        import errno
        return errno.errorcode.get(e.errno, type(e).__name__) if e.errno else type(e).__name__
    except Exception as e:  # sqlite3.OperationalError etc.
        return f"{type(e).__name__}: {e}"


def archil_checkout(path):
    """`archil checkout` inside an Archil exec (the documented way to take a delegation)."""
    import shutil
    import subprocess
    if not shutil.which("archil"):
        return None, "no archil CLI (local run)"
    t = time.perf_counter()
    r = subprocess.run(["archil", "checkout", path], capture_output=True, text=True)
    ms = (time.perf_counter() - t) * 1e3
    return ms, ("ok" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["failed"])[-1])


def sem_files(d):
    """Entries an EARLIER execution creates, for sem-probe to operate on."""
    os.mkdir(d)
    os.mkdir(os.path.join(d, "sub"))
    for name in ("f", "g", "h", "sub/t"):
        with open(os.path.join(d, name), "wb") as out:
            out.write(b"v1\n")


def sem_ops(d):
    """What one execution can do to entries another execution created. Order matters only for f."""
    import sqlite3

    def overwrite():
        with open(os.path.join(d, "f"), "r+b") as o:
            o.write(b"v2\n")

    def append():
        with open(os.path.join(d, "f"), "ab") as o:
            o.write(b"v3\n")

    def atomic_replace():  # write-temp-then-rename: how most tools save a file safely
        tmp = os.path.join(d, "f.tmp")
        with open(tmp, "wb") as o:
            o.write(b"v4\n")
            o.flush()
            os.fsync(o.fileno())
        os.replace(tmp, os.path.join(d, "f"))

    def sqlite_create():
        con = sqlite3.connect(os.path.join(d, "app.db"))
        try:
            con.execute("CREATE TABLE t (v)")  # default journal_mode=DELETE
            con.execute("INSERT INTO t VALUES (1)")
            con.commit()
        finally:
            con.close()

    return {
        "overwrite": outcome(overwrite),
        "append": outcome(append),
        "create-file": outcome(lambda: open(os.path.join(d, "new"), "xb").close()),
        "mkdir": outcome(lambda: os.mkdir(os.path.join(d, "newdir"))),
        "chmod": outcome(lambda: os.chmod(os.path.join(d, "g"), 0o600)),
        "rename": outcome(lambda: os.rename(os.path.join(d, "g"), os.path.join(d, "g2"))),
        "tmp+rename": outcome(atomic_replace),
        "unlink": outcome(lambda: os.unlink(os.path.join(d, "h"))),
        "unlink-in-subdir": outcome(lambda: os.unlink(os.path.join(d, "sub", "t"))),
        "sqlite-create": outcome(sqlite_create),
    }


# One directory per access path, so one probe can never refresh another probe's cache entry.
VIS_NEW_DIRS = ("new-open", "new-stat", "new-list", "new-list-full", "new-open-expiry0")  # writer creates <dir>/x
VIS_OLD_DIRS = ("old-fd", "old-stat", "old-open")        # writer overwrites <dir>/y (100 -> 200 bytes)


def vis_setup(vis):
    os.mkdir(vis)
    for d in VIS_NEW_DIRS + VIS_OLD_DIRS:
        os.mkdir(os.path.join(vis, d))
    for d in VIS_OLD_DIRS:
        synced_write(os.path.join(vis, d, "y"), VIS_OLD)
    synced_write(os.path.join(vis, "new-list-full", "y0"), VIS_OLD)  # a non-empty listing


def set_readdir_expiry_zero(d):
    """Archil's per-directory fix for stale absent-name lookups (no-op without the archil CLI)."""
    import shutil
    import subprocess
    if not shutil.which("archil"):
        return "no archil CLI (local run)"
    r = subprocess.run(["archil", "set-cache-expiry", "--readdir-expiry", "0", "-q", d], capture_output=True, text=True)
    return "ok" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["failed"])[-1]


def vis_writer(vis):
    """Returns, per directory, when that file's fsync returned (staleness is measured from it)."""
    done = {}
    for d in VIS_NEW_DIRS:
        synced_write(os.path.join(vis, d, "x"), VIS_NEW_FILE)
        done[d] = time.time()
    for d in VIS_OLD_DIRS:
        synced_write(os.path.join(vis, d, "y"), VIS_NEW, exclusive=False)
        done[d] = time.time()
    return done


def vis_reader(vis, cap_s):
    """Long-lived reader. Prime each path's cache once, then poll each path ONLY its own way.

    Times are wall-clock epoch seconds so the coordinator can align them with the writer's.
    """
    p = lambda d, f: os.path.join(vis, d, f)  # noqa: E731
    expiry0 = set_readdir_expiry_zero(os.path.join(vis, "new-open-expiry0"))
    primed = {
        "expiry0": expiry0,
        "new-open": outcome(lambda: open(p("new-open", "x"), "rb").close()),   # negative lookup
        "new-open-expiry0": outcome(lambda: open(p("new-open-expiry0", "x"), "rb").close()),
        "new-list-full": sorted(os.listdir(os.path.join(vis, "new-list-full"))),
        "new-stat": outcome(lambda: os.stat(p("new-stat", "x"))),              # negative lookup
        "new-list": sorted(os.listdir(os.path.join(vis, "new-list"))),          # cached listing
        "old-stat": os.stat(p("old-stat", "y")).st_size,                        # cached attributes
        "old-open": len(read_file(p("old-open", "y"))),                         # cached data, closed
    }
    held = os.open(p("old-fd", "y"), os.O_RDONLY)
    primed["old-fd"] = len(os.pread(held, 4096, 0))                             # data via open fd
    t_prime = time.time()
    probes = {
        "new file · open()": lambda: read_file(p("new-open", "x")) == VIS_NEW_FILE,
        "new file · stat()": lambda: os.path.exists(p("new-stat", "x")),
        "new file · listdir() empty dir": lambda: "x" in os.listdir(os.path.join(vis, "new-list")),
        "new file · listdir() non-empty dir": lambda: "x" in os.listdir(os.path.join(vis, "new-list-full")),
        "new file · open(), readdir expiry 0": lambda: read_file(p("new-open-expiry0", "x")) == VIS_NEW_FILE,
        "overwrite · re-open()": lambda: read_file(p("old-open", "y")) == VIS_NEW,
        "overwrite · stat() size": lambda: os.stat(p("old-stat", "y")).st_size == len(VIS_NEW),
        "overwrite · already-open fd": lambda: os.pread(held, 4096, 0) == VIS_NEW,
    }
    dirs = dict(zip(probes, ("new-open", "new-stat", "new-list", "new-list-full", "new-open-expiry0",
                             "old-open", "old-stat", "old-fd")))
    first = {k: None for k in probes}
    polls = 0
    deadline = t_prime + cap_s
    while time.time() < deadline and any(v is None for v in first.values()):
        polls += 1
        for k, check in probes.items():
            if first[k] is None:
                try:
                    if check():
                        first[k] = time.time()
                except OSError:
                    pass
        time.sleep(0.02)
    os.close(held)
    return {"t_prime": t_prime, "t_end": time.time(), "cap_s": cap_s, "polls": polls,
            "primed": primed, "first_seen": first, "dirs": dirs}


def encode(v):
    """Minimal JSON encoder. `import json` costs ~30 ms in a cold Archil microVM (measured), on every step."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v) if v == v and v not in (float("inf"), float("-inf")) else "null"
    if isinstance(v, str):
        out = []
        for c in v:
            if c == '"' or c == "\\":
                out.append("\\" + c)
            elif ord(c) < 0x20:
                out.append("\\u%04x" % ord(c))
            else:
                out.append(c)
        return '"' + "".join(out) + '"'
    if isinstance(v, dict):
        return "{" + ",".join(encode(str(k)) + ":" + encode(x) for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(encode(x) for x in v) + "]"
    raise TypeError(f"cannot encode {type(v).__name__}")


def run(action, base, size_kib, worker, workers, param):
    if action == "cleanup-trial" and not os.path.basename(base).endswith("-all"):
        raise ValueError("cleanup-trial expects .../branchlab-<run>-<trial>-all")
    if action not in ACTIONS:
        raise ValueError("unrecognized action")
    if not 1 <= workers <= 4:
        raise ValueError("workers must be within 1..4")
    if not 1 <= size_kib <= 4096:
        raise ValueError("size_kib must be within 1..4096")
    start = time.perf_counter_ns()
    size, operations, valid, detail = 0, 1, True, ""
    j = lambda *parts: os.path.join(base, *parts)  # noqa: E731

    if action == "init":
        os.makedirs(os.path.dirname(base), exist_ok=True)
        os.mkdir(base)
        os.mkdir(j("workers"))
        for idx in range(workers):
            os.mkdir(j("workers", f"w-{idx}"))
        sync_dir(base)
        sync_dir(os.path.dirname(base))
        detail = "created isolated scratch directory"

    elif action == "write":
        data = payload(size_kib, 0)
        synced_write(j("payload.bin"), data)
        size, detail = len(data), sha(data)

    elif action == "read":
        got = read_file(j("payload.bin"))
        valid, size, detail = got == payload(size_kib, 0), len(got), sha(got)

    elif action == "sqlite-write":
        import sqlite3
        con = sqlite3.connect(j("test.sqlite"), timeout=10)
        try:
            # TRUNCATE, not the default DELETE: on Archil a later exec cannot unlink the rollback
            # journal in a directory an earlier exec created (DELETE and WAL fail; see sem-probe).
            con.execute("PRAGMA journal_mode=TRUNCATE")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("PRAGMA fullfsync=ON")  # macOS: commits reach stable storage; no-op on Linux
            con.execute("CREATE TABLE counters (id INTEGER PRIMARY KEY, amount INTEGER NOT NULL)")
            for idx in range(16):
                con.execute("INSERT INTO counters(id,amount) VALUES (?,?)", (idx + 1, idx))
                con.commit()
        finally:
            con.close()
        sync_dir(base)
        operations, detail = 16, "16 committed transactions"

    elif action == "sqlite-check":
        import sqlite3
        con = sqlite3.connect(f"file:{j('test.sqlite')}?mode=ro", uri=True, timeout=10)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            count, total = con.execute("SELECT COUNT(*), SUM(amount) FROM counters").fetchone()
            valid = integrity == "ok" and count == 16 and total == 120
            detail = f"integrity={integrity},rows={count},sum={total}"
        finally:
            con.close()

    elif action == "parallel-write":
        data = payload(size_kib, worker)
        synced_write(j("workers", f"w-{worker}", "payload.bin"), data)
        size, detail = len(data), f"worker={worker};sha256={sha(data)}"

    elif action == "parallel-check":
        operations = workers
        bad = []
        for idx in range(workers):
            try:
                got = read_file(j("workers", f"w-{idx}", "payload.bin"))
            except OSError as e:
                bad.append(f"w-{idx}: {type(e).__name__}")
                continue
            size += len(got)
            if got != payload(size_kib, idx):
                bad.append(f"w-{idx}: wrong bytes")
        valid = not bad
        detail = f"verified {workers} distinct worker files" if valid else "; ".join(bad)

    elif action == "sem-setup":
        os.mkdir(j("sem"))
        sem_files(j("sem", "plain"))
        sem_files(j("sem", "checkout"))
        detail = "created two identical entry sets for sem-probe"

    elif action in ("sem-probe", "sem-probe-checkout"):
        d = j("sem", "plain" if action == "sem-probe" else "checkout")
        checkout_ms, checkout = (None, "not requested")
        if action == "sem-probe-checkout":
            checkout_ms, checkout = archil_checkout(d)
        ops = sem_ops(d)
        operations = len(ops)
        # A probe records behaviour; it is "valid" when it ran. Outcomes are judged on the page.
        detail = {"ops": ops, "checkout": checkout, "checkout_ms": checkout_ms}

    elif action == "vis-setup":
        vis_setup(j("vis"))
        detail = "created one directory per access path"

    elif action == "vis-reader":
        if not 5 <= param <= 280:
            raise ValueError("visibility cap must be 5..280 s (exec limit is 300 s)")
        detail = vis_reader(j("vis"), param)
        operations = detail["polls"]

    elif action == "vis-writer":
        t_start = time.time()
        per_dir = vis_writer(j("vis"))
        detail = {"t_start": t_start, "t_fsynced": time.time(), "t_fsynced_by_dir": per_dir}

    elif action == "cleanup-trial":
        # The ONLY recursive delete: every sibling directory named branchlab-<run>-<trial>-<case>.
        # safe_base() has checked the path; the parent must exist, or the disk isn't mounted where
        # we expect and "nothing to remove" would be a false success.
        import shutil
        parent = os.path.dirname(base)
        if not os.path.isdir(parent):
            raise FileNotFoundError(f"scratch parent {parent} missing: is the disk mounted?")
        stem = os.path.basename(base)[:-len("all")]
        removed = []
        for name in os.listdir(parent):
            path = os.path.join(parent, name)
            if name.startswith(stem) and safe_base(path) and os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
                removed.append(name[len(stem):])
        operations = len(removed)
        detail = "removed " + (", ".join(sorted(removed)) or "nothing")

    elapsed = (time.perf_counter_ns() - start) / 1e6
    return {"valid": valid, "elapsed_ms": elapsed, "operations": operations,
            "bytes": size, "detail": detail}


def main():
    if len(sys.argv) not in (6, 7):
        raise ValueError("usage: guest_state.py ACTION ABSOLUTE_PATH SIZE_KIB WORKER WORKERS [PARAM]")
    action, raw, size, worker, workers = sys.argv[1:6]
    param = int(sys.argv[6]) if len(sys.argv) == 7 else 0
    result = run(action, safe_base(raw), int(size), int(worker), int(workers), param)
    print(MARKER + encode(result), flush=True)
    if not result["valid"]:
        raise RuntimeError("BranchLab correctness assertion failed")


if __name__ == "__main__":
    main()

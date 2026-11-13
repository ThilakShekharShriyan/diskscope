"""BranchLab fixture, stdlib-only. Every write stays in one scoped test directory.

This is invoked as a fresh process / fresh Archil microVM for every operation.
Nothing here deliberately kills a host, deletes user data, or creates disk branches.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

MARKER = "BRANCHLAB_JSON="
ACTIONS = {
    "init", "write", "read", "sqlite-write", "sqlite-check",
    "parallel-write", "parallel-check", "cleanup",
}


def safe_base(raw: str) -> Path:
    p = Path(raw)
    if (not p.is_absolute() or p.parent.name != ".diskscope"
            or not re.fullmatch(r"branchlab-[a-zA-Z0-9-]{8,90}", p.name)
            or ".." in p.parts):
        raise ValueError("unsafe fixture path: expected absolute .../.diskscope/branchlab-<id>")
    return p


def payload(size_kib: int, worker: int) -> bytes:
    if not 1 <= size_kib <= 4096:
        raise ValueError("size_kib must be within 1..4096")
    if not 0 <= worker <= 8:
        raise ValueError("worker must be 0..8")
    seed = hashlib.sha256(f"BranchLab-worker-{worker}".encode()).digest()
    return (seed * (size_kib * 1024 // len(seed) + 1))[:size_kib * 1024]


def sync_dir(p: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(p, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def synced_write(p: Path, data: bytes) -> None:
    # Uses 'xb' to avoid accidentally overwriting a previous successful trial.
    with p.open("xb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    sync_dir(p.parent)


def run(action: str, base: Path, size_kib: int, worker: int, workers: int) -> dict:
    if action not in ACTIONS:
        raise ValueError("unrecognized action")
    if not 1 <= workers <= 4:
        raise ValueError("workers must be within 1..4")
    if not 1 <= size_kib <= 4096:
        raise ValueError("size_kib must be within 1..4096")
    start = time.perf_counter_ns()
    size = 0
    operations = 1
    valid = True
    detail = ""

    if action == "init":
        base.parent.mkdir(parents=True, exist_ok=True)
        base.mkdir(exist_ok=False)
        (base / "workers").mkdir()
        for idx in range(workers):
            (base / "workers" / f"w-{idx}").mkdir()
        sync_dir(base)
        sync_dir(base.parent)
        detail = "created isolated scratch directory"

    elif action == "write":
        data = payload(size_kib, 0)
        synced_write(base / "payload.bin", data)
        size = len(data)
        detail = hashlib.sha256(data).hexdigest()

    elif action == "read":
        got = (base / "payload.bin").read_bytes()
        expected = payload(size_kib, 0)
        valid = got == expected
        size = len(got)
        detail = hashlib.sha256(got).hexdigest()

    elif action == "sqlite-write":
        con = sqlite3.connect(str(base / "test.sqlite"), timeout=10)
        try:
            # TRUNCATE, not the default DELETE: on Archil a later exec cannot unlink the rollback
            # journal in a directory an earlier exec created (DELETE and WAL fail; ARCHIL-FINDINGS.md #2).
            con.execute("PRAGMA journal_mode=TRUNCATE")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("CREATE TABLE counters (id INTEGER PRIMARY KEY, amount INTEGER NOT NULL)")
            for idx in range(16):
                con.execute("INSERT INTO counters(id,amount) VALUES (?,?)", (idx + 1, idx))
                con.commit()
        finally:
            con.close()
        sync_dir(base)
        operations = 16
        detail = "16 committed transactions"

    elif action == "sqlite-check":
        db_uri = (base / "test.sqlite").as_uri() + "?mode=ro"
        con = sqlite3.connect(db_uri, uri=True, timeout=10)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            count, total = con.execute("SELECT COUNT(*), SUM(amount) FROM counters").fetchone()
            valid = integrity == "ok" and count == 16 and total == 120
            detail = f"integrity={integrity},rows={count},sum={total}"
        finally:
            con.close()

    elif action == "parallel-write":
        data = payload(size_kib, worker)
        p = base / "workers" / f"w-{worker}" / "payload.bin"
        synced_write(p, data)
        size = len(data)
        detail = f"worker={worker};sha256={hashlib.sha256(data).hexdigest()}"

    elif action == "parallel-check":
        operations = workers
        for idx in range(workers):
            p = base / "workers" / f"w-{idx}" / "payload.bin"
            got = p.read_bytes()
            valid = valid and got == payload(size_kib, idx)
            size += len(got)
        detail = f"verified {workers} distinct worker files"

    elif action == "cleanup":
        # This is the ONLY recursive delete. Strict safe_base() checks the path.
        if base.exists():
            shutil.rmtree(base)
        detail = "removed scoped test directory only"

    elapsed = (time.perf_counter_ns() - start) / 1e6
    return {
        "valid": valid,
        "elapsed_ms": elapsed,
        "operations": operations,
        "bytes": size,
        "detail": detail,
    }


def main() -> None:
    if len(sys.argv) != 6:
        raise ValueError("usage: guest_state.py ACTION ABSOLUTE_PATH SIZE_KIB WORKER WORKERS")
    action, raw, size, worker, workers = sys.argv[1:]
    base = safe_base(raw)
    result = run(action, base, int(size), int(worker), int(workers))
    print(MARKER + json.dumps(result, separators=(",", ":")), flush=True)
    if not result["valid"]:
        raise RuntimeError("BranchLab correctness assertion failed")


if __name__ == "__main__":
    main()

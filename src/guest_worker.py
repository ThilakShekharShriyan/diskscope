"""Standard-library-only microbenchmark payload for Archil's Python guest image.

Temporary files are *only* created below caller supplied scoped benchmark root.
This is deliberately not a correctness/fault-injection benchmark.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

TASKS = {"noop", "stat", "small-files", "fsync-4k", "seq-write", "seq-read", "sqlite-commit", "hash-64m"}
MARKER = "DISKSCOPE_JSON="


def durable_fsync(fd: int) -> None:
    """Flush to stable storage. On macOS, fsync() only hands data to the drive, whose write cache
    may still lose it on power failure; F_FULLFSYNC forces the drive to flush. Archil documents data
    as durable across AZs once fsync returns, so a fair local baseline must pay for real durability."""
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fd)


def perform(task: str, base: Path, size_mib: int) -> dict:
    if task not in TASKS:
        raise ValueError("Unrecognized benchmark task")
    if not (1 <= size_mib <= 64):
        raise ValueError("size_mib must be between 1 and 64")
    if task == "noop":
        return {"elapsed_ms": 0.0, "operations": 1, "bytes": 0, "valid": True}
    if task == "stat":
        start = time.perf_counter_ns()
        root = base
        while root.name != ".diskscope" and root != root.parent:
            root = root.parent
        stat = root.parent.stat()
        elapsed = (time.perf_counter_ns() - start) / 1e6
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": 0, "valid": stat.st_ino > 0}
    if task == "hash-64m":
        data = b"disk-scope-" * 1024
        start = time.perf_counter_ns()
        digest = hashlib.sha256()
        for _ in range(64 * 1024 * 1024 // len(data)):
            digest.update(data)
        result = digest.hexdigest()
        elapsed = (time.perf_counter_ns() - start) / 1e6
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": 64 * 1024 * 1024, "valid": len(result) == 64}

    # The scope is unique to this task and run; never delete anything else.
    base.mkdir(parents=True, exist_ok=False)
    try:
        return _with_cleanup(_io_task(task, base, size_mib), base)
    except BaseException:
        _cleanup(base)
        raise


def _cleanup(base: Path):
    """Remove this task's scoped directory. Returns an error string instead of raising,
    so a cleanup failure (observed on Archil: EROFS on rmdir after fsync) does not
    discard a completed measurement. Cleanup problems are reported separately."""
    try:
        shutil.rmtree(base)
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: errno={exc.errno} {exc.strerror} ({exc.filename})"


def _with_cleanup(result: dict, base: Path) -> dict:
    error = _cleanup(base)
    if error:
        result["cleanup_error"] = error
    return result


def _io_task(task: str, base: Path, size_mib: int) -> dict:
    if task == "small-files":
        start = time.perf_counter_ns()
        for index in range(64):
            (base / f"file-{index:03d}.txt").write_bytes(b"x" * 16)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        valid = sum(1 for _ in base.iterdir()) == 64
        return {"elapsed_ms": elapsed, "operations": 64, "bytes": 1024, "valid": valid}
    if task == "fsync-4k":
        start = time.perf_counter_ns()
        with (base / "sync.bin").open("wb") as out:
            out.write(b"a" * 4096)
            out.flush()
            durable_fsync(out.fileno())
        elapsed = (time.perf_counter_ns() - start) / 1e6
        valid = (base / "sync.bin").stat().st_size == 4096
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": 4096, "valid": valid}
    if task in ("seq-write", "seq-read"):
        file = base / "blob.bin"
        target_bytes = size_mib * 1024 * 1024
        chunk = b"z" * (64 * 1024)
        if task == "seq-read":
            with file.open("wb") as out:
                for _ in range(target_bytes // len(chunk)):
                    out.write(chunk)
                out.flush()
                durable_fsync(out.fileno())
            start = time.perf_counter_ns()
            amount = 0
            with file.open("rb") as reader:
                while block := reader.read(64 * 1024):
                    amount += len(block)
            elapsed = (time.perf_counter_ns() - start) / 1e6
            return {"elapsed_ms": elapsed, "operations": 1, "bytes": amount, "valid": amount == target_bytes}
        start = time.perf_counter_ns()
        with file.open("wb") as out:
            for _ in range(target_bytes // len(chunk)):
                out.write(chunk)
            out.flush()
            durable_fsync(out.fileno())
        elapsed = (time.perf_counter_ns() - start) / 1e6
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": target_bytes, "valid": file.stat().st_size == target_bytes}
    if task == "sqlite-commit":
        db = sqlite3.connect(str(base / "test.db"), timeout=5)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA fullfsync=ON")  # macOS: use F_FULLFSYNC; ignored elsewhere
            db.execute("CREATE TABLE x (v INTEGER NOT NULL)")
            db.commit()
            start = time.perf_counter_ns()
            for n in range(25):
                db.execute("INSERT INTO x(v) VALUES (?)", (n,))
                db.commit()
            elapsed = (time.perf_counter_ns() - start) / 1e6
            count = db.execute("SELECT COUNT(*) FROM x").fetchone()[0]
            return {"elapsed_ms": elapsed, "operations": 25, "bytes": 0, "valid": count == 25}
        finally:
            db.close()
    raise RuntimeError("unreachable")


def main() -> None:
    if len(sys.argv) != 4:
        raise ValueError("usage: guest_worker.py TASK PATH SIZE_MIB")
    task, path, size_text = sys.argv[1:]
    scoped = Path(path)
    # Safety rule for remote execution: it must live under our dedicated directory.
    # Local test passes a /tmp/.../.diskscope/... directory with the same structure.
    if ".diskscope" not in scoped.parts or len(scoped.parts) < 5:
        raise ValueError("Refusing to run outside scoped .diskscope directory")
    output = perform(task, scoped, int(size_text))
    print(MARKER + json.dumps(output, separators=(",", ":")), flush=True)
    if not output["valid"]:
        raise RuntimeError("Post-benchmark validation failed")


if __name__ == "__main__":
    main()

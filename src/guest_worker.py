"""Standard-library-only microbenchmark payload for DiskScope (runs locally and inside Archil microVMs).

Temporary files are *only* created below the `.diskscope` directory named by the caller's scoped path
(`<root>/.diskscope/<run>/<task>`). This is deliberately not a correctness/fault-injection benchmark.

Layout: every call creates its own directory and removes it afterwards. A default Archil exec mount
holds write delegations only on what that exec creates, so removing the per-call directory (whose parent
came from an earlier exec) fails with EROFS. The files are still deleted; the leftover empty directory is
reported as `cleanup_error` (never a failed measurement), and the Rust runner removes the whole run
directory at the end with one `conditional` mount.

Start-up cost matters here: every Archil exec is a fresh VM with a cold page cache, where importing
sqlite3 + hashlib + pathlib + json (-> re) up front measured ~135 ms. So this module imports only
builtins at the top, imports each task's dependencies inside that task, and avoids json/pathlib on
the hot path. Run it with `python3 -S` to also skip the `site` module.
"""
import os
import sys
import time

TASKS = ("noop", "stat", "small-files", "fsync-4k", "seq-write", "seq-read", "sqlite-commit", "hash-64m")
MARKER = "DISKSCOPE_JSON="
SCOPE_DIR = ".diskscope"


def durable_fsync(fd: int) -> None:
    """Flush to stable storage. On macOS, fsync() only hands data to the drive, whose write cache
    may still lose it on power failure; F_FULLFSYNC forces the drive to flush. Archil documents data
    as durable once fsync returns, so a fair local baseline must pay for real durability."""
    import fcntl
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fd)


def _ms_since(start_ns: int) -> float:
    return (time.perf_counter_ns() - start_ns) / 1e6


def perform(task: str, base: str, size_mib: int) -> dict:
    if task not in TASKS:
        raise ValueError("Unrecognized benchmark task")
    if not (1 <= size_mib <= 64):
        raise ValueError("size_mib must be between 1 and 64")
    if task == "noop":
        return {"elapsed_ms": 0.0, "operations": 1, "bytes": 0, "valid": True}
    if task == "stat":
        # stat() the directory that contains .diskscope (the mount root on Archil).
        head, tail = base, ""
        while tail != SCOPE_DIR and head not in ("", os.sep):
            head, tail = os.path.split(head)
        target = os.path.dirname(os.path.join(head, tail)) if tail == SCOPE_DIR else os.sep
        start = time.perf_counter_ns()
        st = os.stat(target)
        elapsed = _ms_since(start)
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": 0, "valid": st.st_ino > 0}
    if task == "hash-64m":
        import hashlib
        data = b"disk-scope-" * 1024
        start = time.perf_counter_ns()
        digest = hashlib.sha256()
        for _ in range(64 * 1024 * 1024 // len(data)):
            digest.update(data)
        result = digest.hexdigest()
        elapsed = _ms_since(start)
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": 64 * 1024 * 1024, "valid": len(result) == 64}

    os.makedirs(base, exist_ok=False)
    try:
        result = _io_task(task, base, size_mib)
    except BaseException:
        _remove_tree(base)
        raise
    error = _remove_tree(base)
    if error:
        result["cleanup_error"] = error
    return result


def _remove_tree(path: str):
    """Remove a per-call directory. Returns an error string instead of raising, so a cleanup failure
    does not discard a completed measurement."""
    import shutil
    try:
        shutil.rmtree(path)
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: errno={exc.errno} {exc.strerror} ({exc.filename})"


def _io_task(task: str, base: str, size_mib: int) -> dict:
    if task == "small-files":
        payload = b"x" * 16
        start = time.perf_counter_ns()
        for index in range(64):
            with open(os.path.join(base, f"file-{index:03d}.txt"), "wb") as out:
                out.write(payload)
        elapsed = _ms_since(start)
        valid = len(os.listdir(base)) == 64
        return {"elapsed_ms": elapsed, "operations": 64, "bytes": 1024, "valid": valid}
    if task == "fsync-4k":
        path = os.path.join(base, "sync.bin")
        start = time.perf_counter_ns()
        with open(path, "wb") as out:
            out.write(b"a" * 4096)
            out.flush()
            durable_fsync(out.fileno())
        elapsed = _ms_since(start)
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": 4096, "valid": os.stat(path).st_size == 4096}
    if task in ("seq-write", "seq-read"):
        path = os.path.join(base, "blob.bin")
        target_bytes = size_mib * 1024 * 1024
        chunk = b"z" * (64 * 1024)
        if task == "seq-read":
            with open(path, "wb") as out:  # untimed fixture
                for _ in range(target_bytes // len(chunk)):
                    out.write(chunk)
                out.flush()
                durable_fsync(out.fileno())
            start = time.perf_counter_ns()
            amount = 0
            with open(path, "rb") as reader:
                while True:
                    block = reader.read(64 * 1024)
                    if not block:
                        break
                    amount += len(block)
            elapsed = _ms_since(start)
            return {"elapsed_ms": elapsed, "operations": 1, "bytes": amount, "valid": amount == target_bytes}
        start = time.perf_counter_ns()
        with open(path, "wb") as out:
            for _ in range(target_bytes // len(chunk)):
                out.write(chunk)
            out.flush()
            durable_fsync(out.fileno())
        elapsed = _ms_since(start)
        return {"elapsed_ms": elapsed, "operations": 1, "bytes": target_bytes, "valid": os.stat(path).st_size == target_bytes}
    if task == "sqlite-commit":
        import sqlite3
        db = sqlite3.connect(os.path.join(base, "test.db"), timeout=5)
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
            elapsed = _ms_since(start)
            count = db.execute("SELECT COUNT(*) FROM x").fetchone()[0]
            return {"elapsed_ms": elapsed, "operations": 25, "bytes": 0, "valid": count == 25}
        finally:
            db.close()
    raise RuntimeError("unreachable")


def scoped_path(path: str) -> str:
    """Validate the scratch path: absolute, no '..' components, and inside a .diskscope/<run>/<task>
    directory. Refuses anything else before a single file is touched."""
    if not os.path.isabs(path):
        raise ValueError("Refusing a relative scratch path")
    parts = path.split(os.sep)
    if ".." in parts or "." in parts:
        raise ValueError("Refusing a scratch path with '.' or '..' components")
    norm = os.path.normpath(path)
    norm_parts = norm.split(os.sep)
    if SCOPE_DIR not in norm_parts or len(norm_parts) - norm_parts.index(SCOPE_DIR) < 3:
        raise ValueError("Refusing to run outside a scoped .diskscope/<run>/<task> directory")
    return norm


def encode(output: dict) -> str:
    """Tiny JSON encoder for this fixed schema (numbers, bools, optional string), so the hot path
    never imports json (and, through it, re)."""
    fields = []
    for key, value in output.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (int, float)):
            text = repr(float(value)) if isinstance(value, float) else str(value)
        else:
            import json
            text = json.dumps(str(value))
        fields.append(f'"{key}":{text}')
    return "{" + ",".join(fields) + "}"


ERROR_MARKER = "DISKSCOPE_ERROR="


def serve() -> None:
    """Persistent-worker mode for a warm sandbox: read `TASK PATH SIZE_MIB` lines from stdin and answer
    each with one DISKSCOPE_JSON (or DISKSCOPE_ERROR) line, so a single long-lived process and connection
    serve every task — no per-task VM, interpreter start, or connection setup."""
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        try:
            task, path, size_text = line.split()
            output = perform(task, scoped_path(path), int(size_text))
            print(MARKER + encode(output), flush=True)
        except Exception as exc:  # report and keep serving
            print(ERROR_MARKER + f"{type(exc).__name__}: {exc}".replace("\n", " "), flush=True)


def main() -> None:
    if sys.argv[1:] == ["--serve"]:
        serve()
        return
    if len(sys.argv) != 4:
        raise ValueError("usage: guest_worker.py TASK PATH SIZE_MIB | --serve")
    task, path, size_text = sys.argv[1:]
    output = perform(task, scoped_path(path), int(size_text))
    print(MARKER + encode(output), flush=True)
    if not output["valid"]:
        raise RuntimeError("Post-benchmark validation failed")


if __name__ == "__main__":
    main()

import errno
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "guest_worker.py"
TASKS = ["noop", "stat", "small-files", "fsync-4k", "seq-write", "seq-read", "sqlite-commit", "hash-64m"]


def load_worker():
    spec = importlib.util.spec_from_file_location("guest_worker", SCRIPT)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


def run(task, path, *flags):
    return subprocess.run([sys.executable, *flags, str(SCRIPT), task, str(path), "1"], capture_output=True, text=True)


class GuestTests(unittest.TestCase):
    def test_all_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / ".diskscope" / "test-scope"
            base.mkdir(parents=True)
            for task in TASKS:
                with self.subTest(task=task):
                    path = base / f"{task}-0-1"
                    result = run(task, path, "-S")  # same flags as remote execution
                    self.assertEqual(result.returncode, 0, result.stderr)
                    marker = next(line[len("DISKSCOPE_JSON="):] for line in result.stdout.splitlines() if line.startswith("DISKSCOPE_JSON="))
                    payload = json.loads(marker)
                    self.assertTrue(payload["valid"])
                    self.assertGreaterEqual(payload["elapsed_ms"], 0)
                    self.assertFalse(path.exists(), "Per-call directory should have been removed")

    def test_refuses_unscoped_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run("fsync-4k", Path(tmp) / "forbidden")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(tmp) / "forbidden").exists())

    def test_refuses_traversal_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            escape = f"{tmp}/.diskscope/run/../../escaped"
            for bad in (escape, ".diskscope/run/task", f"{tmp}/.diskscope/./task", f"{tmp}/.diskscope/run-only"):
                with self.subTest(path=bad):
                    result = run("small-files", bad)
                    self.assertNotEqual(result.returncode, 0)
            self.assertFalse(Path(tmp, "escaped").exists())

    def test_hot_path_avoids_heavy_imports(self):
        # noop must not import json/re/pathlib/sqlite3/hashlib/shutil: they cost ~130 ms cold in a fresh VM.
        heavy = {"json", "re", "pathlib", "sqlite3", "hashlib", "shutil"}
        with tempfile.TemporaryDirectory() as tmp:
            result = run("noop", f"{tmp}/.diskscope/r/noop-0-1", "-S", "-X", "importtime")
            self.assertEqual(result.returncode, 0, result.stderr)
            imported = {line.rsplit("|", 1)[-1].strip() for line in result.stderr.splitlines() if line.startswith("import time:")}
            self.assertFalse(heavy & imported, f"hot path imported {sorted(heavy & imported)}")

    def test_encoder_matches_json(self):
        worker = load_worker()
        for out in ({"elapsed_ms": 1.25, "operations": 64, "bytes": 0, "valid": True},
                    {"elapsed_ms": 1e-05, "operations": 1, "bytes": 4096, "valid": False, "cleanup_error": 'OSError: "quoted" \\ path'}):
            self.assertEqual(json.loads(worker.encode(out)), out)

    def test_cleanup_failure_keeps_measurement(self):
        # On Archil a cleanup step can fail (EROFS); the measurement must survive and the
        # cleanup problem must be reported separately rather than failing the task.
        worker = load_worker()
        def erofs(path, *a, **k): raise OSError(errno.EROFS, "Read-only file system", str(path))
        with tempfile.TemporaryDirectory() as tmp:
            base = str(Path(tmp) / ".diskscope" / "scope" / "fsync-4k-0-1")
            with mock.patch("shutil.rmtree", erofs):
                out = worker.perform("fsync-4k", base, 1)
            self.assertTrue(out["valid"])
            self.assertIn("errno=30", out["cleanup_error"])


    def test_serve_mode_answers_each_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = f"noop {tmp}/.diskscope/r/noop-0-1 1\nfsync-4k {tmp}/.diskscope/r/fsync-4k-0-1 1\nfsync-4k relative/path 1\n"
            result = subprocess.run([sys.executable, "-S", str(SCRIPT), "--serve"], input=lines, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = result.stdout.splitlines()
            self.assertEqual(len(out), 3)
            self.assertTrue(json.loads(out[1][len("DISKSCOPE_JSON="):])["valid"])
            self.assertTrue(out[2].startswith("DISKSCOPE_ERROR=ValueError"))

if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "guest_worker.py"
TASKS = ["noop", "stat", "small-files", "fsync-4k", "seq-write", "seq-read", "sqlite-commit", "hash-64m"]

class GuestTests(unittest.TestCase):
    def test_all_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / ".diskscope" / "test-scope"
            base.mkdir(parents=True)
            for task in TASKS:
                with self.subTest(task=task):
                    path = base / f"{task}-0-1"
                    result = subprocess.run([sys.executable, str(SCRIPT), task, str(path), "1"], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    marker = next(line.removeprefix("DISKSCOPE_JSON=") for line in result.stdout.splitlines() if line.startswith("DISKSCOPE_JSON="))
                    payload = json.loads(marker)
                    self.assertTrue(payload["valid"])
                    self.assertGreaterEqual(payload["elapsed_ms"], 0)
                    self.assertFalse(path.exists(), "Scoped test files should have been cleaned up")

    def test_refuses_unscoped_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(SCRIPT), "fsync-4k", str(Path(tmp)/"forbidden"), "1"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(tmp)/"forbidden").exists())

    def test_cleanup_failure_keeps_measurement(self):
        # Archil returned EROFS on rmdir after fsync; the measurement must survive and the
        # cleanup problem must be reported separately rather than failing the task.
        import importlib.util, errno
        from unittest import mock
        spec = importlib.util.spec_from_file_location("guest_worker", SCRIPT)
        worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
        def erofs(path, *a, **k): raise OSError(errno.EROFS, "Read-only file system", str(path))
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / ".diskscope" / "scope" / "fsync-4k-0-1"
            with mock.patch.object(worker.shutil, "rmtree", erofs):
                out = worker.perform("fsync-4k", base, 1)
            self.assertTrue(out["valid"])
            self.assertIn("errno=30", out["cleanup_error"])

if __name__ == "__main__": unittest.main()

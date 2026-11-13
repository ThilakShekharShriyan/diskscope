import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "guest_state.py"
spec = importlib.util.spec_from_file_location("guest_state", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TestFixture(unittest.TestCase):
    def test_guard_rejects_unscoped_delete(self):
        for path in ["/tmp", "/tmp/branchlab-test", "/tmp/.diskscope/bad", "/tmp/.diskscope/branchlab-x"]:
            with self.assertRaises(ValueError):
                module.safe_base(path)

    def test_state_and_sqlite_survive_separate_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / ".diskscope" / "branchlab-abcdef123456"
            for action, worker in [
                ("init", 0), ("write", 0), ("read", 0),
                ("sqlite-write", 0), ("sqlite-check", 0),
                ("parallel-write", 0), ("parallel-write", 1),
                ("parallel-check", 0), ("cleanup", 0),
            ]:
                child = subprocess.run([sys.executable, str(SOURCE), action, str(p), "4", str(worker), "2"], capture_output=True, text=True)
                self.assertEqual(child.returncode, 0, f"{action}: {child.stderr}")
                self.assertIn(module.MARKER, child.stdout)
            self.assertFalse(p.exists())

    def test_rejects_invalid_worker_index(self):
        with self.assertRaises(ValueError):
            module.payload(4, 100)


if __name__ == "__main__":
    unittest.main()

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
                ("parallel-check", 0), ("sem-setup", 0), ("sem-probe", 0),
                ("sem-probe-checkout", 0), ("vis-setup", 0), ("vis-writer", 0), ("cleanup", 0),
            ]:
                child = subprocess.run([sys.executable, "-S", str(SOURCE), action, str(p), "4", str(worker), "2"], capture_output=True, text=True)
                self.assertEqual(child.returncode, 0, f"{action}: {child.stderr}")
                self.assertIn(module.MARKER, child.stdout)
            self.assertFalse(p.exists())

    def test_rejects_invalid_worker_index(self):
        with self.assertRaises(ValueError):
            module.payload(4, 100)


    def run_guest(self, *args):
        return subprocess.run([sys.executable, "-S", str(SOURCE), *map(str, args)], capture_output=True, text=True)

    def result(self, child):
        import json
        line = next(x for x in child.stdout.splitlines() if x.startswith(module.MARKER))
        return json.loads(line[len(module.MARKER):])

    def test_semantics_on_a_local_disk_allows_everything(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / ".diskscope" / "branchlab-abcdef123456"
            for action in ("init", "sem-setup"):
                self.assertEqual(self.run_guest(action, p, 4, 0, 1).returncode, 0)
            ops = self.result(self.run_guest("sem-probe", p, 4, 0, 1))["detail"]["ops"]
            self.assertEqual(len(ops), 10)
            self.assertTrue(all(v == "ok" for v in ops.values()), ops)

    def test_visibility_reader_sees_a_concurrent_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / ".diskscope" / "branchlab-abcdef123456"
            for action in ("init", "vis-setup"):
                self.assertEqual(self.run_guest(action, p, 4, 0, 1).returncode, 0)
            reader = subprocess.Popen([sys.executable, "-S", str(SOURCE), "vis-reader", str(p), "4", "0", "1", "10"],
                                      stdout=subprocess.PIPE, text=True)
            import time
            time.sleep(1.0)
            w = self.result(self.run_guest("vis-writer", p, 4, 0, 1))["detail"]
            out, _ = reader.communicate(timeout=20)
            r = self.result(subprocess.CompletedProcess([], 0, out, ""))["detail"]
            self.assertLess(r["t_prime"], w["t_start"])
            self.assertTrue(all(t is not None and t >= w["t_start"] for t in r["first_seen"].values()), r)

    def test_visibility_cap_is_bounded(self):
        with self.assertRaises(ValueError):
            module.run("vis-reader", "/x/.diskscope/branchlab-abcdefgh", 4, 0, 1, 900)


if __name__ == "__main__":
    unittest.main()

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
            p = Path(temp) / ".diskscope" / "branchlab-run12345-0-handoff"
            for action, worker in [
                ("init", 0), ("write", 0), ("read", 0),
                ("sqlite-write", 0), ("sqlite-check", 0),
                ("parallel-write", 0), ("parallel-write", 1),
                ("parallel-check", 0), ("sem-setup", 0), ("sem-probe", 0),
                ("sem-probe-checkout", 0), ("vis-setup", 0), ("vis-writer", 0),
            ]:
                child = subprocess.run([sys.executable, "-S", str(SOURCE), action, str(p), "4", str(worker), "2"], capture_output=True, text=True)
                self.assertEqual(child.returncode, 0, f"{action}: {child.stderr}")
                self.assertIn(module.MARKER, child.stdout)
            child = self.run_guest_cleanup(p.parent / "branchlab-run12345-0-all")
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertFalse(p.exists())

    def test_rejects_invalid_worker_index(self):
        with self.assertRaises(ValueError):
            module.payload(4, 100)


    def run_guest_cleanup(self, base):
        return subprocess.run([sys.executable, "-S", str(SOURCE), "cleanup-trial", str(base), "4", "0", "1"],
                              capture_output=True, text=True)

    def test_trial_cleanup_is_scoped_and_fails_without_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp) / ".diskscope"
            keep = [parent / "branchlab-run12345-1-sqlite", parent / "branchlab-otherrun-0-sqlite", parent / "unrelated"]
            gone = [parent / "branchlab-run12345-0-sqlite", parent / "branchlab-run12345-0-writers"]
            for d in keep + gone:
                d.mkdir(parents=True)
            self.assertEqual(self.run_guest_cleanup(parent / "branchlab-run12345-0-all").returncode, 0)
            self.assertTrue(all(d.exists() for d in keep))
            self.assertFalse(any(d.exists() for d in gone))
            missing = Path(temp) / "nomount" / ".diskscope" / "branchlab-run12345-0-all"
            self.assertNotEqual(self.run_guest_cleanup(missing).returncode, 0)

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

    def test_encoder_matches_json(self):
        import json
        for v in [{"a": 1, "b": [1.5, None, True, False], "c": 'q"\\\n\t\u00b7 ·', "d": {"e": "ENOENT"}}, [], {}, 0, -2.25e-7]:
            self.assertEqual(json.loads(module.encode(v)), v)
        self.assertEqual(module.encode(float("nan")), "null")

    def test_hot_path_avoids_slow_imports(self):
        # re and json each cost ~15-30 ms in a cold microVM; the guest must not import them.
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / ".diskscope" / "branchlab-abcdef123456"
            for action in ("init", "write", "read"):
                child = subprocess.run([sys.executable, "-S", "-X", "importtime", str(SOURCE), action, str(p), "4", "0", "1"],
                                       capture_output=True, text=True)
                self.assertEqual(child.returncode, 0, child.stderr)
                imported = {line.split("|")[-1].strip() for line in child.stderr.splitlines() if "|" in line}
                self.assertFalse(imported & {"re", "json"}, f"{action} imported {imported & {'re', 'json'}}")

    def test_visibility_cap_is_bounded(self):
        with self.assertRaises(ValueError):
            module.run("vis-reader", "/x/.diskscope/branchlab-abcdefgh", 4, 0, 1, 900)


if __name__ == "__main__":
    unittest.main()

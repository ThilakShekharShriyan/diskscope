import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from agentlab import harness, incidents, prepare, tools  # noqa: E402


class HarnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        for i in ("erofs-a", "quiet-plain"):
            prepare.prepare(incidents.ALL[i], cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_incident_is_well_formed(self):
        ids = [i["id"] for i in incidents.INCIDENTS + incidents.HELD_OUT]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(incidents.INCIDENTS), 20)
        for inc in incidents.INCIDENTS + incidents.HELD_OUT:
            self.assertIn(inc["truth"]["cause"], incidents.CAUSES, inc["id"])
            for c in inc["truth"]["components"]:
                self.assertIn(c, incidents.COMPONENTS, inc["id"])

    def test_mock_runs_every_arm_and_grades(self):
        backend = tools.LocalBackend(self.root, allow_shell=True)
        for arm in tools.ARMS:
            o = harness.run_one("quiet-plain", arm, harness.MockModel(arm), backend)
            self.assertTrue(o["detail"]["grade"]["submitted"], arm)
            self.assertTrue(o["ok"], arm)  # mock always answers no_fault
            self.assertEqual(o["operations"], 2)
            o = harness.run_one("erofs-a", arm, harness.MockModel(arm), backend)
            self.assertFalse(o["ok"])

    def test_retrieval_is_graded_from_tool_outputs(self):
        backend = tools.LocalBackend(self.root, allow_shell=True)
        o = harness.run_one("erofs-a", "files", harness.MockModel("files"), backend)
        self.assertTrue(o["detail"]["grade"]["retrieved"])  # mock greps ERROR lines

    def test_live_model_cannot_use_local_shell(self):
        backend = tools.LocalBackend(self.root, allow_shell=False)
        out, _ = backend.call("erofs-a", "files", "shell", {"command": "echo hi"})
        self.assertIn("disabled", out)

    def test_sql_is_read_only(self):
        backend = tools.LocalBackend(self.root)
        out, _ = backend.call("erofs-a", "sql", "query", {"sql": "DELETE FROM logs"})
        self.assertIn("error", out)

    def test_output_cap_applies(self):
        backend = tools.LocalBackend(self.root)
        out, _ = backend.call("erofs-a", "sql", "query", {"sql": "SELECT * FROM logs"})
        self.assertIn("[truncated:", out)
        self.assertLess(len(out), tools.OUTPUT_CAP + 100)

    def test_trace_written(self):
        backend = tools.LocalBackend(self.root)
        with tempfile.TemporaryDirectory() as d:
            harness.run_one("erofs-a", "digest", harness.MockModel("digest"), backend, trace_dir=d)
            lines = [json.loads(l) for l in open(os.path.join(d, os.listdir(d)[0]))]
        ops = [l["attrs"]["gen_ai.operation.name"] for l in lines]
        self.assertEqual(ops[0], "invoke_agent")
        self.assertIn("execute_tool", ops)

    def test_no_path_or_output_names_the_incident(self):
        backend = tools.LocalBackend(self.root, allow_shell=True)
        probes = [("files", "shell", {"command": "pwd; ls -R .. | head -50; find / -maxdepth 0"}),
                  ("sql", "query", {"sql": "PRAGMA database_list"}),
                  ("digest", "get_digest", {}), ("digest", "drill", {"id": "E1"})]
        names = [i["id"] for i in incidents.INCIDENTS + incidents.HELD_OUT] + ["ho-"]
        for inc in ("erofs-a", "quiet-plain"):
            for arm, tool, args in probes:
                out, _ = backend.call(inc, arm, tool, args)
                leaked = [n for n in names if n in out]
                self.assertEqual(leaked, [], (inc, arm, out[:300]))
        self.assertFalse(any(n in os.listdir(os.path.join(self.root, "files")) for n in names))

    def test_markers_not_in_shared_text(self):
        from agentlab import bundle
        shared = tools.SYSTEM + json.dumps(tools.ARM_TOOLS) + json.dumps(tools.SUBMIT) + bundle.SCHEMA_README
        for inc in incidents.INCIDENTS + incidents.HELD_OUT:
            for m in inc["evidence"]:
                self.assertNotIn(m, shared, (inc["id"], m))

    def test_arm_dirs_differ_and_escape_refused(self):
        from agentlab import prepare as P, tools as T
        dirs = {a: P.opaque("erofs-a", arm=a) for a in ("files", "sql", "digest", "grafana")}
        self.assertEqual(len(set(dirs.values())), 4)
        b = T.LocalBackend("/nonexistent", allow_shell=True)
        for cmd in ("cat ../../digest/*/digest.txt", "ls /mnt/archil", "cat ~/x", "/bin/ls"):
            out, meta = b.call("erofs-a", "files", "shell", {"command": cmd})
            self.assertTrue(meta.get("refused"), cmd)
            self.assertIn("must stay in the telemetry directory", out)

    def test_wander_flag(self):
        self.assertTrue(harness._wandered("files", {"command": "cat ../../digest/*/digest.txt"}))
        self.assertFalse(harness._wandered("files", {"command": "grep ERROR logs.jsonl | head"}))

    def test_cost(self):
        self.assertAlmostEqual(harness.cost_usd("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 0,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}), 4.0)


if __name__ == "__main__":
    unittest.main()


class EstimateTest(unittest.TestCase):
    def test_estimate_uses_pilot_costs(self):
        import contextlib
        import io
        from agentlab.__main__ import main
        rep = {"observations": [{"detail": {"arm": "sql", "usd": u}} for u in (0.10, 0.20, 0.40)]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(rep, f)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["estimate", "--pilot", f.name, "--incidents", "erofs-a,quiet-plain", "--trials", "3"])
        os.unlink(f.name)
        self.assertIn("6 runs: $1.20 (median) .. $2.40 (max)", buf.getvalue())

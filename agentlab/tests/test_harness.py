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

    def test_cost(self):
        self.assertAlmostEqual(harness.cost_usd("claude-opus-5-5", {"input_tokens": 1_000_000, "output_tokens": 0,
                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}), 4.0)


if __name__ == "__main__":
    unittest.main()

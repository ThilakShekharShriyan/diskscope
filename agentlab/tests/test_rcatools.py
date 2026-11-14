"""rcatools arm: RCAEval's agent tools. Stdlib tests always run; the end-to-end tests need
pandas + pyarrow + RCAEval (the venv) and are skipped otherwise."""
import json
import os
import random
import tempfile
import unittest

from agentlab import harness, rcatools, report, tools

try:
    import pandas  # noqa: F401
    import pyarrow  # noqa: F401
    from RCAEval.agent import toolkit as rc_toolkit
    HAVE_RCAEVAL = True
except Exception:  # noqa: BLE001
    HAVE_RCAEVAL = False

CASE = "re2ob_fooservice_cpu_1"
INC = "rcaeval:" + CASE


class ArmTest(unittest.TestCase):
    def test_registered_with_their_schemas(self):
        self.assertIn("rcatools", tools.ARMS)
        ts = harness.arm_tools("rcatools", harness.task_spec("rcaeval:re2ob_checkoutservice_cpu_1")["tool_text"])
        self.assertEqual([t["name"] for t in ts], rcatools.TOOL_NAMES)
        self.assertNotIn("submit", rcatools.TOOL_NAMES)  # the suite's submit_answer replaces it
        for t, ref in zip(ts, rcatools.RCAEVAL_TOOLS):
            self.assertEqual((t["description"], t["input_schema"]), (ref["description"], ref["parameters"]))
            self.assertIs(t["strict"], False)

    def test_openai_strict_follows_tool(self):
        out = harness.OpenAIModel._tools(tools.ARM_TOOLS["rcatools"] + tools.ARM_TOOLS["sql"])
        self.assertEqual({o["name"]: o["strict"] for o in out}["describe_case"], False)
        self.assertEqual({o["name"]: o["strict"] for o in out}["query"], True)

    def test_view_id_opaque_and_variant_free(self):
        v = rcatools.view_id(INC)
        self.assertEqual(v, rcatools.view_id("rcaeval+t:" + CASE))
        self.assertNotIn("foo", v)
        self.assertNotEqual(v, rcatools.view_id("rcaeval:re2ob_barservice_cpu_1"))

    def test_inspected_root(self):
        c = lambda **kw: {"input": kw}
        self.assertTrue(rcatools.inspected_root("rcatools", [c(name="cartservice_cpu")], "cartservice"))
        self.assertTrue(rcatools.inspected_root("rcatools", [c(service="CartService ")], "cartservice"))
        self.assertFalse(rcatools.inspected_root("rcatools", [c(pattern="cartservice")], "cartservice"))
        self.assertFalse(rcatools.inspected_root("rcatools", [c(service="frontend-external")], "frontend"))
        self.assertTrue(rcatools.inspected_root("sql", [c(sql="select * from metrics where series='frontend_cpu'")], "frontend"))
        self.assertFalse(rcatools.inspected_root("sql", [c(sql="where series like 'frontend-external%'")], "frontend"))
        self.assertTrue(rcatools.inspected_root("files", [c(command="grep -c redis logs.jsonl")], "redis"))
        self.assertIsNone(rcatools.inspected_root("digest", [c(id="M1")], "redis"))

    def test_report_reads_json_results(self):
        self.assertTrue(report._is_err('{"error": "unknown metric \'x\'"}'))
        self.assertFalse(report._is_empty('{"name": "a_cpu", "series": [[1, 2.0]]}'))
        self.assertTrue(report._is_empty('{"matches": 0, "groups": []}'))
        d = {"arm": "rcatools", "variant": "rcaeval", "truth": {"components": ["redis"]}}
        self.assertTrue(report._inspected(d, [{"input": {"name": "redis_cpu"}}]))
        self.assertIsNone(report._inspected(dict(d, variant=""), []))


def make_case(root):
    """A tiny RCAEval case in the Hugging Face layout; fooservice's cpu jumps at inject time."""
    import pandas as pd
    d = os.path.join(root, CASE)
    os.makedirs(d)
    t0, inj = 1700000000, 1700000720
    times = list(range(t0, t0 + 1440))
    rng = random.Random(1)
    m = {"time": times}
    for s in ("fooservice", "barservice", "frontend"):
        m[s + "_cpu"] = [rng.random() + (5 if s == "fooservice" and t >= inj else 0) for t in times]
        m[s + "_latency-50"] = [rng.random() for _ in times]
        m[s + "_latency-90"] = [rng.random() for _ in times]
    pd.DataFrame(m).to_parquet(os.path.join(d, "metrics.parquet"))
    pd.DataFrame({"timestamp": pd.array(times[::5], dtype="Int64"),
                  "container_name": ["fooservice" if i % 2 else "frontend" for i in range(len(times[::5]))],
                  "message": ["request %d failed" % i if t >= inj else "ok %d" % i
                              for i, t in enumerate(times[::5])]}).to_parquet(os.path.join(d, "logs.parquet"))
    rows = []
    for i, t in enumerate(times[::2]):
        rows.append({"traceID": "t%d" % i, "spanID": "p%d" % i, "parentSpanID": "", "serviceName": "frontend",
                     "methodName": "Home", "operationName": "/home", "startTime": t * 1_000_000,
                     "startTimeMillis": t * 1000, "duration": 1000, "statusCode": 0, "time": "x"})
        rows.append({"traceID": "t%d" % i, "spanID": "c%d" % i, "parentSpanID": "p%d" % i, "serviceName": "fooservice",
                     "methodName": "Get", "operationName": "/foo.Get", "startTime": t * 1_000_000 + 10,
                     "startTimeMillis": t * 1000, "duration": 500 + (5000 if t >= inj else 0), "statusCode": 0,
                     "time": "x"})
    tr = pd.DataFrame(rows)
    for c in ("startTime", "startTimeMillis", "duration", "statusCode"):
        tr[c] = tr[c].astype("Int64")
    tr.to_parquet(os.path.join(d, "traces.parquet"))
    with open(os.path.join(d, "inject_time.txt"), "w") as f:
        f.write("%d\n" % inj)


@unittest.skipUnless(HAVE_RCAEVAL, "needs pandas, pyarrow and RCAEval (agentlab/.venv)")
class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import warnings
        warnings.filterwarnings("ignore")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data, cls.root = os.path.join(cls.tmp.name, "raw"), os.path.join(cls.tmp.name, "layout")
        make_case(cls.data)
        cls.prep = rcatools.prepare(INC, cls.data, cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_vendored_tools_match_installed_rcaeval(self):
        theirs = [t for t in rc_toolkit.TOOLS if t["name"] != "submit"]
        self.assertEqual(json.dumps(theirs, sort_keys=True), json.dumps(rcatools.RCAEVAL_TOOLS, sort_keys=True))
        self.assertEqual(rc_toolkit.MAX_CHARS, rcatools.MAX_CHARS)

    def test_view_holds_no_labels(self):
        d = os.path.join(self.root, "rcatools", self.prep["dir"])
        self.assertNotIn("inject_time.txt", os.listdir(d))
        task = json.load(open(os.path.join(d, "task.json")))
        self.assertEqual(task["case_id"], self.prep["dir"])
        self.assertNotIn("fooservice_cpu", json.dumps({k: v for k, v in task.items() if k != "candidate_indicators"}))
        for name in os.listdir(d):
            self.assertNotIn("re2ob", name)

    def test_every_tool_runs_without_leaks(self):
        b = tools.LocalBackend(self.root)
        calls = [("describe_case", {}), ("list_metrics", {}), ("list_metrics", {"service": "fooservice"}),
                 ("get_metric", {"name": "fooservice_cpu"}), ("get_metric", {"name": "nope"}),
                 ("get_metric", {"name": "fooservice_cpu", "start": "abc"}), ("get_metric", {"bad": 1}),
                 ("rank_anomalies", {"modality": "metrics"}), ("rank_anomalies", {"modality": "logs"}),
                 ("rank_anomalies", {"modality": "trace_latency"}), ("rank_anomalies", {"modality": "trace_errors"}),
                 ("search_logs", {"pattern": "fail"}), ("search_logs", {"pattern": "("}),
                 ("trace_summary", {}), ("call_graph", {}), ("submit", {"ranks": ["fooservice"]})]
        outs = {}
        for t, a in calls:
            out, meta = b.call(INC, "rcatools", t, a)
            for bad in (CASE, "re2ob", self.tmp.name, "/raw/"):
                self.assertNotIn(bad, out, (t, a))
            self.assertLessEqual(len(out), tools.OUTPUT_CAP + 100)
            self.assertIn(meta["cap"], ("rcaeval", "agentlab"))
            outs.setdefault(t, []).append(json.loads(out) if meta["cap"] == "rcaeval" else out)
        desc = outs["describe_case"][0]
        self.assertEqual(desc["case_id"], self.prep["dir"])
        self.assertEqual(desc["inject_time"], 1700000720)  # the task view shows the injection time
        self.assertEqual(outs["rank_anomalies"][0]["ranked"][0]["name"], "fooservice_cpu")
        self.assertIn("error", outs["submit"][0])
        self.assertIn("error", outs["get_metric"][1])

    def test_mock_run(self):
        b = tools.LocalBackend(self.root)
        # the case is not in the committed subset: give run_one a spec for it
        from agentlab import rcaeval
        orig = rcaeval._subset_by_case
        rcaeval._subset_by_case = lambda suite="rcaeval": {CASE: {"case": CASE, "service": "fooservice", "fault": "cpu"}}
        try:
            spec = harness.task_spec(INC)
            o = harness.run_one(INC, "rcatools", harness.MockModel("rcatools", spec["mock_answer"]), b)
        finally:
            rcaeval._subset_by_case = orig
        d = o["detail"]
        self.assertEqual([c["tool"] for c in d["calls"]], ["describe_case", "rank_anomalies", "trace_summary"])
        self.assertTrue(d["grade"]["submitted"])
        self.assertIsNone(o["error"])


if __name__ == "__main__":
    unittest.main()

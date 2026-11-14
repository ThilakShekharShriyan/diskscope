"""Healthy-window controls (rcaevalm/rcaevalq) and the digest decoy/placebo. Stdlib only:
synthetic RCAEval-shaped data; the real prepared layout is checked only if it exists."""
import json
import os
import random
import re
import tempfile
import unittest

from agentlab import digest, digest_decoy as DD, harness, prepare, rcaeval as R, rcaeval_controls as RC, report, tools

CASE = R.load_subset()[0]["case"]          # re2ob_checkoutservice_cpu_1
TRUTH = R.load_subset()[0]["service"]
LAYOUT = os.environ.get("AGENTLAB_CONTROLS_ROOT",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data-ext", "al-rcaeval-controls"))


def synth(n_s=660, shift_at=400, truth=TRUTH, t0=1_700_000_000, seed=1):
    """RCAEval-shaped data: 1 s metrics for a few services, truth's cpu jumps at shift_at."""
    rng = random.Random(seed)
    metrics = []
    for i in range(n_s):
        ts = (t0 + i) * 1000
        for svc in (truth, "cartservice", "redis", "emailservice", "adservice"):
            for m, base in (("cpu", 3.0), ("mem", 2e7), ("latency-90", 12.0)):
                v = base * (1 + 0.02 * rng.random())
                if svc == truth and m == "cpu" and i >= shift_at:
                    v *= 6
                metrics.append({"ts_ms": ts, "series": "%s_%s" % (svc, m), "value": round(v, 6)})
    logs = [{"ts_ms": (t0 + i) * 1000, "level": "INFO", "component": "frontend", "msg": "request served %d" % i,
             "trace_id": None, "attrs": {}} for i in range(0, n_s, 3)]
    spans = [{"trace_id": "t%d" % i, "span_id": "s%d" % i, "parent_id": None, "name": "GET /", "component": "frontend",
              "start_ms": (t0 + i) * 1000.0, "dur_ms": 5.0, "status": "OK", "attrs": {"status_code": 0}}
             for i in range(0, n_s, 2)]
    return {"spans": spans, "logs": logs, "metrics": metrics, "calls": len(spans)}


class SuiteTest(unittest.TestCase):
    def test_mixed_suite_shape(self):
        ids = RC.mix_ids()
        q = [i for i in ids if i.startswith("rcaevalq:")]
        self.assertEqual((len(ids), len(q)), (36, RC.N_QUIET))
        self.assertTrue(0.15 <= len(q) / len(ids) <= 0.20)
        self.assertEqual(ids, RC.mix_ids())  # deterministic
        self.assertEqual(len(RC.quiet_ids()), 30)
        for c in R.load_subset():
            self.assertTrue(RC.OFFSET_RANGE[0] <= RC.inject_offset(c["case"]) <= RC.OFFSET_RANGE[1])

    def test_real_and_healthy_tasks_look_identical(self):
        for c in R.load_subset():
            m, q = harness.task_spec("rcaevalm:" + c["case"]), harness.task_spec("rcaevalq:" + c["case"])
            for k in ("system", "user", "submit", "tool_text"):
                self.assertEqual(m[k], q[k], k)
            blob = m["system"] + m["user"] + json.dumps(m["submit"]) + json.dumps(m["tool_text"])
            self.assertNotIn(c["case"], blob)
            self.assertNotIn("re2ob", blob)
            self.assertNotIn("24 min", blob)
        self.assertIn("no_fault", RC.SUBMIT["input_schema"]["properties"]["fault"]["enum"])
        self.assertNotIn("no_fault", R.SUBMIT["input_schema"]["properties"]["fault"]["enum"])  # main suite unchanged
        self.assertIn("about 11 minutes", RC.README)
        self.assertNotIn("24", RC.README)

    def test_grading(self):
        q, m = harness.task_spec("rcaevalq:" + CASE), harness.task_spec("rcaevalm:" + CASE)
        none = {"services": [], "fault": "no_fault", "evidence": [], "summary": ""}
        wrong = {"services": ["redis"], "fault": "cpu", "evidence": [], "summary": ""}
        right = {"services": [TRUTH], "fault": m["truth"]["cause"], "evidence": [], "summary": ""}
        g = q["grade"](q, none, [])
        self.assertTrue(g["correct"] and g["quiet"] and not g["false_positive"])
        g = q["grade"](q, wrong, [])
        self.assertTrue(g["false_positive"] and not g["correct"])
        self.assertEqual(g["named_first"], "redis")
        self.assertFalse(q["grade"](q, None, [])["false_positive"])  # unsubmitted is counted apart
        g = m["grade"](m, none, [])
        self.assertTrue(g["false_negative"] and g["submitted"])
        self.assertEqual(g["ac1"], 0.0)
        g = m["grade"](m, right, [])
        self.assertTrue(g["correct"] and not g["false_negative"])
        self.assertEqual(g["ac1"], 1.0)

    def test_windows(self):
        inject, t0 = 1_700_000_720, 1_700_000_000
        qs, qe = RC.window_ms("rcaevalq", inject, t0)
        self.assertEqual(qe, (inject - RC.GUARD_S) * 1000)
        self.assertEqual(qe - qs, RC.WINDOW_S * 1000)
        ms, me = RC.real_window_ms(CASE, inject)
        self.assertEqual(me - ms, RC.WINDOW_S * 1000)
        self.assertEqual(inject * 1000 - ms, RC.inject_offset(CASE) * 1000)
        ms, me = RC.real_window_ms(CASE, inject, last_s=inject + 283)  # data ends early: shift, keep length
        self.assertLessEqual(me, (inject + 284) * 1000)
        self.assertEqual(me - ms, RC.WINDOW_S * 1000)
        d = RC.crop(synth(n_s=1440, shift_at=720, t0=t0), qs, qe)
        self.assertLess(max(r["ts_ms"] for r in d["metrics"]), inject * 1000)
        self.assertEqual(len({r["ts_ms"] for r in d["metrics"]}), RC.WINDOW_S)


class DecoyTest(unittest.TestCase):
    def setUp(self):
        self.data = synth()
        self.text, self.index = digest.digest(self.data)
        self.v = DD.make_variants("rcaevalm:" + CASE, self.data, self.text, self.index, TRUTH)

    def test_one_line_inserted_as_m1_and_renumbered(self):
        for kind in ("decoy", "placebo"):
            t2, i2, meta = self.v[kind]
            a, b = self.text.splitlines(), t2.splitlines()
            self.assertEqual(len(b), len(a) + 1)
            first = b[b.index("METRICS (change points)") + 1]
            self.assertTrue(first.startswith("  M1 " + meta["series"] + "  "), first)
            ids = re.findall(r"^  ([MLE]\d+) ", t2, re.M)
            self.assertEqual(sorted(ids), sorted(i2))  # every id in the text drills, and nothing else
            self.assertEqual(i2["M2"], self.index["M1"])  # real lines keep their evidence
            self.assertEqual(DD.mentioned_services(t2, [meta["service"]]), [meta["service"]])

    def test_innocent_deterministic_and_drill_flat(self):
        meta = self.v["decoy"][2]
        self.assertNotEqual(meta["service"], TRUTH)
        self.assertEqual(meta["tier"], "unmentioned")
        again = DD.make_variants("rcaevalm:" + CASE, self.data, self.text, self.index, TRUTH)
        self.assertEqual(again["decoy"][0], self.v["decoy"][0])
        self.assertEqual(self.v["decoy"][1]["M1"], self.v["placebo"][1]["M1"])  # same evidence behind both
        vals = [float(x) for x in self.v["decoy"][1]["M1"].splitlines()[1].split()]
        self.assertLess(max(vals) / min(vals), 1.2)  # the "changed" series is flat
        self.assertGreater(meta["claimed_factor"], 2.4)
        self.assertGreater(meta["claimed_score"], max(DD._real_mlines(self.text)[0]["score"], 0))

    def test_decoy_and_placebo_differ_only_in_m1(self):
        a, b = self.v["decoy"][0].splitlines(), self.v["placebo"][0].splitlines()
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        self.assertEqual(len(diff), 1)
        self.assertIn("x1.0", diff[0][1])

    def test_healthy_window_without_metric_lines(self):
        flat = synth(shift_at=10 ** 6)
        text, index = digest.digest(flat)
        self.assertIn("none above threshold", text)
        t2, i2, meta = DD.make_variants("rcaevalq:" + CASE, flat, text, index, None)["decoy"]
        lines = t2.splitlines()
        self.assertTrue(lines[lines.index("METRICS (change points)") + 1].startswith("  M1 "))
        self.assertNotEqual(lines[lines.index("METRICS (change points)") + 2].strip(), "none above threshold")
        self.assertIn("M1", i2)


class LayoutLeakTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        cls.res = {}
        for tid, d, truth in (("rcaevalm:" + CASE, synth(), TRUTH), ("rcaevalq:" + CASE, synth(shift_at=10 ** 6), None)):
            cls.res[tid] = RC.prepare_task(tid, d, cls.root, truth, RC.README)
        RC._DECOYS = {tid: meta for tid, (_, meta) in cls.res.items()}

    @classmethod
    def tearDownClass(cls):
        RC._DECOYS = None
        cls.tmp.cleanup()

    def test_no_path_or_file_names_the_case(self):
        for dirpath, _, files in os.walk(self.root):
            for n in files:
                p = os.path.join(dirpath, n)
                self.assertNotIn("rcaeval", p)
                self.assertNotIn(CASE, p)
                if n.endswith((".txt", ".json", ".md", ".jsonl", ".csv")):
                    with open(p, errors="replace") as f:
                        body = f.read()
                    self.assertNotIn(CASE, body, p)
                    self.assertNotIn("no_fault", body, p)
                    self.assertNotIn("decoy", body.lower(), p)

    def test_opaque_dirs_distinct_per_arm_and_task(self):
        names = [prepare.opaque(t, arm=a) for t in self.res for a in ("files", "sql", "digest") + tuple(tools.CONTROL_ARMS)]
        self.assertEqual(len(names), len(set(names)))
        for t in self.res:
            for a in tools.CONTROL_ARMS:
                self.assertTrue(os.path.isdir(os.path.join(self.root, a, prepare.opaque(t, arm=a))))
        rd = []
        for t in self.res:
            with open(os.path.join(self.root, "files", prepare.opaque(t, arm="files"), "README.md")) as f:
                rd.append(f.read())
        self.assertEqual(rd[0], rd[1])

    def test_mock_runs_control_arms_and_records_decoy(self):
        b = tools.LocalBackend(self.root)
        for tid in self.res:
            for arm in ("digest",) + tuple(tools.CONTROL_ARMS):
                o = harness.run_one(tid, arm, harness.MockModel(arm, harness.task_spec(tid)["mock_answer"]), b)
                d = o["detail"]
                self.assertTrue(d["grade"]["submitted"])
                self.assertIn("DIGEST: 11 min window", d["calls"][0]["output"])
                if arm == "digest":
                    self.assertIsNone(d["decoy"])
                else:
                    self.assertEqual(d["decoy"]["service"], self.res[tid][1]["service"])
                    self.assertNotIn("unknown id", d["calls"][1]["output"])  # drill(M1) = the inserted line
                    self.assertIn(d["decoy"]["series"], d["calls"][0]["output"].splitlines()[4])
            self.assertEqual(o["detail"]["grade"]["quiet"], tid.startswith("rcaevalq:"))


class ReportTest(unittest.TestCase):
    def _obs(self, task, arm, answer, decoy=None):
        spec = harness.task_spec(task)
        g = spec["grade"](spec, answer, [])
        return {"task": task, "trial": 0, "error": None,
                "detail": {"arm": arm, "model": "m", "variant": spec["variant"], "grade": g, "truth": spec["truth"],
                           "answer": answer, "tokens": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0,
                                                        "cache_creation_input_tokens": 0}, "decoy": decoy}}

    def test_fp_rate_and_decoy_capture(self):
        cases = [c["case"] for c in R.load_subset()]
        ans = lambda s, f: {"services": s, "fault": f, "evidence": [], "summary": ""}
        obs = [self._obs("rcaevalq:" + cases[i], "sql", ans([], "no_fault") if i < 3 else ans(["redis"], "cpu"))
               for i in range(4)]
        obs += [self._obs("rcaevalm:" + c["case"], "sql", ans([c["service"]], c["fault"])) for c in R.load_subset()[:4]]
        obs += [self._obs("rcaevalm:" + c["case"], "digest-decoy", ans(["adservice", c["service"]], c["fault"]),
                          {"service": "adservice"}) for c in R.load_subset()[:4]]
        path = os.path.join(tempfile.mkdtemp(), "run.json")
        with open(path, "w") as f:
            json.dump({"observations": obs}, f)
        runs = report.load([path])
        ctl = report.controls(runs)
        fp = [x for x in ctl["false_positive"] if x["arm"] == "sql+mix"][0]
        self.assertAlmostEqual(fp["fp_rate"][0], 0.25)
        self.assertEqual(fp["quiet_cases"], 4)
        self.assertEqual(fp["correct_real"][0], 1.0)
        dec = [x for x in ctl["decoy"] if x["arm"] == "digest-decoy+mix"][0]
        self.assertEqual(dec["decoy_first"][0], 1.0)
        self.assertEqual(dec["ac1"][0], 0.0)
        summary, _ = report.summarize([r for r in runs if not r["quiet"]])
        self.assertEqual({s["arm"] for s in summary}, {"sql+mix", "digest-decoy+mix"})
        self.assertIn("False-positive rate", report.controls_markdown(ctl))


@unittest.skipUnless(os.path.isdir(LAYOUT), "prepared controls layout not present")
class PreparedLayoutTest(unittest.TestCase):
    """The real prepared layout (python -m agentlab.rcaeval_controls): no case names anywhere an agent
    reads, every mixed-suite digest an 11-min window, decoy manifest covers every task."""

    def test_layout(self):
        subset = R.load_subset()
        names = {c["case"] for c in subset}
        for arm in os.listdir(LAYOUT):
            self.assertFalse(any(n in arm for n in names))
        for c in subset:
            for v in ("rcaevalm", "rcaevalq"):
                tid = v + ":" + c["case"]
                for arm in ("digest", "digest-decoy", "digest-placebo"):
                    d = os.path.join(LAYOUT, arm, prepare.opaque(tid, arm=arm))
                    text = open(os.path.join(d, "digest.txt")).read()
                    self.assertTrue(text.startswith("DIGEST: 11 min window"), (tid, arm))
                    self.assertNotIn("re2ob", text)
                self.assertIsNotNone(RC.decoy_for(tid), tid)
                self.assertNotEqual(RC.decoy_for(tid)["service"], c["service"] if v == "rcaevalm" else None)
                readme = open(os.path.join(LAYOUT, "files", prepare.opaque(tid, arm="files"), "README.md")).read()
                self.assertEqual(readme, RC.README)


if __name__ == "__main__":
    unittest.main()

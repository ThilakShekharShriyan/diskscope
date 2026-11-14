"""Lessons / placebo arms: folds, leak filter, build with a fake distiller, harness routing. No network."""
import glob
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from agentlab import harness, lessons, rcaeval  # noqa: E402

PILOTS = sorted(glob.glob(os.path.join(HERE, "..", "..", "data", "agentlab", "pilot-rcaeval-*.json")))

FAKE = """## cpu
- A container cpu series of the root-cause service jumps many-fold while workload stays flat.
- productcatalogservice_cpu rose, so it was the culprit.
## mem
- The mem series steps up and stays high; callers show no errors.
- See case re2ob_emailservice_mem_1 for a clean example.
## disk
- diskio rises on one service; checkout latency follows.
## delay
- latency-90 rises on the root-cause service and its callers without error logs.
"""


def fake_distiller(calls):
    def distill(prompt, kind):
        calls.append((kind, prompt))
        usage = {"input_tokens": len(prompt) // 4, "output_tokens": 500,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        text = FAKE if kind == "lessons" else FAKE.replace("root-cause", "suspect") + "\n- extra filler " * 50
        return text, usage
    return distill


class FoldsTest(unittest.TestCase):
    def test_five_disjoint_service_folds(self):
        fs = lessons.folds()
        self.assertEqual(len(fs), 5)
        cases = [c for _, _, cs in fs for c in cs]
        self.assertEqual(len(cases), 30)
        self.assertEqual(len(set(cases)), 30)
        for _, svc, cs in fs:
            self.assertEqual(len(cs), 6)
            self.assertTrue(all(c.startswith("re2ob_%s_" % svc) for c in cs))

    @unittest.skipUnless(PILOTS, "pilot data not found")
    def test_training_runs_exclude_held_out_service(self):
        runs = lessons.load_runs(PILOTS)
        self.assertTrue(runs)
        for base in lessons.BASES:
            for _, svc, tests in lessons.folds():
                tr = lessons.training_runs(runs, base, svc)
                self.assertTrue(tr)
                for o in tr:
                    self.assertNotEqual(o["detail"]["truth"]["components"][0], svc)
                    self.assertNotIn(o["task"].split(":", 1)[1], tests)
                    self.assertEqual(o["detail"]["arm"], base)

    @unittest.skipUnless(PILOTS, "pilot data not found")
    def test_load_runs_jsonl_dedupe_and_variant(self):
        obs = json.loads(lessons._read(PILOTS[0]))["observations"]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json.partial.jsonl")
            with open(p, "w") as f:
                for o in obs + obs:
                    f.write(json.dumps(o) + "\n")
                t = json.loads(json.dumps(obs[0]))
                t["task"] = t["task"].replace("rcaeval:", "rcaeval+t:")
                f.write(json.dumps(t) + "\n")
                t = json.loads(json.dumps(obs[1]))
                t["trial"] = 9
                t["detail"]["infra_error"] = True
                f.write(json.dumps(t) + "\n")
            self.assertEqual(len(lessons.load_runs([p])), len(obs))
            self.assertEqual(len(lessons.load_runs([p], variant="rcaeval+t")), 1)


class FilterTest(unittest.TestCase):
    def test_catches_series_names_stems_and_case_ids(self):
        for line in ["- productcatalogservice_cpu rose", "- Checkout latency", "- see re2ob_x_cpu_1",
                     "- the FRONTEND socket count", "- redis mem", "- rcaeval:foo"]:
            self.assertEqual(lessons.leaks(line), [line], line)
        self.assertEqual(lessons.leaks("- the root-cause service's diskio rises; callers' latency-90 follows"), [])

    def test_post_filter_drops_and_caps(self):
        text, dropped = lessons.post_filter(FAKE, 400)
        self.assertEqual(lessons.leaks(text), [])
        self.assertEqual(len(dropped), 3)
        self.assertIn("## delay", text)
        self.assertNotIn("## disk", text)  # its only rule named a service: no empty heading left
        short, _ = lessons.post_filter(FAKE, 25)
        self.assertLessEqual(lessons.words(short), 25)
        self.assertFalse(short.strip().splitlines()[-1].startswith("#"))

    def test_prompts_do_not_name_confusion_pairs(self):
        p = lessons.placebo_prompt("digest", 300) + lessons.LESSONS_INTRO + lessons.RULES
        for a, b in [("disk", "mem"), ("loss", "delay"), ("socket", "cpu")]:
            self.assertNotRegex(p.lower(), r"%s\W+(vs|versus|or|confused with)\W+%s" % (a, b))


@unittest.skipUnless(PILOTS, "pilot data not found")
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.calls = []
        with redirect_stdout(io.StringIO()):
            self.man = lessons.build(PILOTS, self.dir, distill=fake_distiller(self.calls), max_usd=100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_hashes_and_leak_check(self):
        self.assertEqual(len(self.man["files"]), 20)  # 2 bases x 5 folds x {lessons, placebo}
        self.assertEqual(len(self.calls), 20)
        self.assertEqual(lessons.check(self.dir), [])
        for e in self.man["files"]:
            body = lessons._read(os.path.join(self.dir, e["file"]))
            self.assertEqual(lessons.sha256(body), e["sha256"])
            if e["kind"] == "placebo":
                self.assertLessEqual(e["words"], e["target_words"])
            else:  # the held-out service's runs never reach its fold's distiller prompt
                self.assertTrue(all(e["held_out"] not in t for t, _, _ in e["training_runs"]))

    def test_lessons_prompt_has_no_held_out_cases_and_placebo_no_transcripts(self):
        lp = [p for k, p in self.calls if k == "lessons"]
        pp = [p for k, p in self.calls if k == "placebo"]
        for e, p in zip([e for e in self.man["files"] if e["kind"] == "lessons"], lp):
            self.assertNotIn("Correct answer: service=%s " % e["held_out"], p)  # other services telemetry may mention it
            self.assertEqual(p.count("Correct answer: service="), len({t for t, _, _ in e["training_runs"]}))
        for p in pp:
            self.assertNotIn("Graded runs", p)
            self.assertEqual(lessons.leaks(p), [])  # no service names anywhere in the placebo prompt

    def test_check_fails_on_planted_name_or_edit(self):
        e = self.man["files"][0]
        p = os.path.join(self.dir, e["file"])
        with open(p, "a") as f:
            f.write("- %s socket climbs\n" % e["held_out"])
        probs = lessons.check(self.dir)
        self.assertTrue(any("sha256" in x for x in probs))
        self.assertTrue(any("leaks" in x for x in probs))

    def test_dry_run_is_deterministic_and_never_makes_a_client(self):
        def boom(*a, **k):
            raise AssertionError("dry run created a client")
        out1, out2 = io.StringIO(), io.StringIO()
        with mock.patch.object(lessons, "make_distiller", boom):
            with redirect_stdout(out1):
                lessons.build(PILOTS, os.path.join(self.dir, "dry"), dry_run=True)
            with redirect_stdout(out2):
                lessons.build(PILOTS, os.path.join(self.dir, "dry"), dry_run=True)
        self.assertEqual(out1.getvalue(), out2.getvalue())
        self.assertIn("projected", out1.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "dry")))
        live = [p for k, p in self.calls if k == "lessons"][0]
        self.assertIn(live, out1.getvalue())  # dry-run prompt == the prompt the live build sends

    def test_build_refuses_to_overwrite_and_check_needs_manifest(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            lessons.build(PILOTS, self.dir, distill=fake_distiller([]), max_usd=100)
        self.assertTrue(lessons.check(os.path.join(self.dir, "nowhere")))

    def test_spend_cap_stops_before_the_call(self):
        calls = []
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            lessons.build(PILOTS, os.path.join(self.dir, "cap"), distill=fake_distiller(calls), max_usd=0.01)
        self.assertEqual(calls, [])

    def test_harness_routes_lessons_arm_to_base(self):
        seen = {}

        class Stub:
            name = "stub"

            def call(self, incident_id, arm, tool, args):
                seen.setdefault("arms", set()).add(arm)
                return "ok", {"tool_ms": 0}

        class Spy(harness.MockModel):
            def turn(self, messages, tools, system):
                seen["system"], seen["tools"] = system, [t["name"] for t in tools]
                return super().turn(messages, tools, system)

        held = lessons.folds()[0]
        case = "rcaeval:" + held[2][0]
        with mock.patch.dict(os.environ, {"AGENTLAB_LESSONS_DIR": self.dir}):
            for arm in ("sql+lessons", "digest+placebo"):
                seen.clear()
                o = harness.run_one(case, arm, Spy(arm, harness.task_spec(case)["mock_answer"]), Stub())
                base, kind = arm.split("+")
                self.assertEqual(seen["arms"], {base})
                self.assertIn("## " + lessons.HEADING, seen["system"])
                self.assertNotIn("submit_answer", seen["tools"][:-1])
                e = [x for x in self.man["files"] if x["base"] == base and x["kind"] == kind
                     and x["held_out"] == held[1]][0]
                self.assertEqual(o["detail"]["lessons_sha"], e["sha256"])
                self.assertEqual(o["detail"]["arm"], arm)
            with self.assertRaises(ValueError):
                lessons.section("sql+lessons", "erofs-a")


if __name__ == "__main__":
    unittest.main()

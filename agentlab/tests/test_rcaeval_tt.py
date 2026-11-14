"""RCAEval RE2-TT (Train Ticket) suite: task spec, subset, ids (no dataset download needed)."""
import hashlib
import json
import unittest

from agentlab import harness, rcaeval
from agentlab import __main__ as cli


def _ob_specs():
    return {c["case"]: {k: v for k, v in harness.task_spec("rcaeval:" + c["case"]).items() if k != "grade"}
            for c in rcaeval.load_subset()}


class TrainTicketSuiteTest(unittest.TestCase):
    def test_online_boutique_specs_unchanged(self):
        # sha256 of every "rcaeval:<case>" spec before the suite was generalised
        blob = json.dumps(_ob_specs(), sort_keys=True).encode()
        self.assertEqual(hashlib.sha256(blob).hexdigest(),
                         "4fe236fa86726533fd2583ff19c0c4f56b855829bd5a533acd52e98c13a41eb6")
        self.assertIs(rcaeval.SUITES["rcaeval"]["submit"], rcaeval.SUBMIT)

    def test_subset_fixed(self):
        s = rcaeval.load_subset("rcaevaltt")
        self.assertEqual(len(s), 30)
        self.assertEqual(len({(c["service"], c["fault"]) for c in s}), 30)
        self.assertTrue(all(c["case"].startswith("re2tt_") for c in s))
        self.assertEqual(sorted({c["fault"] for c in s}), sorted(rcaeval.FAULTS))

    def test_pilot_one_per_fault(self):
        pilot = rcaeval.ids("rcaevaltt-pilot")
        sub = {c["case"]: c for c in rcaeval.load_subset("rcaevaltt")}
        cases = [i.split(":", 1)[1] for i in pilot]
        self.assertEqual(sorted(sub[c]["fault"] for c in cases), sorted(rcaeval.FAULTS))
        self.assertEqual(cases, rcaeval.pick_pilot(rcaeval.load_subset("rcaevaltt")))

    def test_enum_covers_truth_and_is_structure(self):
        enum = rcaeval.SUITES["rcaevaltt"]["submit"]["input_schema"]["properties"]["services"]["items"]["enum"]
        self.assertEqual(enum, rcaeval.TT_SERVICES)
        self.assertEqual(len(enum), len(set(enum)))
        self.assertGreater(len(enum), 40)
        self.assertTrue({c["service"] for c in rcaeval.load_subset("rcaevaltt")} <= set(enum))

    def test_prompt_does_not_name_case(self):
        for c in rcaeval.load_subset("rcaevaltt"):
            spec = harness.task_spec("rcaevaltt:" + c["case"])
            blob = spec["system"] + spec["user"] + str(spec["submit"]) + str(spec["tool_text"])
            self.assertNotIn(c["case"], blob)
            self.assertNotIn("re2tt", blob)
            self.assertIn("Train Ticket", spec["system"])
            self.assertIn("at most {turns} turns", spec["system"])
            self.assertEqual(spec["layout_id"], "rcaevaltt:" + c["case"])
            self.assertEqual(spec["truth"], {"components": [c["service"]], "cause": c["fault"]})
            self.assertIn(spec["mock_answer"]["services"][0], rcaeval.TT_SERVICES)

    def test_ids_and_suites(self):
        self.assertEqual(rcaeval.suite_of("rcaevaltt+t:x"), ("rcaevaltt+t", "rcaevaltt", "x"))
        self.assertEqual(rcaeval.suite_of("rcaeval:x"), ("rcaeval", "rcaeval", "x"))
        with self.assertRaises(KeyError):
            rcaeval.suite_of("rcaevalss:x")
        self.assertEqual(set(rcaeval.ID_PREFIXES), {"rcaeval:", "rcaeval+t:", "rcaevaltt:", "rcaevaltt+t:"})
        tt = cli._ids("rcaevaltt")
        self.assertEqual(len(tt), 30)
        self.assertTrue(all(i.startswith("rcaevaltt:") for i in tt))
        self.assertTrue(all(i.startswith("rcaevaltt+t:") for i in cli._ids("rcaevaltt+t")))
        self.assertEqual(len(cli._ids("rcaeval")), 30)
        self.assertEqual(cli._ids(tt[0] + ",rcaeval:" + rcaeval.load_subset()[0]["case"])[0], tt[0])

    def test_grade_tt(self):
        spec = harness.task_spec(rcaeval.ids("rcaevaltt")[0])
        truth = spec["truth"]["components"][0]
        g = rcaeval.grade(spec, {"services": ["ts-ui-dashboard", truth], "fault": spec["truth"]["cause"]}, [])
        self.assertEqual((g["ac1"], g["ac3"]), (0.0, 1.0))

    def test_tt_readme_mentions_empty_logs(self):
        self.assertIn("empty", rcaeval.TT_README)
        self.assertNotIn("re2tt", rcaeval.TT_README)

    def test_mock_run_reads_tt_layout_id(self):
        seen = []

        class Backend:
            name = "fake"

            def call(self, incident_id, arm, tool, args):
                seen.append(incident_id)
                return "ok", {"tool_ms": 0}

        i = rcaeval.ids("rcaevaltt")[0]
        o = harness.run_one(i, "digest", harness.MockModel("digest", harness.task_spec(i)["mock_answer"]), Backend())
        self.assertTrue(seen and set(seen) == {i})
        self.assertEqual(o["detail"]["truth"]["components"][0], rcaeval.load_subset("rcaevaltt")[0]["service"])


if __name__ == "__main__":
    unittest.main()

"""RCAEval suite: task spec and grading (no dataset download needed)."""
import unittest

from agentlab import harness, rcaeval


class RcaevalTaskTest(unittest.TestCase):
    def test_subset_fixed(self):
        s = rcaeval.load_subset()
        self.assertEqual(len(s), 30)
        self.assertEqual(len({(c["service"], c["fault"]) for c in s}), 30)

    def test_prompt_does_not_name_case(self):
        for c in rcaeval.load_subset():
            spec = harness.task_spec("rcaeval:" + c["case"])
            blob = spec["system"] + spec["user"] + str(spec["submit"]) + str(spec["tool_text"])
            self.assertNotIn(c["case"], blob)
            self.assertNotIn("re2ob", blob)

    def test_grade_ranked(self):
        spec = harness.task_spec("rcaeval:" + rcaeval.load_subset()[0]["case"])
        truth = spec["truth"]["components"][0]
        g = rcaeval.grade(spec, {"services": ["redis", truth], "fault": spec["truth"]["cause"]}, [])
        self.assertEqual((g["ac1"], g["ac3"], g["avg5"]), (0.0, 1.0, 0.8))
        self.assertFalse(g["correct"])
        self.assertTrue(rcaeval.grade(spec, {"services": [truth], "fault": spec["truth"]["cause"]}, [])["correct"])

    def test_synthetic_prompt_unchanged(self):
        spec = harness.task_spec("erofs-a")
        self.assertIn("at most {turns} turns", spec["system"])
        self.assertEqual(harness.arm_tools("sql", spec["tool_text"]), harness.ARM_TOOLS["sql"])


if __name__ == "__main__":
    unittest.main()

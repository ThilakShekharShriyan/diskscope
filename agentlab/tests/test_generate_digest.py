import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from agentlab import generate, digest, bundle  # noqa: E402

# A deliberately generic effect, unrelated to any incident in incidents.py.
GENERIC = [{"kind": "scale", "component": "worker", "op": "compute", "factor": 6.0, "start_s": 2400}]


class GenerateDigestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = generate.generate(GENERIC, seed=7, rate_per_s=0.5)

    def test_deterministic(self):
        again = generate.generate(GENERIC, seed=7, rate_per_s=0.5)
        self.assertEqual(again["spans"][:50], self.data["spans"][:50])

    def test_shapes(self):
        self.assertGreater(self.data["calls"], 1000)
        roots = [s for s in self.data["spans"] if s["parent_id"] is None]
        self.assertEqual(len(roots), self.data["calls"])

    def test_digest_finds_generic_shift_first(self):
        text, index = digest.digest(self.data)
        first = [l for l in text.splitlines() if l.strip().startswith("M1 ")][0]
        self.assertIn("worker.compute", first)
        self.assertIn("+40m", first)
        self.assertIn("M1", index)
        self.assertLess(len(text), 4000)

    def test_bundle_writes_every_form(self):
        with tempfile.TemporaryDirectory() as d:
            files = bundle.write_bundle(self.data, d)
        for f in ["logs.jsonl", "spans.jsonl", "metrics.csv", "telemetry.db", "metrics.om", "loki.json", "README.md"]:
            self.assertIn(f, files)

    def test_unknown_effect_rejected(self):
        with self.assertRaises(ValueError):
            generate.generate([{"kind": "nope"}])


if __name__ == "__main__":
    unittest.main()

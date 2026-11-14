"""The digest must stay generic: no fault-specific words or rules."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "agentlab", "digest.py")

# Words that would only appear if the digest encoded a specific fault.
FORBIDDEN = ["erofs", "eexist", "enoent", "read-only", "readonly", "sqlite", "slim", "image",
             "checkout", "cache", "fanout", "concurren", "rename", "unlink", "fsync", "commit",
             "exec-pool", "gateway", "scheduler", "store", "worker", "mount", "shared", "conditional",
             "archil", "lookup", "permission", "exists", "acquire", "boot", "flush", "decoy", "incident"]


class FreezeTest(unittest.TestCase):
    def test_no_fault_specific_words(self):
        src = open(SRC).read().lower()
        hits = [w for w in FORBIDDEN if re.search(re.escape(w), src)]
        self.assertEqual(hits, [], "digest.py mentions fault-specific words: %s" % hits)

    def test_no_incident_import(self):
        self.assertNotIn("incidents", open(SRC).read())


if __name__ == "__main__":
    unittest.main()

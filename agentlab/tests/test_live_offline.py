"""Exercise LiveModel + run_one with real SDK response objects and no network. Skips without the SDK."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    import anthropic
    from anthropic.types import Message
except ImportError:  # CI and system python have no SDK
    anthropic = None

from agentlab import harness, incidents, prepare, tools  # noqa: E402


def _msg(content, stop):
    return Message.model_validate({
        "id": "msg_x", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 900,
                  "cache_creation_input_tokens": 0}})


@unittest.skipIf(anthropic is None, "anthropic SDK not installed")
class LiveOfflineTest(unittest.TestCase):
    def test_loop_with_sdk_objects(self):
        script = [
            _msg([{"type": "thinking", "thinking": "", "signature": "sig"},
                  {"type": "tool_use", "id": "tu_1", "name": "get_digest", "input": {}}], "tool_use"),
            _msg([{"type": "text", "text": "The fs component is rejecting renames."},
                  {"type": "tool_use", "id": "tu_2", "name": "submit_answer",
                   "input": {"component": "fs", "cause": "readonly_or_permission",
                             "evidence": ["Read-only file system"], "summary": "EROFS on rename"}}], "tool_use"),
        ]
        sent = []

        def fake_create(**kw):
            sent.append(kw)
            return script.pop(0)

        os.environ.setdefault("ANTHROPIC_API_KEY", "test-not-used")
        m = harness.LiveModel("claude-opus-5-5")
        m.client.messages.create = fake_create
        with tempfile.TemporaryDirectory() as root:
            prepare.prepare(incidents.ALL["erofs-a"], root)
            o = harness.run_one("erofs-a", "digest", m, tools.LocalBackend(root))
        self.assertTrue(o["ok"])
        self.assertEqual(o["detail"]["assistant_text"], ["The fs component is rejecting renames."])
        self.assertEqual(o["detail"]["tokens"]["cache_read_input_tokens"], 1800)
        self.assertAlmostEqual(o["detail"]["usd"], 2 * (1200 * 4 + 80 * 20 + 900 * 0.2) / 1e6)
        kw = sent[1]
        self.assertNotIn("fallbacks", kw)  # no server-side model fallback in the benchmark
        self.assertEqual(o["detail"]["served_by"], ["claude-opus-5-5"])
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        self.assertEqual(kw["cache_control"], {"type": "ephemeral"})
        # thinking block passed back unchanged, tool result follows
        self.assertEqual(kw["messages"][1]["content"][0].type, "thinking")
        self.assertEqual(kw["messages"][2]["content"][0]["type"], "tool_result")


if __name__ == "__main__":
    unittest.main()

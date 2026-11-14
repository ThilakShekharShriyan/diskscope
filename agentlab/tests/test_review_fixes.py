"""Regression tests for the pre-run review (2026-11-14)."""
import os
import sqlite3
import tempfile
import unittest

from agentlab import harness, prepare, tools
from agentlab.harness import Block


def _db(root, incident="x"):
    d = os.path.join(root, "sql", prepare.opaque(incident, arm="sql"))
    os.makedirs(d)
    db = sqlite3.connect(os.path.join(d, "telemetry.db"))
    db.execute("CREATE TABLE logs(ts_ms INTEGER, msg TEXT)")
    db.executemany("INSERT INTO logs VALUES (?,?)", [(i, 'a,b "q"') for i in range(5000)])
    db.commit()
    db.close()


class SqlToolTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        _db(self.root)
        self.b = tools.LocalBackend(self.root)

    def q(self, sql):
        return self.b.call("x", "sql", "query", {"sql": sql})

    def test_csv_quoting_like_sqlite_cli(self):
        out, _ = self.q("SELECT ts_ms, msg FROM logs LIMIT 1")
        self.assertEqual(out, 'ts_ms,msg\n0,"a,b ""q"""\n')  # trailing newline, as sqlite3 -csv

    def test_truncation_is_marked(self):
        out, _ = self.q("SELECT * FROM logs")
        self.assertIn("[truncated:", out)
        self.assertTrue(out.rstrip().endswith("+ more chars]"))

    def test_attach_pragma_and_file_functions_refused(self):
        self.assertIn("not authorized", self.q("PRAGMA database_list")[0])
        self.assertIn("not authorized", self.q("ATTACH DATABASE ':memory:' AS m")[0])
        self.assertEqual(self.q("SELECT readfile('/etc/hosts')")[0], tools.SQL_REFUSED)
        self.assertEqual(self.q(".tables")[0], tools.SQL_REFUSED)

    def test_slow_query_interrupted(self):
        old = tools._SQL_TIMEOUT_S
        tools._SQL_TIMEOUT_S = 0
        try:
            out, _ = self.q("SELECT count(*) FROM logs a, logs b")
        finally:
            tools._SQL_TIMEOUT_S = old
        self.assertIn("longer than", out)


class ScriptModel:
    name = "mock"

    def __init__(self, turns):
        self.turns = list(turns)

    def turn(self, messages, tools_, system):
        content, stop = self.turns.pop(0)
        return content, stop, {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}


class FakeBackend:
    name = "fake"

    def __init__(self, exc=None):
        self.exc, self.n = exc, 0

    def call(self, *a):
        self.n += 1
        if self.exc:
            raise self.exc
        return "out", {"tool_ms": 1}


def use(name, inp, i=0):
    return Block(type="tool_use", id="t%d" % i, name=name, input=inp)


SUBMIT = {"component": "none", "cause": "no_fault", "evidence": [], "summary": "s"}


class LoopTest(unittest.TestCase):
    def test_infra_error_stops_and_is_not_shown(self):
        m = ScriptModel([([use("query", {"sql": "x"})], "tool_use")])
        o = harness.run_one("quiet-plain", "sql", m, FakeBackend(harness.InfraError("budget")))
        self.assertTrue(o["detail"]["infra_error"])
        self.assertEqual(o["detail"]["calls"], [])

    def test_cut_off_reply_tools_not_executed(self):
        b = FakeBackend()
        m = ScriptModel([([use("query", {"sql": "x"})], "max_tokens"), ([use("submit_answer", SUBMIT)], "tool_use")])
        o = harness.run_one("quiet-plain", "sql", m, b)
        self.assertEqual(b.n, 0)
        self.assertTrue(o["detail"]["grade"]["submitted"])

    def test_tool_call_cap(self):
        b = FakeBackend()
        many = [use("query", {"sql": "x"}, i) for i in range(harness.MAX_TOOL_CALLS + 5)]
        m = ScriptModel([(many, "tool_use"), ([use("submit_answer", SUBMIT, 99)], "tool_use")])
        harness.run_one("quiet-plain", "sql", m, b)
        self.assertEqual(b.n, harness.MAX_TOOL_CALLS)

    def test_spend_meter_stops_before_next_turn(self):
        meter = harness.SpendMeter(0.0)
        o = harness.run_one("quiet-plain", "sql", ScriptModel([]), FakeBackend(), meter=meter)
        self.assertEqual(o["error"], "spend cap reached")
        self.assertTrue(o["detail"]["infra_error"])

    def test_turns_log_and_call_turn_index(self):
        m = ScriptModel([([use("query", {"sql": "x"})], "tool_use"), ([use("submit_answer", SUBMIT, 1)], "tool_use")])
        d = harness.run_one("quiet-plain", "sql", m, FakeBackend())["detail"]
        self.assertEqual(len(d["turns_log"]), 2)
        self.assertEqual(d["calls"][0]["turn"], 0)
        self.assertFalse(d["model_mismatch"])


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class OpenAITurnTest(unittest.TestCase):
    def _model(self, resp):
        m = harness.OpenAIModel.__new__(harness.OpenAIModel)
        m.name, m.effort, m.prev, m.sent = "gpt-5.6-sol", "medium", None, 0
        m.client = NS(responses=NS(create=lambda **kw: resp))
        return m

    def test_usage_and_tool_calls(self):
        resp = NS(id="r1", model="gpt-5.6-sol-2026-09-01", status="completed", incomplete_details=None,
                  output=[NS(type="reasoning"), NS(type="function_call", call_id="c1", name="query", arguments='{"sql":"x"}')],
                  usage=NS(input_tokens=100, output_tokens=7, input_tokens_details=NS(cached_tokens=40),
                           output_tokens_details=NS(reasoning_tokens=3)))
        content, stop, usage = self._model(resp).turn([{"role": "user", "content": "go"}], [], "sys")
        self.assertEqual(stop, "tool_use")
        self.assertEqual((usage["input_tokens"], usage["cache_read_input_tokens"], usage["reasoning_tokens"]), (60, 40, 3))
        self.assertEqual(content[0].input, {"sql": "x"})

    def test_incomplete_and_refusal(self):
        base = dict(id="r", model="gpt-5.6-sol", usage=NS(input_tokens=1, output_tokens=1, input_tokens_details=None,
                                                          output_tokens_details=None))
        _, stop, _ = self._model(NS(status="incomplete", incomplete_details=NS(reason="max_output_tokens"),
                                    output=[], **base)).turn([{"role": "user", "content": "go"}], [], "s")
        self.assertEqual(stop, "max_tokens")
        _, stop, _ = self._model(NS(status="completed", incomplete_details=None,
                                    output=[NS(type="message", content=[NS(type="refusal")])], **base)
                                 ).turn([{"role": "user", "content": "go"}], [], "s")
        self.assertEqual(stop, "refusal")


class RcaevalToolTextTest(unittest.TestCase):
    def test_query_description_replaced(self):
        spec = harness.task_spec("rcaeval:" + __import__("agentlab.rcaeval").rcaeval.load_subset()[0]["case"])
        q = harness.arm_tools("sql", spec["tool_text"])[0]["description"]
        self.assertIn("1 s steps", q)
        self.assertIn("8000", q)


if __name__ == "__main__":
    unittest.main()

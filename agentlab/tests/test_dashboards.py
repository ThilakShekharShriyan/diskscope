"""Grafana arms (visual, mcp): conversion, dashboard, MCP client, harness plumbing.
Docker-free except StackTest, which skips unless the stack is up with incidents loaded."""
import base64
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from agentlab import dashboards as D  # noqa: E402
from agentlab import harness, prepare, tools  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
CASE = "re2ob_currencyservice_cpu_1"


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_openmetrics_relabels_by_stat_service_incident(self):
        src = os.path.join(self.tmp.name, "m.om")
        with open(src, "w") as f:
            f.write(textwrap.dedent('''\
                # TYPE agentlab_adservice_cpu gauge
                agentlab_adservice_cpu{component="adservice_cpu"} 0.5 1705767105.000
                agentlab_adservice_cpu{component="adservice_cpu"} 0.7 1705767106.000
                # TYPE agentlab_frontend_latency_90 gauge
                agentlab_frontend_latency_90{component="frontend_latency-90"} 0.2 1705767105.000
                # TYPE agentlab_gateway_tool_call_p95 gauge
                agentlab_gateway_tool_call_p95{component="gateway.tool_call.p95"} 12 1705767107.000
                # EOF
                '''))
        dst = os.path.join(self.tmp.name, "out.om")
        info = D.convert_openmetrics(src, dst, "cabc")
        text = open(dst).read()
        self.assertEqual(info["stats"], ["app_cpu", "app_latency_90", "app_tool_call_p95"])
        self.assertEqual(info["services"], ["adservice", "frontend", "gateway"])
        self.assertEqual((info["start_ms"], info["end_ms"], info["samples"]), (1705767105000, 1705767107000, 4))
        self.assertIn('app_cpu{incident="cabc",service="adservice"} 0.7 1705767106.000', text)
        self.assertIn('app_latency_90{incident="cabc",service="frontend"} 0.2', text)
        self.assertTrue(text.endswith("# EOF\n"))
        self.assertEqual(text.count("# TYPE app_cpu gauge"), 1)  # one contiguous family per metric

    def test_loki_batches_label_and_keep_every_line(self):
        src = os.path.join(self.tmp.name, "l.json")
        vals = [["1000000000", "a"], ["1000000000", "a"], ["1000000000", "b"], ["2000000000", "c"]]
        with open(src, "w") as f:
            json.dump({"streams": [{"stream": {"job": "agentlab", "component": "x", "level": "INFO"}, "values": vals}]}, f)
        out = D.loki_batches(src, "cabc", max_values=3)
        got = [v for b in out for s in b["streams"] for v in s["values"]]
        self.assertEqual(len(got), 4)
        self.assertEqual([int(t) for t, _ in got], [1000000000, 1000000001, 1000000002, 2000000000])
        self.assertTrue(all(s["stream"]["incident"] == "cabc" for b in out for s in b["streams"]))


class DashboardTest(unittest.TestCase):
    def test_generic_dashboard_selects_by_incident(self):
        stats = ["app_cpu", "app_mem", "app_latency_50", "app_latency_90", "app_error", "app_workload",
                 "app_socket", "app_diskio"]
        d = D.build_dashboard(stats)
        ids = [p["id"] for p in d["panels"]]
        self.assertEqual(ids, list(range(1, len(stats) + 4)))
        for p in d["panels"]:
            for t in p["targets"]:
                self.assertIn('incident="$incident"', t["expr"])
        blob = json.dumps(d).lower()
        for leak in ("currencyservice", "re2ob", "inject", "fault", "root", "cause", "stress"):
            self.assertIsNone(re.search(r"\b%s\b" % leak, blob), leak)
        for p in d["panels"]:  # no overlap in the grid
            g = p["gridPos"]
            self.assertLessEqual(g["x"] + g["w"], 24)
        cells = set()
        for p in d["panels"]:
            g = p["gridPos"]
            for x in range(g["x"], g["x"] + g["w"]):
                for y in range(g["y"], g["y"] + g["h"]):
                    self.assertNotIn((x, y), cells)
                    cells.add((x, y))

    def test_visual_tools_are_strict_and_small(self):
        for t in D.VISUAL_TOOLS:
            self.assertTrue(t["strict"])
            self.assertEqual(sorted(t["input_schema"]["required"]), sorted(t["input_schema"]["properties"]))

    def test_mcp_tool_defs_pass_schemas_through(self):
        defs = D.mcp_tool_defs([{"name": "query_prometheus", "description": "d",
                                 "inputSchema": {"$schema": "x", "type": "object",
                                                 "properties": {"expr": {"type": "string"}}}},
                                {"name": "list_datasources"}])
        self.assertNotIn("$schema", defs[0]["input_schema"])
        self.assertEqual(defs[0]["input_schema"]["properties"]["expr"], {"type": "string"})
        self.assertEqual(defs[1]["input_schema"], {"type": "object", "properties": {}})
        self.assertFalse(any(d["strict"] for d in defs))
        self.assertGreater(D.tool_defs_tokens(defs), 10)

    def test_mcp_argv_keeps_token_out_of_argv_and_disables_rendering(self):
        argv = D.mcp_argv()
        self.assertIn("GRAFANA_SERVICE_ACCOUNT_TOKEN", argv)
        self.assertFalse(any(a.startswith("GRAFANA_SERVICE_ACCOUNT_TOKEN=") for a in argv))
        self.assertIn("--disable-rendering", argv)
        self.assertIn("--disable-write", argv)
        self.assertTrue(any(a.startswith("grafana/mcp-grafana:") and a[-1].isdigit() for a in argv))  # pinned


FAKE_SERVER = r'''
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m or "method" not in m:  # notifications, and replies to our ping
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": m["params"]["protocolVersion"], "capabilities": {"tools": {}},
             "serverInfo": {"name": "fake", "version": "1"}}
    elif m["method"] == "tools/list":
        if not m["params"].get("cursor"):
            r = {"tools": [{"name": "a", "inputSchema": {"type": "object"}}], "nextCursor": "p2"}
        else:
            r = {"tools": [{"name": "b", "description": "B", "inputSchema": {"type": "object"}}]}
    elif m["method"] == "tools/call":
        if m["params"]["name"] == "boom":
            r = {"content": [{"type": "text", "text": "bad"}], "isError": True}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": 999, "method": "ping"}) + "\n")
            r = {"content": [{"type": "text", "text": json.dumps(m["params"]["arguments"])}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "nope"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
'''


class McpClientTest(unittest.TestCase):
    def test_stdio_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            srv = os.path.join(d, "srv.py")
            with open(srv, "w") as f:
                f.write(FAKE_SERVER)
            c = D.McpStdioClient([sys.executable, "-I", srv], timeout=20)
            try:
                self.assertEqual(c.server_info["serverInfo"]["name"], "fake")
                self.assertEqual([t["name"] for t in c.list_tools()], ["a", "b"])  # follows nextCursor
                self.assertEqual(json.loads(c.call_tool("a", {"x": 1})), {"x": 1})
                self.assertTrue(c.call_tool("boom", {}).startswith("error: "))
                with self.assertRaises(D.McpError):
                    c._request("nope/method", {})
            finally:
                c.close()


class FakeGrafana:
    """Stands in for GrafanaBackend (same hooks) so the harness plumbing runs without docker."""
    name = "fake-grafana"

    def arm_tools(self, arm):
        if arm == "visual":
            return [dict(t) for t in D.VISUAL_TOOLS]
        if arm == "mcp":
            return D.mcp_tool_defs([{"name": "list_datasources"}, {"name": "list_prometheus_metric_names"}])
        return None

    def arm_context(self, incident_id, arm):
        return "\n\nCTX incident=\"%s\"" % prepare.opaque(incident_id, prepare.DEFAULT_SALT, "grafana")

    def call(self, incident_id, arm, tool, args):
        if arm == "visual":
            return [{"type": "text", "text": "panel"}, D.image_block(PNG)], {"tool_ms": 1, "image_bytes": len(PNG)}
        return tools.cap("x" * 9000), {"tool_ms": 1}


class HarnessPlumbingTest(unittest.TestCase):
    def test_visual_and_mcp_runs_with_images_and_backend_tools(self):
        iid = "rcaeval:" + CASE
        for arm in tools.GRAFANA_ARMS:
            model = harness.MockModel(arm, {"services": ["frontend"], "fault": "cpu", "evidence": [], "summary": "m"})
            seen = {}
            orig = model.turn

            def turn(messages, tool_defs, system, _o=orig):
                seen.setdefault("tools", [t["name"] for t in tool_defs])
                seen.setdefault("user", messages[0]["content"])
                seen["last"] = messages[-1]
                return _o(messages, tool_defs, system)
            model.turn = turn
            o = harness.run_one(iid, arm, model, FakeGrafana())
            d = o["detail"]
            self.assertTrue(d["grade"]["submitted"], arm)
            self.assertEqual(o["operations"], 2)
            self.assertIn("submit_answer", seen["tools"])
            self.assertNotIn(CASE, seen["user"])
            self.assertIn("CTX incident=", seen["user"])
            for c in d["calls"]:
                self.assertIsInstance(c["output"], str)
                self.assertLessEqual(c["chars"], tools.OUTPUT_CAP + 100)
            if arm == "visual":
                self.assertEqual(seen["tools"][:2], ["get_dashboard", "render_panel"])
                self.assertIn("[image image/png", d["calls"][0]["output"])
                self.assertEqual(d["calls"][0]["image_bytes"], len(PNG))
            else:
                self.assertEqual(seen["tools"][:2], ["list_datasources", "list_prometheus_metric_names"])
                self.assertIn("[truncated", d["calls"][0]["output"])

    def test_openai_adapter_sends_images_as_input_image(self):
        content = [{"type": "text", "text": "panel 1"}, D.image_block(PNG)]
        out = harness.OpenAIModel._output(content)
        self.assertEqual(out[0], {"type": "input_text", "text": "panel 1"})
        self.assertEqual(out[1]["type"], "input_image")
        self.assertTrue(out[1]["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(harness.OpenAIModel._output("plain"), "plain")
        tl = harness.OpenAIModel._tools(D.mcp_tool_defs([{"name": "q"}]) + D.VISUAL_TOOLS)
        self.assertEqual([t["strict"] for t in tl], [False, True, True])

    def test_content_text(self):
        t = D.content_text([{"type": "text", "text": "hi"}, D.image_block(PNG)])
        self.assertTrue(t.startswith("hi\n[image image/png %d bytes" % len(PNG)))


@unittest.skipUnless(D.docker_ok() and (D.load_state() or {}).get("incidents"), "Grafana stack not up/loaded")
class StackTest(unittest.TestCase):
    """Live checks against the running stack (python3 -m agentlab grafana up/load)."""

    @classmethod
    def setUpClass(cls):
        if D.stack_status().get("grafana") != "up":
            raise unittest.SkipTest("grafana not reachable")
        cls.b = D.GrafanaBackend()
        oid = sorted(cls.b.state["incidents"])[0]
        cls.iid = None
        from agentlab import rcaeval
        for c in rcaeval.load_subset():
            if prepare.opaque("rcaeval:" + c["case"], prepare.DEFAULT_SALT, "grafana") == oid:
                cls.iid = "rcaeval:" + c["case"]
        if cls.iid is None:
            raise unittest.SkipTest("no loaded RCAEval incident")

    @classmethod
    def tearDownClass(cls):
        cls.b.close()

    def test_render_panel_png(self):
        out, meta = self.b.call(self.iid, "visual", "render_panel", {"panel": 1, "service": "all",
                                                                     "from_min": 0, "to_min": 10})
        raw = base64.b64decode(out[1]["source"]["data"])
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(meta["image_bytes"], 5000)

    def test_mcp_real_tools_no_rendering(self):
        names = {t["name"] for t in self.b.mcp_tools()}
        self.assertIn("query_prometheus", names)
        self.assertIn("query_loki_logs", names)
        self.assertNotIn("get_panel_image", names)
        out, _ = self.b.call(self.iid, "mcp", "list_prometheus_metric_names", {"datasourceUid": "prom"})
        self.assertIn("app_cpu", out)


if __name__ == "__main__":
    unittest.main()

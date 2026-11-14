"""The two Grafana arms: `visual` (rendered dashboard PNGs) and `mcp` (the official mcp-grafana server).

Stack (agentlab/grafana/docker-compose.yml, pinned images): Prometheus, Loki, Grafana with
provisioned datasources and one generic dashboard, grafana-image-renderer. Many incidents share
one stack: every series and log stream carries incident="<grafana opaque id>" (the salted hash
prepare.py uses for the grafana/ tree), and every query selects by it. Case names never enter
the stack.

  python3 -m agentlab grafana up                       # start, create a read-only service account
  python3 -m agentlab grafana load --root DIR [--incidents ids]   # backfill metrics.om, push loki.json
  python3 -m agentlab grafana status | down

Metric model (generic, the same for every suite): a source series <service>_<stat> (RCAEval) or
<component>.<rest> (synthetic) becomes app_<stat>{service, incident}. The dashboard has one
time-series panel per stat found in the data (all services, one line each) plus log-volume,
error-log-volume and raw error-log panels. Nothing in it is chosen per fault.

Docker-free parts (conversion, dashboard JSON, the MCP stdio client, image blocks) are unit
tested; anything needing the stack raises StackUnavailable with setup instructions.
"""
import base64
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .prepare import DEFAULT_SALT, opaque
from .tools import OUTPUT_CAP, cap

HERE = os.path.dirname(os.path.abspath(__file__))
STACK_DIR = os.path.normpath(os.path.join(HERE, "..", "grafana"))
STATE_FILE = os.path.join(STACK_DIR, ".state.json")
IMPORT_DIR = os.path.join(STACK_DIR, ".import")
DASH_DIR = os.path.join(STACK_DIR, "dashboards")
GRAFANA_URL = "http://127.0.0.1:13000"
LOKI_URL = "http://127.0.0.1:13100"
PROM_URL = "http://127.0.0.1:19090"
DASH_UID = "svc-overview"
MCP_IMAGE = "grafana/mcp-grafana:2.1.1"
# Real tool set, minus image rendering (the mcp arm is the text interface) and minus write tools
# (runs share one Grafana; an agent must not change what the next run sees).
MCP_ARGS = ["-t", "stdio", "--disable-rendering", "--disable-write", "--usage-stats", "disabled",
            "--log-level", "error"]
COMPOSE_PROJECT = "agentlab-grafana"

# Image budget per tool result (the visual analogue of OUTPUT_CAP): fixed pixel sizes.
PANEL_PX = (1000, 500)
DASH_WIDTH = 1400

SETUP = """the Grafana arms need the docker stack:
  1. install/start Docker Desktop (docker info must work)
  2. cd agentlab && python3 -m agentlab grafana up
  3. python3 -m agentlab grafana load --root <prepared root, e.g. ../data-ext/al-rcaeval> [--incidents ids]
  4. python3 -m agentlab run --backend grafana --root <same root> --arms visual,mcp ..."""


class StackUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- conversion (no docker)

_SAFE = re.compile(r"[^a-zA-Z0-9_]")
_OM_LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)\{component="((?:[^"\\]|\\.)*)"\} (\S+) (\S+)$')


def split_series(series):
    """Source series name -> (service, stat). RCAEval: <service>_<stat>; synthetic: <component>.<rest>."""
    if "." in series:
        svc, stat = series.split(".", 1)
    elif "_" in series:
        svc, stat = series.rsplit("_", 1)
    else:
        svc, stat = "", series
    return svc, stat


def metric_name(stat):
    return "app_" + _SAFE.sub("_", stat).strip("_").lower()


def convert_openmetrics(src, dst, oid):
    """bundle.py's metrics.om (one family per source series, label component=<series>) ->
    app_<stat>{service, incident} families. Returns {stats, services, start_ms, end_ms, samples}."""
    fam = {}
    t_min, t_max, n = None, None, 0
    with open(src) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            m = _OM_LINE.match(line.rstrip("\n"))
            if not m:
                continue
            series, value, ts = m.group(2), m.group(3), m.group(4)
            svc, stat = split_series(series)
            fam.setdefault(metric_name(stat), []).append((svc, value, ts))
            t = float(ts)
            t_min = t if t_min is None or t < t_min else t_min
            t_max = t if t_max is None or t > t_max else t_max
            n += 1
    with open(dst, "w") as f:
        for name in sorted(fam):
            f.write("# TYPE %s gauge\n" % name)
            for svc, value, ts in sorted(fam[name], key=lambda r: (r[0], float(r[2]))):
                f.write('%s{incident="%s",service="%s"} %s %s\n' % (name, oid, svc, value, ts))
        f.write("# EOF\n")
    return {"stats": sorted(fam), "services": sorted({s for v in fam.values() for s, _, _ in v}),
            "start_ms": int(t_min * 1000) if t_min is not None else None,
            "end_ms": int(t_max * 1000) if t_max is not None else None, "samples": n}


def loki_batches(src, oid, max_values=20000):
    """loki.json push body -> smaller push bodies with incident=<oid> on every stream."""
    with open(src) as f:
        body = json.load(f)
    out, cur, k = [], [], 0
    for s in body["streams"]:
        labels = dict(s["stream"], incident=oid)
        vals, last = [], -1
        for ts, line in s["values"]:  # logs have 1 s resolution: Loki would drop repeated (ts, line) pairs,
            t = int(ts)                # so lines sharing a timestamp get +1 ns each (order kept, counts exact)
            last = t if t > last else last + 1
            vals.append([str(last), line])
        for i in range(0, len(vals), max_values):
            chunk = vals[i:i + max_values]
            cur.append({"stream": labels, "values": chunk})
            k += len(chunk)
            if k >= max_values:
                out.append({"streams": cur})
                cur, k = [], 0
    if cur:
        out.append({"streams": cur})
    return out


_ORDER = ["cpu", "mem", "latency", "error", "workload", "socket", "diskio"]


def _stat_key(name):
    s = name[len("app_"):]
    for i, p in enumerate(_ORDER):
        if s.startswith(p):
            return (i, s)
    return (len(_ORDER), s)


def build_dashboard(stats):
    """One generic overview dashboard: a panel per metric family (one line per service), log
    volume, error-log volume, raw error logs. Variables: incident (hidden), service (multi)."""
    panels = []
    cur = {"x": 0, "y": 0}
    stats = sorted(set(stats), key=_stat_key)

    def add(p, w=12, h=8):
        if cur["x"] + w > 24:
            cur["x"], cur["y"] = 0, cur["y"] + 8
        p.update({"id": len(panels) + 1, "gridPos": {"x": cur["x"], "y": cur["y"], "w": w, "h": h}})
        cur["x"] += w
        panels.append(p)

    for name in stats:
        add({"type": "timeseries", "title": "%s by service" % name[len("app_"):],
             "datasource": {"type": "prometheus", "uid": "prom"},
             "targets": [{"refId": "A", "expr": '%s{incident="$incident", service=~"$service"}' % name,
                          "legendFormat": "{{service}}", "datasource": {"type": "prometheus", "uid": "prom"}}],
             "fieldConfig": {"defaults": {"custom": {"lineWidth": 1, "showPoints": "never"}}, "overrides": []},
             "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["mean", "max"]},
                         "tooltip": {"mode": "multi"}}})
    loki = {"type": "loki", "uid": "loki"}
    for title, sel in (("log lines by service", '{incident="$incident", component=~"$service"}'),
                       ("ERROR log lines by service", '{incident="$incident", component=~"$service", level="ERROR"}')):
        add({"type": "timeseries", "title": title, "datasource": loki,
             "targets": [{"refId": "A", "expr": "sum by (component) (count_over_time(%s [$__auto]))" % sel,
                          "legendFormat": "{{component}}", "datasource": loki}],
             "fieldConfig": {"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60}}, "overrides": []},
             "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["sum"]}}})
    add({"type": "logs", "title": "ERROR and WARN logs", "datasource": loki,
         "targets": [{"refId": "A", "expr": '{incident="$incident", component=~"$service", level=~"ERROR|WARN"}',
                      "datasource": loki, "maxLines": 200}],
         "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"}}, w=24, h=10)
    return {"uid": DASH_UID, "title": "Service overview", "editable": False, "schemaVersion": 39,
            "timezone": "utc", "time": {"from": "now-1h", "to": "now"}, "refresh": "",
            "templating": {"list": [
                {"name": "incident", "type": "textbox", "hide": 2, "query": "", "current": {"text": "", "value": ""}},
                {"name": "service", "type": "query", "datasource": {"type": "prometheus", "uid": "prom"},
                 "query": {"query": 'label_values({incident="$incident"}, service)', "refId": "v"},
                 "definition": 'label_values({incident="$incident"}, service)', "refresh": 2,
                 "multi": True, "includeAll": True, "allValue": ".*",
                 "current": {"text": "All", "value": "$__all"}}]},
            "panels": panels}


def panel_index(dash):
    return [{"id": p["id"], "title": p["title"]} for p in dash["panels"]]


# ---------------------------------------------------------------- state and stack (docker)

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_FILE)


def docker_ok():
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _compose(args, env=None, check=True, **kw):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run(["docker", "compose", "-p", COMPOSE_PROJECT, "-f", os.path.join(STACK_DIR, "docker-compose.yml")]
                       + args, cwd=STACK_DIR, env=e, capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        raise StackUnavailable("docker compose %s failed: %s" % (" ".join(args[:2]), (r.stderr or r.stdout)[-800:]))
    return r


def _http(method, url, body=None, auth=None, headers=None, timeout=60, raw=False):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    if auth and auth[0] == "basic":
        h["Authorization"] = "Basic " + base64.b64encode(("%s:%s" % auth[1:]).encode()).decode()
    elif auth and auth[0] == "bearer":
        h["Authorization"] = "Bearer " + auth[1]
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        return b if raw else (json.loads(b) if b else None)


def _wait(url, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=5).read()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(2)
    return False


def _env(st):
    return {"AGENTLAB_GRAFANA_ADMIN_PASSWORD": st["admin_password"],
            "AGENTLAB_RENDERER_TOKEN": st.get("renderer_token", "unset")}


def stack_up():
    if not docker_ok():
        raise StackUnavailable("Docker is not running.\n" + SETUP)
    st = load_state() or {"admin_password": secrets.token_urlsafe(18), "incidents": {}, "stats": []}
    st.setdefault("renderer_token", secrets.token_urlsafe(18))
    os.makedirs(IMPORT_DIR, exist_ok=True)
    os.makedirs(DASH_DIR, exist_ok=True)
    write_dashboard(st.get("stats") or [])
    save_state(st)
    _compose(["up", "-d"], env=_env(st))
    for u in (GRAFANA_URL + "/api/health", LOKI_URL + "/ready", PROM_URL + "/-/ready"):
        if not _wait(u, 180):
            raise StackUnavailable("not ready: %s" % u)
    if not st.get("sa_token"):
        adm = ("basic", "admin", st["admin_password"])
        sa = _http("POST", GRAFANA_URL + "/api/serviceaccounts", {"name": "agentlab", "role": "Viewer"}, auth=adm)
        tok = _http("POST", GRAFANA_URL + "/api/serviceaccounts/%d/tokens" % sa["id"], {"name": "agentlab"}, auth=adm)
        st["sa_token"] = tok["key"]
        save_state(st)
    return st


def stack_down(volumes=True):
    st = load_state() or {"admin_password": "x"}
    _compose(["down"] + (["-v"] if volumes else []), env=_env(st), check=False)
    if volumes and os.path.exists(STATE_FILE):
        st.pop("sa_token", None)
        st["incidents"], st["stats"] = {}, []
        save_state(st)


def write_dashboard(stats):
    os.makedirs(DASH_DIR, exist_ok=True)
    with open(os.path.join(DASH_DIR, "overview.json"), "w") as f:
        json.dump(build_dashboard(stats), f, indent=1)


def stack_load(root, incident_ids=None, salt=DEFAULT_SALT, log=print):
    """Backfill each incident's grafana/<oid>/ tree. incident_ids None = every directory under root/grafana."""
    st = load_state()
    if not st or not st.get("sa_token"):
        raise StackUnavailable("stack not up.\n" + SETUP)
    gdir = os.path.join(root, "grafana")
    if incident_ids:
        oids = [opaque(i, salt, "grafana") for i in incident_ids]
    else:
        oids = sorted(d for d in os.listdir(gdir) if os.path.isdir(os.path.join(gdir, d)))
    new = 0
    for oid in oids:
        if oid in st["incidents"]:
            continue
        src = os.path.join(gdir, oid)
        if not os.path.exists(os.path.join(src, "metrics.om")):
            raise StackUnavailable("missing %s/metrics.om (prepare this incident first)" % src)
        t0 = time.time()
        info = convert_openmetrics(os.path.join(src, "metrics.om"), os.path.join(IMPORT_DIR, oid + ".om"), oid)
        r = _compose(["exec", "-T", "prometheus", "promtool", "tsdb", "create-blocks-from", "openmetrics",
                      "/import/%s.om" % oid, "/prometheus"], env=_env(st), check=False)
        if r.returncode != 0:
            raise StackUnavailable("promtool failed for %s: %s" % (oid, (r.stderr or r.stdout)[-600:]))
        os.remove(os.path.join(IMPORT_DIR, oid + ".om"))
        nlog = 0
        if os.path.exists(os.path.join(src, "loki.json")):
            for b in loki_batches(os.path.join(src, "loki.json"), oid):
                _http("POST", LOKI_URL + "/loki/api/v1/push", b, timeout=300)
                nlog += sum(len(s["values"]) for s in b["streams"])
        st["incidents"][oid] = {"start_ms": info["start_ms"], "end_ms": info["end_ms"],
                                "services": info["services"], "samples": info["samples"], "log_lines": nlog}
        st["stats"] = sorted(set(st.get("stats", [])) | set(info["stats"]))
        save_state(st)
        new += 1
        log("loaded %s: %d samples, %d log lines, %.1fs" % (oid, info["samples"], nlog, time.time() - t0))
    if new:
        _loki_flush()
        write_dashboard(st["stats"])
        _compose(["restart", "prometheus"], env=_env(st))  # pick up the new blocks
        _wait(PROM_URL + "/-/ready", 120)
        _wait(GRAFANA_URL + "/api/health", 60)
    return st


def _loki_flush(timeout=300):
    """Flush pushed logs from the ingester to the store: Loki's querier does not reach the ingester
    for these historical time ranges, so unflushed lines would be invisible to queries."""
    _http("POST", LOKI_URL + "/flush", b"", timeout=60)
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = urllib.request.urlopen(LOKI_URL + "/metrics", timeout=10).read().decode()
        mem = re.search(r"^loki_ingester_memory_chunks (\S+)$", m, re.M)
        q = re.search(r"^loki_ingester_flush_queue_length (\S+)$", m, re.M)
        if (not mem or float(mem.group(1)) == 0) and (not q or float(q.group(1)) == 0):
            return
        time.sleep(2)
    raise StackUnavailable("Loki flush did not finish in %ds" % timeout)


def stack_status():
    st = load_state()
    out = {"docker": docker_ok(), "state": bool(st), "incidents": len((st or {}).get("incidents", {}))}
    for name, u in (("grafana", GRAFANA_URL + "/api/health"), ("loki", LOKI_URL + "/ready"),
                    ("prometheus", PROM_URL + "/-/ready")):
        try:
            urllib.request.urlopen(u, timeout=3).read()
            out[name] = "up"
        except Exception as e:  # noqa: BLE001
            out[name] = "down (%s)" % type(e).__name__
    return out


# ---------------------------------------------------------------- MCP stdio client (no SDK)

class McpError(RuntimeError):
    pass


class McpStdioClient:
    """Minimal MCP client over stdio (newline-delimited JSON-RPC 2.0): initialize, tools/list,
    tools/call. Stdlib only, so it also runs under the system python3."""

    PROTOCOL = "2025-06-18"

    def __init__(self, argv, env=None, timeout=120):
        self.argv, self.timeout = argv, timeout
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=dict(os.environ, **(env or {})), text=True, bufsize=1)
        self._id, self._lock = 0, threading.Lock()
        self._err = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.server_info = self._request("initialize", {
            "protocolVersion": self.PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "agentlab", "version": "0.1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _drain_stderr(self):
        for line in self.p.stderr:
            self._err.append(line)
            del self._err[:-50]

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def _request(self, method, params):
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            box = {}

            def read():
                while True:
                    line = self.p.stdout.readline()
                    if not line:
                        box["eof"] = True
                        return
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("id") == rid and ("result" in msg or "error" in msg):
                        box["msg"] = msg
                        return
                    if "method" in msg and "id" in msg:  # server->client request (e.g. ping): answer it
                        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

            t = threading.Thread(target=read, daemon=True)
            t.start()
            t.join(self.timeout)
            if t.is_alive():
                self.close()
                raise McpError("MCP %s timed out after %ds" % (method, self.timeout))
            if "eof" in box:
                raise McpError("MCP server exited: %s" % "".join(self._err[-5:]).strip()[-400:])
            msg = box["msg"]
            if "error" in msg:
                raise McpError("MCP %s error: %s" % (method, msg["error"].get("message")))
            return msg["result"]

    def list_tools(self):
        tools, cursor = [], None
        while True:
            r = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools += r.get("tools", [])
            cursor = r.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, args):
        r = self._request("tools/call", {"name": name, "arguments": args})
        parts = []
        for c in r.get("content", []):
            if c.get("type") == "text":
                parts.append(c["text"])
            elif c.get("type") == "resource":
                parts.append(c.get("resource", {}).get("text", ""))
            else:
                parts.append("[%s content omitted]" % c.get("type"))
        if not parts and r.get("structuredContent") is not None:
            parts.append(json.dumps(r["structuredContent"]))
        text = "\n".join(parts)
        return ("error: " + text) if r.get("isError") else text

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.p.terminate()
            self.p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.p.kill()
        for s in (self.p.stdout, self.p.stderr):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass


def mcp_argv(network=COMPOSE_PROJECT + "_default"):
    """docker run argv for mcp-grafana over stdio. The token is passed by env name only (not argv)."""
    return ["docker", "run", "--rm", "-i", "--network", network, "-e", "GRAFANA_URL=http://grafana:3000",
            "-e", "GRAFANA_SERVICE_ACCOUNT_TOKEN", "--entrypoint", "/app/mcp-grafana", MCP_IMAGE] + MCP_ARGS


def mcp_tool_defs(mcp_tools):
    """MCP tool list -> harness tool definitions (Anthropic shape; the OpenAI adapter converts).
    Descriptions and schemas are passed through unchanged; no strict mode (MCP schemas are not strict)."""
    out = []
    for t in mcp_tools:
        schema = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
        schema.pop("$schema", None)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        out.append({"name": t["name"], "description": t.get("description", ""), "input_schema": schema,
                    "strict": False})
    return out


def tool_defs_tokens(defs):
    """Rough size of a tool list: chars/4 of its JSON (the live run also records the provider's count)."""
    return len(json.dumps(defs)) // 4


# ---------------------------------------------------------------- visual tools

VISUAL_TOOLS = [
    {"name": "get_dashboard",
     "description": ("Get the incident's 'Service overview' Grafana dashboard as one PNG image covering the whole "
                     "telemetry window, plus the list of panel ids and titles."),
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
     "strict": True},
    {"name": "render_panel",
     "description": ("Render one dashboard panel as a %dx%d PNG. panel: a panel id from get_dashboard. service: "
                     "one service name, or 'all'. from_min/to_min: minutes from the start of the telemetry window "
                     "(e.g. 0 and 30 for the whole window)." % PANEL_PX),
     "input_schema": {"type": "object", "additionalProperties": False,
                      "properties": {"panel": {"type": "integer"}, "service": {"type": "string"},
                                     "from_min": {"type": "number"}, "to_min": {"type": "number"}},
                      "required": ["panel", "service", "from_min", "to_min"]},
     "strict": True},
]


def image_block(png):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png).decode()}}


def content_text(out):
    """Text view of a tool result that may hold images (for grading, logs and the run record)."""
    if isinstance(out, str):
        return out
    parts = []
    for b in out:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "image":
            raw = base64.b64decode(b["source"]["data"])
            parts.append("[image %s %d bytes sha=%s]" % (b["source"]["media_type"], len(raw),
                                                        hashlib.sha256(raw).hexdigest()[:10]))
    return "\n".join(parts)


def _iso(ms):
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GrafanaBackend:
    """Executes the visual and mcp arms against the stack; any other arm goes to `fallback`
    (a LocalBackend), so one run can compare all arms."""
    name = "grafana"
    ARMS = ("visual", "mcp")

    def __init__(self, fallback=None, salt=DEFAULT_SALT, image_dir=None, state=None):
        self.fallback, self.salt, self.image_dir = fallback, salt, image_dir
        self.state = state or load_state()
        if not self.state or not self.state.get("sa_token"):
            raise StackUnavailable("Grafana stack is not up.\n" + SETUP)
        self._mcp, self._mcp_tools = None, None
        self.dash = build_dashboard(self.state.get("stats") or [])

    # -- harness hooks (optional; harness checks with getattr)
    def arm_tools(self, arm):
        if arm == "visual":
            return [dict(t) for t in VISUAL_TOOLS]
        if arm == "mcp":
            return mcp_tool_defs(self.mcp_tools())
        return None

    def arm_context(self, incident_id, arm):
        if arm not in self.ARMS:
            return ""
        inc = self._incident(incident_id)
        win = "%s to %s (UTC), about %d minutes" % (_iso(inc["start_ms"]), _iso(inc["end_ms"]),
                                                      round((inc["end_ms"] - inc["start_ms"]) / 60000))
        if arm == "visual":
            return "\n\nThe telemetry is shown in Grafana dashboards. Window: %s." % win
        return ("\n\nThe telemetry is in Grafana (Prometheus and Loki datasources). This incident's series and log "
                "streams carry the label incident=\"%s\"; other incidents share the same Grafana, so always select "
                "by it. Window: %s. The dashboard 'Service overview' (uid %s, variable incident) shows it."
                % (self._oid(incident_id), win, DASH_UID))

    # --
    def _oid(self, incident_id):
        base = incident_id.replace("rcaeval+t:", "rcaeval:", 1)  # same telemetry for the matched-time variant
        return opaque(base, self.salt, "grafana")

    def _incident(self, incident_id):
        oid = self._oid(incident_id)
        inc = self.state["incidents"].get(oid)
        if inc is None:
            raise StackUnavailable("incident %s (%s) is not loaded; run: python3 -m agentlab grafana load "
                                   "--root <root> --incidents %s" % (incident_id, oid, incident_id))
        return inc

    def mcp_tools(self):
        if self._mcp_tools is None:
            self._mcp_tools = self._client().list_tools()
        return self._mcp_tools

    def _client(self):
        if self._mcp is None or self._mcp.p.poll() is not None:
            self._mcp = McpStdioClient(mcp_argv(), env={"GRAFANA_SERVICE_ACCOUNT_TOKEN": self.state["sa_token"]})
        return self._mcp

    def close(self):
        if self._mcp:
            self._mcp.close()

    def call(self, incident_id, arm, tool, args):
        if arm not in self.ARMS:
            if self.fallback is None:
                return "error: arm %s needs another backend" % arm, {"tool_ms": 0}
            return self.fallback.call(incident_id, arm, tool, args)
        t0 = time.perf_counter()
        if arm == "mcp":
            try:
                out = cap(self._client().call_tool(tool, args))
            except McpError as e:
                from .harness import InfraError
                raise InfraError(str(e)) from None
            return out, {"tool_ms": (time.perf_counter() - t0) * 1000}
        inc = self._incident(incident_id)
        if tool == "get_dashboard":
            png = self._render(inc, incident_id, None, "all", inc["start_ms"], inc["end_ms"])
            idx = "\n".join("  panel %d: %s" % (p["id"], p["title"]) for p in panel_index(self.dash))
            text = "Service overview, whole window. Panels:\n" + idx
        elif tool == "render_panel":
            ids = {p["id"]: p["title"] for p in panel_index(self.dash)}
            try:
                pid = int(args.get("panel"))
            except (TypeError, ValueError):
                pid = -1
            if pid not in ids:
                return "error: unknown panel %r; valid ids: %s" % (args.get("panel"), sorted(ids)), {"tool_ms": 0}
            svc = str(args.get("service") or "all")
            if svc != "all" and svc not in inc["services"]:
                return "error: unknown service %r; valid: all, %s" % (svc, ", ".join(inc["services"])), {"tool_ms": 0}
            span = inc["end_ms"] - inc["start_ms"]
            a = max(0.0, float(args.get("from_min") or 0)) * 60000
            b = min(span, float(args.get("to_min") or 0) * 60000)
            if b - a < 30000:
                return ("error: the window must be at least 0.5 minutes, within 0..%.1f"
                        % (span / 60000)), {"tool_ms": 0}
            png = self._render(inc, incident_id, pid, svc, inc["start_ms"] + int(a), inc["start_ms"] + int(b))
            text = "panel %d (%s), service=%s, minutes %.1f-%.1f" % (pid, ids[pid], svc, a / 60000, b / 60000)
        else:
            return "error: unknown tool %s for arm visual" % tool, {"tool_ms": 0}
        if self.image_dir:
            os.makedirs(self.image_dir, exist_ok=True)
            h = hashlib.sha256(png).hexdigest()[:12]
            with open(os.path.join(self.image_dir, "%s-%s.png" % (self._oid(incident_id), h)), "wb") as f:
                f.write(png)
        return ([{"type": "text", "text": cap(text)}, image_block(png)],
                {"tool_ms": (time.perf_counter() - t0) * 1000, "image_bytes": len(png)})

    def _render(self, inc, incident_id, panel_id, service, t_from, t_to):
        q = [("orgId", "1"), ("from", str(t_from)), ("to", str(t_to)), ("tz", "UTC"),
             ("var-incident", self._oid(incident_id)), ("theme", "light")]
        q.append(("var-service", "$__all" if service == "all" else service))
        if panel_id is None:
            rows = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in self.dash["panels"])
            q += [("width", str(DASH_WIDTH)), ("height", str(rows * 36 + 40)), ("kiosk", "true")]
            path = "/render/d/%s/overview" % DASH_UID
        else:
            q += [("panelId", str(panel_id)), ("width", str(PANEL_PX[0])), ("height", str(PANEL_PX[1]))]
            path = "/render/d-solo/%s/overview" % DASH_UID
        url = GRAFANA_URL + path + "?" + urllib.parse.urlencode(q)
        try:
            return _http("GET", url, auth=("bearer", self.state["sa_token"]), timeout=120, raw=True)
        except (urllib.error.URLError, OSError) as e:
            from .harness import InfraError
            raise InfraError("render failed: %s" % e) from None

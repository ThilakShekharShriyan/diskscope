"""Arm `rcatools`: RCAEval's own agent tools (RCAEval.agent.toolkit, v1.12.0), run locally.

RCAEval (since v1.10) ships an LLM-agent interface: `rcaeval-agent export` writes a
blind task view (anonymised case ids, telemetry CSVs, task.json), CaseToolkit serves
8 narrow tools over one case, each result a JSON dict that `cap()` shrinks (halving
its longest list) to MAX_CHARS = 8000 chars of JSON. The same tools back their
reference agent (react.py, LiteLLM) and their MCP server.

This arm gives the agent those tools unchanged: same names, JSON schemas,
descriptions and output cap, executed by RCAEval's CaseToolkit itself over a task view
we build from the raw case (data-ext/rcaeval/<case>). Differences from RCAEval's setup,
all deliberate so that only the tools differ from the other AgentLab arms:
- The prompt and the final tool are the suite's (rcaeval.SYSTEM, submit_answer with
  services + fault type), not RCAEval's SYSTEM_PROMPT_V2 + submit(ranks). Their
  `submit` tool is therefore not offered.
- Turn/tool-call caps and effort are the harness's (20 turns), not their 30 steps,
  temperature 0 and forced final submit.
- Their tool results are already capped (8000 chars of JSON), so the harness cap is not
  applied on top; it is applied only if a result still exceeds OUTPUT_CAP (cap() can
  not shrink a result with no list), recorded as meta["cap"] = "agentlab".

The task view exposes the INJECTION TIME (describe_case, and every before/after split
in get_metric, rank_anomalies, search_logs, trace_summary). It cannot be removed
without changing the tools, so this arm is a time-given condition: compare it with the
`+time` arms (ids rcaeval+t:...), not the blind ones. The report labels it rcatools+time.

The case name never reaches the agent: the view lives in an opaque directory, task.json
holds an opaque case_id, and any path in an error message is replaced before the result
is returned. Labels (inject_time.txt is read, root cause never) stay outside the view.

Derived files, as RCAEval.convert.derive builds them for converted data (the Hugging Face
copy has only metrics/logs/traces Parquet): tracets_err/tracets_lat from the traces,
logts + log_templates.json from the logs with Drain3. Drain3 is needed only at prepare
time; without it the view has no logts and rank_anomalies(logs) says so.
"""
import json
import os
import time

from .prepare import DEFAULT_SALT, opaque

RCAEVAL_VERSION = "1.12.0"
MAX_CHARS = 8000  # RCAEval.agent.toolkit.MAX_CHARS
STANDARD_LENGTH_MIN = 20  # RCAEval.agent.export.STANDARD_LENGTH
DATASET = "re2-ob"

# Verbatim copy of RCAEval.agent.toolkit.TOOLS (v1.12.0) without `submit`; the copy keeps this
# module importable without pandas. tests/test_rcatools.py checks it against the installed package.
RCAEVAL_TOOLS = [
    {
        "name": "describe_case",
        "description": "Overview of the case: system, inject time, standard window, available telemetry and candidate services. Call this first.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_metrics",
        "description": "Metric names. Without a service, the indicator suffixes per service; with a service, that service's full metric names.",
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string", "description": "Only this service's metrics."},
        }},
    },
    {
        "name": "get_metric",
        "description": "One metric over time, averaged into at most `points` buckets, with its mean and max before and after the inject time.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Metric name, e.g. cartservice_cpu."},
            "start": {"type": "integer", "description": "Unix seconds (default: window start)."},
            "end": {"type": "integer", "description": "Unix seconds (default: window end)."},
            "points": {"type": "integer", "description": "Number of buckets (default 40, max 120)."},
        }, "required": ["name"]},
    },
    {
        "name": "rank_anomalies",
        "description": (
            "Series ranked by how far they move after the inject time (BARO-style robust z-score). "
            "modality: metrics, logs (log template counts), trace_latency or trace_errors."
        ),
        "parameters": {"type": "object", "properties": {
            "modality": {"type": "string", "enum": ["metrics", "logs", "trace_latency", "trace_errors"]},
            "top_k": {"type": "integer", "description": "Number of series to return (default 20, max 100)."},
        }},
    },
    {
        "name": "search_logs",
        "description": (
            "Log lines matching a case-insensitive regex, optionally for one service, grouped by message "
            "pattern with counts before and after the inject time, largest increase first."
        ),
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string"},
            "pattern": {"type": "string", "description": "Regex, e.g. 'error|exception|timeout'."},
            "start": {"type": "integer"},
            "end": {"type": "integer"},
            "limit": {"type": "integer", "description": "Number of groups (default 20, max 50)."},
        }},
    },
    {
        "name": "trace_summary",
        "description": (
            "Per service and operation: requests, errors and p50/p90 latency (ms) before and after the "
            "inject time, largest change first."
        ),
        "parameters": {"type": "object", "properties": {
            "service": {"type": "string"},
            "start": {"type": "integer"},
            "end": {"type": "integer"},
            "top_k": {"type": "integer", "description": "Number of rows (default 20, max 100)."},
        }},
    },
    {
        "name": "call_graph",
        "description": "Which service calls which, with call counts, from the traces.",
        "parameters": {"type": "object", "properties": {}},
    },
]
TOOL_NAMES = [t["name"] for t in RCAEVAL_TOOLS]

# Harness format. strict=False: their schemas have optional properties and no
# additionalProperties:false, which OpenAI strict mode rejects; the schema itself is unchanged.
ARM_TOOLS = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"], "strict": False}
             for t in RCAEVAL_TOOLS]


def case_of(incident_id):
    return incident_id.split(":", 1)[1] if ":" in incident_id else incident_id


def view_id(incident_id, salt=DEFAULT_SALT):
    """Opaque directory name / case_id; the same for rcaeval: and rcaeval+t: ids of one case."""
    return opaque("rcaeval:" + case_of(incident_id), salt, "rcatools")


def prepare_case(src_dir, out_dir, case_id, dataset=DATASET):
    """Write an `rcaeval-agent export`-shaped case directory from a Hugging Face RCAEval case
    (metrics/logs/traces.parquet + inject_time.txt). Returns the task dict."""
    import pandas as pd
    from RCAEval.agent.export import ANSWER_FORMAT, SCHEMA_VERSION, candidate_indicators, system_of
    from RCAEval.benchmark.scoring import to_service
    from RCAEval.convert.derive import trace_series

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(src_dir, "inject_time.txt")) as f:
        inject_time = int(f.readlines()[0].strip())
    files = {}

    def write(df, name, key):
        df.to_csv(os.path.join(out_dir, name), index=False)
        files[key] = name

    write(pd.read_parquet(os.path.join(src_dir, "metrics.parquet")), "metrics.csv", "metrics")

    lp = os.path.join(src_dir, "logs.parquet")
    if os.path.exists(lp):
        logs = pd.read_parquet(lp)
        write(logs, "logs.csv", "logs")
        try:
            from RCAEval.convert.derive import log_series
            ns = logs.assign(timestamp=logs["timestamp"].astype("int64") * 1_000_000_000)  # derive expects ns
            logts, cluster_info, _ = log_series(ns)
        except ImportError:  # drain3 absent: no logts in the view
            logts = None
        if logts is not None:
            write(logts, "logts.csv", "logts")
            with open(os.path.join(out_dir, "log_templates.json"), "w") as f:
                json.dump(cluster_info, f, indent=1)
            files["log_templates"] = "log_templates.json"

    tp = os.path.join(src_dir, "traces.parquet")
    if os.path.exists(tp):
        tr = pd.read_parquet(tp)
        num = tr.assign(**{c: pd.to_numeric(tr[c]).astype("float64") for c in ("startTime", "duration", "statusCode")})
        num = num.assign(startTime=num["startTime"].astype("int64"))
        err, lat = trace_series(num, inject_time)
        write(err, "tracets_err.csv", "tracets_err")
        write(lat, "tracets_lat.csv", "tracets_lat")
        # only the columns CaseToolkit.traces() reads; startTime (us) is the time column it picks
        keep = [c for c in ("startTime", "serviceName", "operationName", "spanID", "parentSpanID", "duration",
                            "statusCode") if c in tr.columns]
        write(tr[keep], "traces.csv", "traces")

    indicators = candidate_indicators(os.path.join(out_dir, "metrics.csv"))
    half = STANDARD_LENGTH_MIN * 60 // 2
    task = {"schema_version": SCHEMA_VERSION, "case_id": case_id, "dataset": dataset, "system": system_of(dataset),
            "inject_time": inject_time, "standard_window": {"start": inject_time - half, "end": inject_time + half},
            "files": files, "candidate_services": sorted({to_service(c) for c in indicators}),
            "candidate_indicators": indicators, "answer_format": ANSWER_FORMAT}
    with open(os.path.join(out_dir, "task.json"), "w") as f:
        json.dump(task, f, indent=2)
    return task


def prepare(incident_id, data_root, root, salt=DEFAULT_SALT):
    vid = view_id(incident_id, salt)
    t0 = time.time()
    task = prepare_case(os.path.join(data_root, case_of(incident_id)), os.path.join(root, "rcatools", vid), vid)
    return {"id": incident_id, "dir": vid, "files": sorted(task["files"]), "s": round(time.time() - t0, 1)}


class RcaToolsBackend:
    """RCAEval's CaseToolkit over <root>/rcatools/<view_id>/. One toolkit per case is kept
    (its DataFrame cache makes repeat calls fast); one backend per worker thread."""
    name = "local"

    def __init__(self, root, salt=DEFAULT_SALT):
        self.root, self.salt = os.path.abspath(root), salt
        self._key, self._tk = None, None

    def case_dir(self, incident_id):
        return os.path.join(self.root, "rcatools", view_id(incident_id, self.salt))

    def toolkit(self, incident_id):
        d = self.case_dir(incident_id)
        if self._key != d:
            if not os.path.exists(os.path.join(d, "task.json")):
                from .harness import InfraError
                raise InfraError("no rcatools view for this case; run `python3 -m agentlab prepare-rcatools`")
            from RCAEval.agent.toolkit import CaseToolkit
            self._key, self._tk = d, CaseToolkit(d)
        return self._tk

    def call(self, incident_id, arm, tool, args):
        t0 = time.perf_counter()
        if tool not in TOOL_NAMES:  # includes their `submit`: the suite's submit_answer replaces it
            out = json.dumps({"error": "unknown tool %r; tools: %s" % (tool, ", ".join(TOOL_NAMES))})
            return out, {"tool_ms": 0, "cap": "rcaeval"}
        tk = self.toolkit(incident_id)
        result = tk.call(tool, args)  # applies RCAEval's cap(): <= MAX_CHARS chars of JSON
        out = json.dumps(result, default=str)  # what react.py and the MCP server send
        out = out.replace(self.case_dir(incident_id), "<case>").replace(self.root, "<root>")
        meta = {"tool_ms": (time.perf_counter() - t0) * 1000, "cap": "rcaeval",
                "ok": "error" not in result, "rcaeval_truncated": bool(result.get("truncated"))}
        from .tools import OUTPUT_CAP, cap
        if len(out) > OUTPUT_CAP:  # their cap could not shrink it (no list): ours applies
            out, meta["cap"] = cap(out), "agentlab"
        return out, meta


# ---- Inspected@root (RCAEval.agent.trajectory.inspected), plus a proxy for the other arms ----
def to_service(name):
    """RCAEval.benchmark.scoring.to_service."""
    return name.split("_")[0].replace("-db", "")


def inspected_root(arm, calls, service):
    """Did a tool call before submit target the root-cause service?
    rcatools: RCAEval's rule (a `service` or `name` argument belonging to it), exact.
    sql/files: proxy - the service name appears as a token in the query or command.
    Other arms (digest, prior): None, their inputs never name services."""
    import re
    if arm == "rcatools":
        for c in calls:
            args = c.get("input") or {}
            for key in ("service", "name"):
                v = args.get(key)
                if isinstance(v, str) and to_service(v.strip().lower()) == service:
                    return True
        return False
    if arm in ("sql", "files"):
        rx = re.compile(r"(?<![A-Za-z0-9-])%s(?![A-Za-z0-9-])" % re.escape(service), re.I)
        return any(rx.search(json.dumps(c.get("input") or {})) for c in calls)
    return None

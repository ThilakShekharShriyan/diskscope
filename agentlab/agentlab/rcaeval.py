"""RCAEval (Pham et al., WWW'25; MIT) as an external AgentLab suite.

Source: https://huggingface.co/datasets/phamquiluan/RCAEval (Parquet copy of the
Zenodo release). Each case is a real fault injection into a microservice demo
app with metrics (1 s), logs and traces; the label is the root-cause service
and the fault type.

Rules (fixed before any scored run):
- Subset: RE2-OB, one repetition per (service, fault) chosen with SUBSET_SEED
  -> 30 cases. Written to rcaeval_subset.json and committed.
- The case name, cases.parquet, inject_time.txt and root_cause.txt encode or give
  away the answer; none of them is copied into any agent-visible tree.
- The agent does NOT get the injection time (RCAEval's classical baselines do).
  In RE2 every injection is at the window midpoint (12 of 24 min); this is a
  property of the benchmark, reported, not hidden by cropping.
- Conversion is mechanical: metrics keep their column names as series; logs get a
  level from one keyword rule (RCAEval logs have none); trace duration is
  microseconds -> ms; empty parent -> root span.
"""
import json
import os
import random
import re
import shutil
import urllib.request

HF = "https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/main/"
SUBSET_SEED = 20261114
FAULTS = ["cpu", "mem", "disk", "delay", "loss", "socket"]
HERE = os.path.dirname(os.path.abspath(__file__))
SUBSET_FILE = os.path.join(HERE, "rcaeval_subset.json")
FILES = ["metrics.parquet", "logs.parquet", "traces.parquet", "inject_time.txt"]

_ERR = re.compile(r"error|exception|fail|panic|fatal|refused|timeout|timed out", re.I)
_WARN = re.compile(r"warn", re.I)


def pick_subset(cases_parquet, dataset="RE2-OB", seed=SUBSET_SEED):
    import pandas as pd
    d = pd.read_parquet(cases_parquet)
    d = d[d.dataset == dataset].sort_values("case")
    rng = random.Random(seed)
    out = []
    for (svc, fault), g in d.groupby(["root_cause_service", "fault"], sort=True):
        row = g.iloc[rng.randrange(len(g))]
        out.append({"case": row["case"], "service": svc, "fault": fault})
    return out


def load_subset():
    with open(SUBSET_FILE) as f:
        return json.load(f)


def download(case, dest_root):
    d = os.path.join(dest_root, case)
    os.makedirs(d, exist_ok=True)
    for name in FILES:
        p = os.path.join(d, name)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            continue
        try:
            urllib.request.urlretrieve(HF + case + "/" + name, p + ".part")
            os.replace(p + ".part", p)
        except Exception:  # noqa: BLE001 - some cases lack logs/traces
            if os.path.exists(p + ".part"):
                os.remove(p + ".part")
    return d


def _level(msg):
    return "ERROR" if _ERR.search(msg) else ("WARN" if _WARN.search(msg) else "INFO")


def service_of(series):
    return series.rsplit("_", 1)[0]


def convert(case_dir):
    """RCAEval case directory -> AgentLab data dict {spans, logs, metrics}."""
    import pandas as pd
    m = pd.read_parquet(os.path.join(case_dir, "metrics.parquet"))
    metrics = []
    cols = [c for c in m.columns if c != "time"]
    for row in m.itertuples(index=False):
        ts = int(row[0]) * 1000
        for c, v in zip(cols, row[1:]):
            if not pd.isna(v):
                metrics.append({"ts_ms": ts, "series": c, "value": round(float(v), 6)})
    logs = []
    lp = os.path.join(case_dir, "logs.parquet")
    if os.path.exists(lp):
        lg = pd.read_parquet(lp)
        for ts, comp, msg in zip(lg["timestamp"], lg["container_name"], lg["message"]):
            msg = "" if pd.isna(msg) else str(msg)
            logs.append({"ts_ms": int(ts) * 1000, "level": _level(msg), "component": str(comp),
                         "msg": msg, "trace_id": None, "attrs": {}})
        logs.sort(key=lambda r: r["ts_ms"])
    spans = []
    tp = os.path.join(case_dir, "traces.parquet")
    if os.path.exists(tp):
        t = pd.read_parquet(tp)
        for r in t.itertuples(index=False):
            parent = None if pd.isna(r.parentSpanID) or r.parentSpanID == "" else str(r.parentSpanID)
            code = None if pd.isna(r.statusCode) else int(r.statusCode)
            spans.append({"trace_id": r.traceID, "span_id": r.spanID, "parent_id": parent,
                          "name": str(r.operationName), "component": str(r.serviceName),
                          "start_ms": float(r.startTimeMillis), "dur_ms": float(r.duration) / 1000.0,
                          "status": "ERROR" if code not in (None, 0) else "OK",
                          "attrs": {"status_code": code}})
        spans.sort(key=lambda s: s["start_ms"])
    return {"spans": spans, "logs": logs, "metrics": metrics, "calls": len(spans)}


def services(data):
    return sorted({service_of(r["series"]) for r in data["metrics"]})


README = """# Telemetry for one window of a microservice application

All timestamps are Unix epoch milliseconds (UTC). The window is about 24 minutes.

- logs.jsonl   one JSON object per line: ts_ms, level, component, msg, trace_id, attrs
               (level is derived from the message text: ERROR/WARN keywords, else INFO)
- spans.jsonl  one span per line: trace_id, span_id, parent_id, name, component,
               start_ms, dur_ms, status (OK|ERROR), attrs (status_code)
- metrics.csv  ts_ms,series,value  (1 s steps; series = <service>_<metric>, e.g. cpu, mem,
               diskio, socket, workload, error, latency-50, latency-90)
"""


# ---- agent task definition (used by harness.run_one for ids "rcaeval:<case>") ----
OB_SERVICES = ["adservice", "cartservice", "checkoutservice", "currencyservice", "emailservice", "frontend",
               "frontend-external", "paymentservice", "productcatalogservice", "recommendationservice",
               "redis", "shippingservice"]

SYSTEM = """You are the on-call engineer for Online Boutique, a microservice web shop (services: \
{services}). You have about 24 minutes of its telemetry: per-service metrics at 1 s, logs and traces.

A fault was injected into exactly one service during this window. Find the root-cause service (not \
just the services showing symptoms) and the fault type. Use the tools to investigate, then call \
submit_answer exactly once with up to 5 services, most likely first. You have at most {turns} turns.

Fault types:
- cpu: CPU stress on the service's container
- mem: memory stress
- disk: disk I/O stress
- delay: added network latency
- loss: network packet loss
- socket: socket exhaustion"""

USER = "Investigate this telemetry window and submit your diagnosis."

SUBMIT = {
    "name": "submit_answer",
    "description": "Submit your final diagnosis. Call exactly once, when you are done investigating.",
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "services": {"type": "array", "items": {"type": "string", "enum": OB_SERVICES},
                         "description": "Up to 5 candidate root-cause services, most likely first."},
            "fault": {"type": "string", "enum": FAULTS, "description": "Fault type."},
            "evidence": {"type": "array", "items": {"type": "string"},
                         "description": "Short quotes or ids from the telemetry that support the diagnosis."},
            "summary": {"type": "string", "description": "One or two sentences."},
        },
        "required": ["services", "fault", "evidence", "summary"],
    },
    "strict": True,
}

TOOL_TEXT = {  # keyed by tool name; replaces the synthetic suite's wording
    "query": ("Run one read-only SQLite query over the telemetry and get CSV rows (truncated to {cap} chars). "
            "Tables: logs(ts_ms, level, component, msg, trace_id, attrs), spans(trace_id, span_id, parent_id, "
            "name, component, start_ms, dur_ms, status, attrs), metrics(ts_ms, series, value). Times are epoch ms. "
            "Metrics are 1 s steps; series = <service>_<metric> (cpu, mem, diskio, socket, workload, error, "
            "latency-50, latency-90). level is derived from message keywords."),
    "get_digest": ("Get the ranked digest of this telemetry window: metric change points, log templates whose "
                   "rate changed, rare events, totals."),
}


def _subset_by_case():
    return {c["case"]: c for c in load_subset()}


def inject_offset_s(case, data_root=None):
    """Seconds from the first metric sample to the injection (only for the matched-time variant)."""
    import pandas as pd
    root = data_root or os.environ.get("AGENTLAB_RCAEVAL_DATA") or os.path.join(HERE, "..", "..", "data-ext", "rcaeval")
    d = os.path.join(root, case)
    t0 = int(pd.read_parquet(os.path.join(d, "metrics.parquet"), columns=["time"])["time"].min())
    inj = int(open(os.path.join(d, "inject_time.txt")).read().strip())
    return inj - t0, inj


def task(incident_id):
    variant, case = incident_id.split(":", 1)
    c = _subset_by_case()[case]
    user = USER
    if variant == "rcaeval+t":  # matched condition: the agent gets the injection time, as the classical baselines do
        off, inj = inject_offset_s(case)
        user = ("The fault was injected at %d ms epoch (+%dm%02ds from the start of the telemetry). "
                % (inj * 1000, off // 60, off % 60)) + USER
    return {"truth": {"components": [c["service"]], "cause": c["fault"]}, "evidence": [], "variant": variant,
            "system": SYSTEM.replace("{services}", ", ".join(OB_SERVICES)), "user": user, "submit": SUBMIT,
            "tool_text": TOOL_TEXT, "grade": grade, "held_out": False,
            "mock_answer": {"services": ["frontend"], "fault": "cpu", "evidence": [], "summary": "mock"}}


def grade(t, answer, tool_outputs):
    if answer is None:
        return {"correct": False, "component_ok": False, "cause_ok": False, "retrieved": None,
                "submitted": False, "ac1": 0.0, "ac3": 0.0, "ac5": 0.0, "avg5": 0.0}
    truth = t["truth"]["components"][0]
    order = list(dict.fromkeys(answer.get("services") or []))[:5]
    acs = {k: 1.0 if truth in order[:k] else 0.0 for k in range(1, 6)}
    cause_ok = answer.get("fault") == t["truth"]["cause"]
    return {"correct": acs[1] == 1.0 and cause_ok, "component_ok": acs[1] == 1.0, "cause_ok": cause_ok,
            "retrieved": None, "submitted": True, "ac1": acs[1], "ac3": acs[3], "ac5": acs[5],
            "avg5": sum(acs.values()) / 5}

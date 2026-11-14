"""RCAEval (Pham et al., WWW'25; MIT) as an external AgentLab suite.

Source: https://huggingface.co/datasets/phamquiluan/RCAEval (Parquet copy of the
Zenodo release). Each case is a real fault injection into a microservice demo
app with metrics (1 s), logs and traces; the label is the root-cause service
and the fault type.

Suites (task id prefix -> dataset):
  rcaeval:<case>    rcaeval+t:<case>     RE2-OB, Online Boutique (12 services)
  rcaevaltt:<case>  rcaevaltt+t:<case>   RE2-TT, Train Ticket (68 services and databases)
"+t" is the matched condition (the agent is told the injection time); every variant
of a case reads the same prepared layout ("layout_id").

Rules (fixed before any scored run):
- Subset: one repetition per (service, fault) chosen with SUBSET_SEED -> 30 cases
  per dataset. Written to rcaeval_subset.json (OB) / rcaeval_tt_subset.json (TT)
  and committed before any scoring.
- The service enum is the app's public structure: the service prefixes of the metric
  columns present in every case of the dataset (not the root-cause labels).
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
TT_SUBSET_FILE = os.path.join(HERE, "rcaeval_tt_subset.json")
TT_PILOT_FILE = os.path.join(HERE, "rcaeval_tt_pilot.json")
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


def load_subset(suite="rcaeval"):
    with open(SUITES[suite]["subset"]) as f:
        return json.load(f)


def pick_pilot(subset, seed=SUBSET_SEED):
    """One case per fault, chosen by seed from the subset (for small live pilots)."""
    rng = random.Random(seed)
    out = []
    for f in FAULTS:
        g = sorted((c for c in subset if c["fault"] == f), key=lambda c: c["case"])
        out.append(g[rng.randrange(len(g))]["case"])
    return out


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

def _submit(services):
    return {
    "name": "submit_answer",
    "description": "Submit your final diagnosis. Call exactly once, when you are done investigating.",
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "services": {"type": "array", "items": {"type": "string", "enum": services},
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


SUBMIT = _submit(OB_SERVICES)

TOOL_TEXT = {  # keyed by tool name; replaces the synthetic suite's wording
    "query": ("Run one read-only SQLite query over the telemetry and get CSV rows (truncated to {cap} chars). "
            "Tables: logs(ts_ms, level, component, msg, trace_id, attrs), spans(trace_id, span_id, parent_id, "
            "name, component, start_ms, dur_ms, status, attrs), metrics(ts_ms, series, value). Times are epoch ms. "
            "Metrics are 1 s steps; series = <service>_<metric> (cpu, mem, diskio, socket, workload, error, "
            "latency-50, latency-90). level is derived from message keywords."),
    "get_digest": ("Get the ranked digest of this telemetry window: metric change points, log templates whose "
                   "rate changed, rare events, totals."),
}


# ---- RE2-TT: Train Ticket (ids "rcaevaltt:<case>", "rcaevaltt+t:<case>") ----
# Service prefixes of the metric columns present in all 90 RE2-TT cases (istio-init, an init
# container seen in 5 of 90, is left out). The app's structure, not the root-cause labels.
# Trace serviceName and log container_name use the same names.
TT_SERVICES = [
    "ts-admin-basic-info-service", "ts-admin-order-service", "ts-admin-route-service", "ts-admin-travel-service",
    "ts-admin-user-service", "ts-assurance-mongo", "ts-assurance-service", "ts-auth-mongo", "ts-auth-service",
    "ts-avatar-service", "ts-basic-service", "ts-cancel-service", "ts-config-mongo", "ts-config-service",
    "ts-consign-mongo", "ts-consign-price-mongo", "ts-consign-price-service", "ts-consign-service",
    "ts-contacts-mongo", "ts-contacts-service", "ts-execute-service", "ts-food-map-mongo", "ts-food-map-service",
    "ts-food-mongo", "ts-food-mysql", "ts-food-service", "ts-inside-payment-mongo", "ts-inside-payment-service",
    "ts-news-service", "ts-notification-mongo", "ts-notification-service", "ts-order-mongo",
    "ts-order-other-mongo", "ts-order-other-service", "ts-order-service", "ts-payment-mongo", "ts-payment-service",
    "ts-preserve-mongo", "ts-preserve-other-mongo", "ts-preserve-other-service", "ts-preserve-service",
    "ts-price-mongo", "ts-price-service", "ts-rebook-service", "ts-route-mongo", "ts-route-plan-service",
    "ts-route-service", "ts-seat-service", "ts-security-mongo", "ts-security-service", "ts-station-mongo",
    "ts-station-service", "ts-ticket-office-mongo", "ts-ticket-office-service", "ts-ticketinfo-service",
    "ts-train-mongo", "ts-train-service", "ts-travel-mongo", "ts-travel-plan-service", "ts-travel-service",
    "ts-travel2-mongo", "ts-travel2-service", "ts-ui-dashboard", "ts-user-mongo", "ts-user-service",
    "ts-verification-code-service", "ts-voucher-mysql", "ts-voucher-service"]

TT_SYSTEM = """You are the on-call engineer for Train Ticket, a railway ticket-booking application built from \
about 40 Java/Spring microservices (ts-*-service) behind a web UI and gateway (ts-ui-dashboard); most \
services keep their data in their own MongoDB (ts-*-mongo), two in MySQL (ts-*-mysql). Components: \
{services}. You have about 24 minutes of its telemetry: per-component metrics at 1 s, logs and traces.

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

TT_README = """# Telemetry for one window of a microservice application

All timestamps are Unix epoch milliseconds (UTC). The window is about 24 minutes.

- logs.jsonl   one JSON object per line: ts_ms, level, component, msg, trace_id, attrs
               (level is derived from the message text: ERROR/WARN keywords, else INFO;
               the file is empty when no logs were collected for the window)
- spans.jsonl  one span per line: trace_id, span_id, parent_id, name, component,
               start_ms, dur_ms, status (OK|ERROR), attrs (status_code)
- metrics.csv  ts_ms,series,value  (1 s steps; series = <component>_<metric>, e.g. cpu, mem,
               diskio, socket, workload, error, latency-50, latency-90)
"""

TT_TOOL_TEXT = {
    "query": ("Run one read-only SQLite query over the telemetry and get CSV rows (truncated to {cap} chars). "
              "Tables: logs(ts_ms, level, component, msg, trace_id, attrs), spans(trace_id, span_id, parent_id, "
              "name, component, start_ms, dur_ms, status, attrs), metrics(ts_ms, series, value). Times are epoch "
              "ms. Metrics are 1 s steps; series = <component>_<metric> (cpu, mem, diskio, socket, workload, "
              "error, latency-50, latency-90). level is derived from message keywords. logs can be empty. spans "
              "has up to ~1.5M rows: filter and aggregate in SQL."),
    "get_digest": TOOL_TEXT["get_digest"],
}

_DATA = os.path.join(HERE, "..", "..", "data-ext")
SUITES = {
    "rcaeval": {"dataset": "RE2-OB", "rc_dataset": "re2-ob", "subset": SUBSET_FILE, "services": OB_SERVICES,
                "system": SYSTEM, "submit": SUBMIT, "tool_text": TOOL_TEXT, "readme": README,
                "data_env": "AGENTLAB_RCAEVAL_DATA", "data_default": os.path.join(_DATA, "rcaeval"),
                "mock_service": "frontend"},
    "rcaevaltt": {"dataset": "RE2-TT", "rc_dataset": "re2-tt", "subset": TT_SUBSET_FILE, "services": TT_SERVICES,
                  "system": TT_SYSTEM, "submit": _submit(TT_SERVICES), "tool_text": TT_TOOL_TEXT,
                  "readme": TT_README, "data_env": "AGENTLAB_RCAEVAL_TT_DATA",
                  "data_default": os.path.join(_DATA, "rcaeval-tt-full"), "mock_service": "ts-ui-dashboard"},
}
ID_PREFIXES = tuple(s + v + ":" for s in SUITES for v in ("", "+t"))  # "rcaeval:", "rcaeval+t:", "rcaevaltt:", ...


def suite_of(incident_id):
    """'rcaevaltt+t:<case>' -> ('rcaevaltt+t', 'rcaevaltt', '<case>')."""
    variant, case = incident_id.split(":", 1)
    suite = variant[:-2] if variant.endswith("+t") else variant
    if suite not in SUITES:
        raise KeyError("unknown RCAEval suite %r" % variant)
    return variant, suite, case


def ids(arg):
    """CLI keywords: 'rcaeval', 'rcaeval+t', 'rcaevaltt', 'rcaevaltt+t', 'rcaevaltt-pilot'; else None."""
    if arg == "rcaevaltt-pilot":
        with open(TT_PILOT_FILE) as f:
            return ["rcaevaltt:" + c for c in json.load(f)]
    if arg in ("rcaeval", "rcaeval+t", "rcaevaltt", "rcaevaltt+t"):
        return [arg + ":" + c["case"] for c in load_subset(suite_of(arg + ":x")[1])]
    return None


def _subset_by_case(suite="rcaeval"):
    return {c["case"]: c for c in load_subset(suite)}


def inject_offset_s(case, data_root=None, suite="rcaeval"):
    """Seconds from the first metric sample to the injection (only for the matched-time variant)."""
    import pandas as pd
    S = SUITES[suite]
    root = data_root or os.environ.get(S["data_env"]) or S["data_default"]
    d = os.path.join(root, case)
    t0 = int(pd.read_parquet(os.path.join(d, "metrics.parquet"), columns=["time"])["time"].min())
    inj = int(open(os.path.join(d, "inject_time.txt")).read().strip())
    return inj - t0, inj


def task(incident_id):
    variant, suite, case = suite_of(incident_id)
    S = SUITES[suite]
    c = _subset_by_case(suite)[case]
    user = USER
    if variant.endswith("+t"):  # matched condition: the agent gets the injection time, as the classical baselines do
        off, inj = inject_offset_s(case, suite=suite)
        user = ("The fault was injected at %d ms epoch (+%dm%02ds from the start of the telemetry). "
                % (inj * 1000, off // 60, off % 60)) + USER
    return {"truth": {"components": [c["service"]], "cause": c["fault"]}, "evidence": [], "variant": variant,
            "layout_id": suite + ":" + case,  # every variant reads the same prepared telemetry
            "system": S["system"].replace("{services}", ", ".join(S["services"])), "user": user,
            "submit": S["submit"], "tool_text": S["tool_text"], "grade": grade, "held_out": False,
            "mock_answer": {"services": [S["mock_service"]], "fault": "cpu", "evidence": [], "summary": "mock"}}


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

"""Interface arms: the tools each arm gives the agent, and where they execute.

Every arm gets the same output cap (OUTPUT_CAP chars per tool result) and the
same submit_answer tool. Arms 2-4 run on a backend:
  LocalBackend  - a directory on this machine (shell only with allow_shell=True,
                  which the harness permits for the scripted mock model only)
  ArchilBackend - each tool call is one Archil exec (a fresh microVM) over the disk
The grafana arms (0 visual, 1 mcp) need the docker stack and are not built yet.
"""
import base64
import json
import os
import shlex
import sqlite3
import subprocess
import time

from .incidents import CAUSES, COMPONENTS
from .prepare import DEFAULT_SALT, opaque

OUTPUT_CAP = 8000
ARMS = ["files", "sql", "digest"]          # implemented
PLANNED_ARMS = ["visual", "mcp"]           # need docker (Prometheus, Loki, Grafana)

SUBMIT = {
    "name": "submit_answer",
    "description": "Submit your final diagnosis. Call exactly once, when you are done investigating.",
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "component": {"type": "string", "enum": COMPONENTS,
                          "description": "Component where the root cause lives; 'none' if there is no incident."},
            "cause": {"type": "string", "enum": CAUSES, "description": "Root-cause category."},
            "evidence": {"type": "array", "items": {"type": "string"},
                         "description": "Short quotes or ids from the telemetry that support the diagnosis."},
            "summary": {"type": "string", "description": "One or two sentences."},
        },
        "required": ["component", "cause", "evidence", "summary"],
    },
    "strict": True,
}

ARM_TOOLS = {
    "files": [{
        "name": "shell",
        "description": ("Run a shell command in the telemetry directory and get stdout+stderr "
                        "(truncated to %d chars). Files: README.md (schema), logs.jsonl, spans.jsonl, "
                        "metrics.csv. Tools available: grep, awk, sed, sort, uniq, head, tail, wc, jq, python3 "
                        "(stdlib only)." % OUTPUT_CAP),
        "input_schema": {"type": "object", "additionalProperties": False,
                         "properties": {"command": {"type": "string"}}, "required": ["command"]},
        "strict": True}],
    "sql": [{
        "name": "query",
        "description": ("Run one read-only SQLite query over the telemetry and get CSV rows "
                        "(truncated to %d chars). Tables: logs(ts_ms, level, component, msg, trace_id, attrs), "
                        "spans(trace_id, span_id, parent_id, name, component, start_ms, dur_ms, status, attrs), "
                        "metrics(ts_ms, series, value). ts_ms/start_ms are epoch ms; attrs is JSON text "
                        "(use json_extract). Metrics are 10 s steps; series = <component>.<span>.<stat> or a gauge."
                        % OUTPUT_CAP),
        "input_schema": {"type": "object", "additionalProperties": False,
                         "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
        "strict": True}],
    "digest": [
        {"name": "get_digest", "description": "Get the ranked digest of the last 60 minutes: metric change points, "
                                              "log templates whose rate changed, rare events, totals.",
         "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
         "strict": True},
        {"name": "drill", "description": "Get the raw evidence behind one digest line, by its id (e.g. M1, L2, E1).",
         "input_schema": {"type": "object", "additionalProperties": False,
                          "properties": {"id": {"type": "string"}}, "required": ["id"]},
         "strict": True}],
}

SYSTEM = """You are the on-call engineer for an agent platform. Every agent tool call runs as a \
fresh microVM over a shared network disk; components are gateway, scheduler, exec-pool, fs, store, \
worker and commit. You have the last 60 minutes of telemetry.

Decide whether there is an incident. If there is, find the root-cause component and the cause \
category (not just the symptom). If nothing is wrong, answer component "none", cause "no_fault". \
Use the tools to investigate, then call submit_answer exactly once. You have at most {turns} turns.

Cause categories:
- config_change: a configuration, policy or deployment change altered behaviour
- resource_exhausted: a pool, quota, memory or disk space ran out
- readonly_or_permission: writes or calls rejected as read-only, unauthorised or not permitted
- stale_visibility: data that exists is not visible yet (caching, propagation delay)
- concurrency_conflict: simultaneous operations collide (locks, create races)
- storage_latency: storage or durable writes became slow without errors
- contention: more parallel work slowed a shared resource
- dependency_failure: a downstream service or device returned errors
- traffic_surge: more incoming load; the system is otherwise healthy
- no_fault: nothing is wrong"""


def cap(text):
    if len(text) <= OUTPUT_CAP:
        return text
    return text[:OUTPUT_CAP] + "\n[truncated: %d more chars]" % (len(text) - OUTPUT_CAP)


def _csv(rows, cols):
    out = [",".join(cols)]
    for r in rows:
        out.append(",".join("" if v is None else str(v).replace("\n", " ") for v in r))
    return "\n".join(out)


class LocalBackend:
    name = "local"

    def __init__(self, root, allow_shell=False, salt=DEFAULT_SALT):
        self.root, self.allow_shell, self.salt = root, allow_shell, salt

    def call(self, incident_id, arm, tool, args):
        oid = opaque(incident_id, self.salt)
        d = lambda a: os.path.join(self.root, a, oid)
        t0 = time.perf_counter()
        if arm == "files" and tool == "shell":
            if not self.allow_shell:
                out = "error: local shell is disabled for live models; use the Archil backend"
            else:
                p = subprocess.run(["/bin/sh", "-c", args["command"]], cwd=d("files"),
                                   capture_output=True, text=True, timeout=30)
                out = p.stdout + p.stderr
        elif arm == "sql" and tool == "query":
            db = sqlite3.connect("file:%s?mode=ro" % os.path.join(d("sql"), "telemetry.db"), uri=True)
            try:
                cur = db.execute(args["sql"])
                out = _csv(cur.fetchmany(2000), [d[0] for d in cur.description or []])
            except sqlite3.Error as e:
                out = "sqlite error: %s" % e
            finally:
                db.close()
        elif arm == "digest" and tool == "get_digest":
            with open(os.path.join(d("digest"), "digest.txt")) as f:
                out = f.read()
        elif arm == "digest" and tool == "drill":
            with open(os.path.join(d("digest"), "index.json")) as f:
                index = json.load(f)
            out = index.get(args["id"].strip().upper(), "unknown id %r; valid ids: %s" % (args["id"], ", ".join(index)))
        else:
            out = "error: unknown tool %s for arm %s" % (tool, arm)
        return cap(out), {"tool_ms": (time.perf_counter() - t0) * 1000}


_SQL_GUEST = r"""
import base64,sqlite3,sys
db=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
try:
    c=db.execute(base64.b64decode(sys.argv[2]).decode())
    rows=c.fetchmany(2000); cols=[d[0] for d in c.description or []]
    print(','.join(cols))
    for r in rows: print(','.join('' if v is None else str(v).replace('\n',' ') for v in r))
except sqlite3.Error as e: print('sqlite error: %s'%e)
"""

_DRILL_GUEST = r"""
import json,sys
ix=json.load(open(sys.argv[1])); k=sys.argv[2].strip().upper()
print(ix.get(k,'unknown id %r; valid ids: %s'%(sys.argv[2],', '.join(ix))))
"""


class ArchilBackend:
    """Each tool call = one Archil exec. root is the disk-relative directory of the prepared layout."""
    name = "archil"

    def __init__(self, client, root, salt=DEFAULT_SALT):
        from .archil import MOUNT
        self.client, self.root, self.salt = client, "%s/%s" % (MOUNT, root.strip("/")), salt

    def call(self, incident_id, arm, tool, args):
        oid = opaque(incident_id, self.salt)
        d = lambda a: "%s/%s/%s" % (self.root, a, oid)
        if arm == "files" and tool == "shell":
            cmd = "cd %s && %s" % (shlex.quote(d("files")), args["command"])
        elif arm == "sql" and tool == "query":
            cmd = "python3 -S -c %s %s %s" % (shlex.quote(_SQL_GUEST), shlex.quote(d("sql") + "/telemetry.db"),
                                              shlex.quote(base64.b64encode(args["sql"].encode()).decode()))
        elif arm == "digest" and tool == "get_digest":
            cmd = "cat %s" % shlex.quote(d("digest") + "/digest.txt")
        elif arm == "digest" and tool == "drill":
            cmd = "python3 -S -c %s %s %s" % (shlex.quote(_DRILL_GUEST), shlex.quote(d("digest") + "/index.json"),
                                              shlex.quote(args["id"]))
        else:
            return "error: unknown tool %s for arm %s" % (tool, arm), {"tool_ms": 0}
        r = self.client.exec(cmd)
        return cap(r["stdout"] + r["stderr"]), {"tool_ms": r["wall_ms"], "queue_ms": r["queue_ms"],
                                                "execute_ms": r["execute_ms"], "exit_code": r["exit_code"]}

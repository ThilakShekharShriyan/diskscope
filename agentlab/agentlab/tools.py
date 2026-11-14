"""Interface arms: the tools each arm gives the agent, and where they execute.

Every arm gets the same output cap (OUTPUT_CAP chars per tool result) and the
same submit_answer tool. Arms 2-4 run on a backend:
  LocalBackend  - a directory on this machine (shell only with allow_shell=True,
                  which the harness permits for the scripted mock model only)
  ArchilBackend - each tool call is one Archil exec (a fresh microVM) over the disk
The grafana arms (0 visual, 1 mcp) need the docker stack and are not built yet.
"""
import json
import os
import re
import shlex
import sqlite3
import subprocess
import time

from .incidents import CAUSES, COMPONENTS
from .prepare import DEFAULT_SALT, opaque
from . import rcatools as _rcatools  # stdlib only at import

OUTPUT_CAP = 8000
ARMS = ["files", "sql", "digest", "prior", "rcatools"]  # implemented; prior = no telemetry tools (control)
# rcatools = RCAEval's own agent tools, run by RCAEval's CaseToolkit (rcatools.py); local backend only
PLANNED_ARMS = ["visual", "mcp"]           # need docker (Prometheus, Loki, Grafana)
# Controls: the digest arm's tools over a digest with one inserted line (agentlab/digest_decoy.py);
# their layouts exist only where rcaeval_controls prepared them
CONTROL_ARMS = ["digest-decoy", "digest-placebo"]
DIGEST_ARMS = ("digest",) + tuple(CONTROL_ARMS)

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
    "prior": [],
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
    "rcatools": _rcatools.ARM_TOOLS,
}
for _a in CONTROL_ARMS:
    ARM_TOOLS[_a] = ARM_TOOLS["digest"]

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


REFUSE_WANDER = ("error: commands must stay in the telemetry directory "
                 "(no '..', absolute paths or cd; use relative file names)")


def leaves_dir(cmd):
    """Files-arm commands that could reach outside the arm's own directory (other arms' data)."""
    return ".." in cmd or "/mnt" in cmd or "cd /" in cmd or " /" in cmd or cmd.lstrip().startswith("/") or "~" in cmd


def cap(text, clipped=False):
    """Every tool result goes through this. clipped: the source already stopped early (more data exists)."""
    if len(text) <= OUTPUT_CAP:
        return text + ("\n[truncated: more rows not shown]" if clipped else "")
    return text[:OUTPUT_CAP] + "\n[truncated: %d%s more chars]" % (len(text) - OUTPUT_CAP, "+" if clipped else "")


_SQL_MAX_BYTES = 4 * OUTPUT_CAP   # rows read / bytes transferred before cap() applies
_SQL_TIMEOUT_S = 30               # same as the local shell timeout
_SQL_DENY = re.compile(r"\b(fsdir|readfile|writefile|edit|load_extension|zipfile|sqlar_\w+)\s*\(|^\s*\.", re.I)
SQL_REFUSED = "error: only plain read-only SELECT queries over the telemetry are allowed"


def _sql_csv(cur):
    """CSV like `sqlite3 -csv -header` (minimal quoting, NULL empty); stops after _SQL_MAX_BYTES."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([d[0] for d in cur.description or []])
    for row in cur:
        w.writerow(["" if v is None else v for v in row])
        if buf.tell() > _SQL_MAX_BYTES:
            return buf.getvalue()[:_SQL_MAX_BYTES], True
    return buf.getvalue(), False


def _authorize(action, *args):
    """SQLite authorizer: reads only; no ATTACH (other files), no PRAGMA (paths, settings)."""
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class LocalBackend:
    name = "local"

    def __init__(self, root, allow_shell=False, salt=DEFAULT_SALT):
        self.root, self.allow_shell, self.salt = root, allow_shell, salt
        self._rcatools = None

    def call(self, incident_id, arm, tool, args):
        if arm == "rcatools":  # RCAEval's CaseToolkit over <root>/rcatools/<opaque>/
            if self._rcatools is None:
                self._rcatools = _rcatools.RcaToolsBackend(self.root, self.salt)
            return self._rcatools.call(incident_id, arm, tool, args)
        d = lambda a: os.path.join(self.root, a, opaque(incident_id, self.salt, a))
        if arm in ("files", "sql", "digest") and not os.path.isdir(d(arm)):
            from .harness import InfraError  # prepared data missing: the benchmark's fault, not the agent's
            raise InfraError("layout missing for this task and arm")
        t0 = time.perf_counter()
        clipped = False
        if arm == "files" and tool == "shell":
            if leaves_dir(args["command"]):
                return REFUSE_WANDER, {"tool_ms": 0, "refused": True}
            if not self.allow_shell:
                out = "error: local shell is disabled for live models; use the Archil backend"
            else:
                p = subprocess.run(["/bin/sh", "-c", args["command"]], cwd=d("files"),
                                   capture_output=True, text=True, timeout=30)
                out = p.stdout + p.stderr
        elif arm == "sql" and tool == "query":
            if _SQL_DENY.search(args["sql"]):
                return SQL_REFUSED, {"tool_ms": 0, "refused": True}
            db = sqlite3.connect("file:%s?mode=ro" % os.path.join(d("sql"), "telemetry.db"), uri=True)
            db.set_authorizer(_authorize)
            deadline = time.monotonic() + _SQL_TIMEOUT_S
            db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
            try:
                out, clipped = _sql_csv(db.execute(args["sql"]))
            except sqlite3.Error as e:
                out = "sqlite error: %s" % ("query took longer than %d s" % _SQL_TIMEOUT_S
                                            if "interrupted" in str(e) else e)
            finally:
                db.close()
        elif arm in DIGEST_ARMS and tool == "get_digest":
            with open(os.path.join(d(arm), "digest.txt")) as f:
                out = f.read()
        elif arm in DIGEST_ARMS and tool == "drill":
            with open(os.path.join(d(arm), "index.json")) as f:
                index = json.load(f)
            out = index.get(args["id"].strip().upper(), "unknown id %r; valid ids: %s" % (args["id"], ", ".join(index)))
        else:
            out = "error: unknown tool %s for arm %s" % (tool, arm)
        return cap(out, clipped), {"tool_ms": (time.perf_counter() - t0) * 1000}


_ID_RX = re.compile(r"^[MLE][0-9]{1,2}$")


class ArchilBackend:
    """Each tool call = one Archil exec. root is the disk-relative directory of the prepared layout."""
    name = "archil"

    def __init__(self, client, root, salt=DEFAULT_SALT):
        from .archil import MOUNT
        self.client, self.root, self.salt = client, "%s/%s" % (MOUNT, root.strip("/")), salt

    def call(self, incident_id, arm, tool, args):
        d = lambda a: "%s/%s/%s" % (self.root, a, opaque(incident_id, self.salt, a))
        if arm == "files" and tool == "shell":
            if leaves_dir(args["command"]):
                return REFUSE_WANDER, {"tool_ms": 0, "refused": True}
            cmd = "cd %s && %s" % (shlex.quote(d("files")), args["command"])
        elif arm == "sql" and tool == "query":
            if _SQL_DENY.search(args["sql"]):
                return SQL_REFUSED, {"tool_ms": 0, "refused": True}
            # sqlite3 CLI: no interpreter start-up; -readonly -safe (no file/shell functions);
            # output trimmed before transfer, same CSV dialect and byte limit as LocalBackend
            cmd = "timeout %d sqlite3 -readonly -safe -csv -header %s %s 2>&1 | head -c %d" % (
                _SQL_TIMEOUT_S, shlex.quote(d("sql") + "/telemetry.db"), shlex.quote(args["sql"]), _SQL_MAX_BYTES)
        elif arm in DIGEST_ARMS and tool == "get_digest":
            cmd = "cat %s" % shlex.quote(d(arm) + "/digest.txt")
        elif arm in DIGEST_ARMS and tool == "drill":
            k = args["id"].strip().upper()
            if not _ID_RX.match(k):
                return "unknown id %r; ids look like M1, L2, E1" % args["id"], {"tool_ms": 0}
            cmd = "cat %s 2>/dev/null || echo %s" % (shlex.quote("%s/drill/%s.txt" % (d(arm), k)),
                                                     shlex.quote("unknown id %r" % k))
        else:
            return "error: unknown tool %s for arm %s" % (tool, arm), {"tool_ms": 0}
        from .archil import ArchilError
        try:
            r = self.client.exec(cmd, retry=True)  # every arm tool is read-only
        except ArchilError as e:
            from .harness import InfraError
            raise InfraError(str(e)) from None
        out = r["stdout"] + r["stderr"]
        clipped = arm == "sql" and len(out.encode()) >= _SQL_MAX_BYTES
        return cap(out, clipped), {"tool_ms": r["wall_ms"], "queue_ms": r["queue_ms"],
                                                "execute_ms": r["execute_ms"], "exit_code": r["exit_code"]}

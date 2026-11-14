"""Incident definitions. Written AFTER the digest freeze (commit 27b346c).

Each incident = generic effects for generate.generate() + ground truth.
truth.components: acceptable root-cause components (first is canonical).
truth.cause: one of CAUSES. evidence: strings that appear only in the fault's
evidence; a run "retrieved" the evidence if any appears in its tool outputs.
Markers are log/event text only: series names are visible to every arm just by
listing series, so incidents whose evidence is only a metric shift have
evidence [] and their retrieval is not graded (reported as None).
Fault families come from DiskScope/BranchLab findings; HELD_OUT are new kinds.
"""

CAUSES = ["config_change", "resource_exhausted", "readonly_or_permission", "stale_visibility",
          "concurrency_conflict", "storage_latency", "contention", "dependency_failure",
          "traffic_surge", "no_fault"]
COMPONENTS = ["gateway", "scheduler", "exec-pool", "fs", "store", "worker", "commit", "none"]

SLIM = "python:3.13-slim"


def ev(at, comp, msg, level="INFO", **attrs):
    return {"kind": "event", "at_s": at, "component": comp, "msg": msg, "level": level, "attrs": attrs}


DECOY_DEPLOY = lambda at: ev(at, "worker", "deploy worker build=2.3.1 rollout=complete")
DECOY_DASH = lambda at: ev(at, "gateway", "dashboard config reloaded panels=42")


def _image(onset):
    return [ev(onset - 5, "scheduler", "config applied exec_image=%s previous=archil/default" % SLIM),
            {"kind": "attr", "set": {"image": SLIM}, "start_s": onset},
            {"kind": "profile", "profile": "slim", "component": "exec-pool", "fields": ["queue"], "start_s": onset}]


def _erofs(onset, p=0.6):
    return [{"kind": "error", "component": "fs", "op": "write", "p": p, "code": "EROFS", "start_s": onset,
             "msg": "rename {id}.tmp -> {id}: [Errno 30] Read-only file system"}]


INCIDENTS = [
    {"id": "image-queue-a", "seed": 101, "effects": _image(1860),
     "truth": {"components": ["scheduler", "exec-pool"], "cause": "config_change"}, "evidence": [SLIM, "exec_image"]},
    {"id": "image-queue-b", "seed": 102, "effects": _image(2700) + [DECOY_DEPLOY(1500)],
     "truth": {"components": ["scheduler", "exec-pool"], "cause": "config_change"}, "evidence": [SLIM, "exec_image"]},
    {"id": "erofs-a", "seed": 103, "effects": _erofs(2100),
     "truth": {"components": ["fs"], "cause": "readonly_or_permission"}, "evidence": ["Read-only file system", "EROFS"]},
    {"id": "erofs-b", "seed": 104, "effects": _erofs(2900, p=0.3) + [DECOY_DASH(1200)],
     "truth": {"components": ["fs"], "cause": "readonly_or_permission"}, "evidence": ["Read-only file system", "EROFS"]},
    {"id": "sqlite-shared-dir", "seed": 105, "effects": [
        {"kind": "error", "component": "store", "op": "txn_commit", "p": 0.85, "code": "SQLITE_IOERR", "start_s": 2400,
         "msg": "sqlite3.OperationalError: disk I/O error db=jobs-{id}.db journal=delete"},
        {"kind": "attr", "task": "sqlite-commit", "set": {"mount": "shared", "dir_owner": "other-exec"}, "start_s": 2400}],
     "truth": {"components": ["store"], "cause": "readonly_or_permission"}, "evidence": ["SQLITE_IOERR", "disk I/O error"]},
    {"id": "negative-lookup", "seed": 106, "effects": [
        {"kind": "error", "component": "fs", "op": "read", "p": 0.7, "code": "ENOENT", "start_s": s, "end_s": s + 30,
         "msg": "open results/{id}.json: [Errno 2] No such file or directory"} for s in range(2200, 3500, 180)],
     "truth": {"components": ["fs"], "cause": "stale_visibility"}, "evidence": ["No such file or directory", "ENOENT"]},
    {"id": "eexist-race", "seed": 107, "effects": [
        {"kind": "attr", "task": "small-files", "set": {"concurrency": 4}, "start_s": 2000},
        {"kind": "error", "component": "fs", "op": "create_many", "p": 0.65, "code": "EEXIST", "start_s": 2000,
         "msg": "open shared/{id}/marker: [Errno 17] File exists"}],
     "truth": {"components": ["fs"], "cause": "concurrency_conflict"}, "evidence": ["File exists", "EEXIST"]},
    {"id": "sqlite-busy", "seed": 108, "effects": [
        {"kind": "attr", "task": "sqlite-commit", "set": {"concurrency": 4}, "start_s": 2500},
        {"kind": "error", "component": "store", "op": "txn_commit", "p": 0.5, "code": "SQLITE_BUSY", "start_s": 2500,
         "msg": "sqlite3.OperationalError: database is locked db=jobs-{id}.db"}],
     "truth": {"components": ["store"], "cause": "concurrency_conflict"}, "evidence": ["database is locked", "SQLITE_BUSY"]},
    {"id": "sqlite-slow", "seed": 109, "effects": [
        {"kind": "scale", "component": "store", "op": "txn_commit", "factor": 3.5, "start_s": 2300}],
     "truth": {"components": ["store"], "cause": "storage_latency"}, "evidence": []},
    {"id": "sqlite-slow-decoy", "seed": 110, "effects": [
        DECOY_DEPLOY(1300), {"kind": "scale", "component": "store", "op": "txn_commit", "factor": 3.0, "start_s": 2280}],
     "truth": {"components": ["store"], "cause": "storage_latency"}, "evidence": []},
    {"id": "commit-slow", "seed": 111, "effects": [
        {"kind": "scale", "component": "commit", "factor": 2.5, "add_ms": 120, "start_s": 2600}],
     "truth": {"components": ["commit"], "cause": "storage_latency"}, "evidence": []},
    {"id": "fanout-contention", "seed": 112, "effects": [
        {"kind": "attr", "set": {"concurrency": 4}, "start_s": 2150},
        ev(2140, "scheduler", "dispatch policy updated max_parallel=4 previous=1"),
        {"kind": "scale", "component": "exec-pool", "op": "acquire", "factor": 1.8, "start_s": 2150},
        {"kind": "scale", "component": "fs", "op": "stat", "factor": 1.5, "start_s": 2150}],
     "truth": {"components": ["scheduler", "exec-pool"], "cause": "contention"}, "evidence": ["max_parallel"]},
    {"id": "pool-exhausted", "seed": 113, "effects": [
        {"kind": "gauge", "series": "exec-pool.ready_vms", "factor": 0, "add": 1, "start_s": 2450},
        {"kind": "scale", "component": "exec-pool", "op": "acquire", "factor": 9, "start_s": 2450}],
     "truth": {"components": ["exec-pool"], "cause": "resource_exhausted"}, "evidence": []},
    {"id": "worker-dependency", "seed": 114, "effects": [
        {"kind": "error", "component": "worker", "op": "compute", "p": 0.6, "code": "UPSTREAM_503", "start_s": 2350,
         "msg": "fetch model weights from artifact-store: HTTP 503 Service Unavailable req={id}"}],
     "truth": {"components": ["worker"], "cause": "dependency_failure"}, "evidence": ["HTTP 503", "UPSTREAM_503"]},
    {"id": "traffic-surge", "seed": 115, "effects": [
        {"kind": "traffic", "factor": 2.6, "start_s": 2200},
        {"kind": "scale", "component": "exec-pool", "op": "acquire", "factor": 1.15, "start_s": 2200}],
     "truth": {"components": ["gateway"], "cause": "traffic_surge"}, "evidence": []},
    {"id": "quiet-decoys", "seed": 116, "effects": [DECOY_DEPLOY(1700), DECOY_DASH(2500)],
     "truth": {"components": ["none"], "cause": "no_fault"}, "evidence": []},
    {"id": "quiet-plain", "seed": 117, "effects": [],
     "truth": {"components": ["none"], "cause": "no_fault"}, "evidence": []},
    {"id": "scheduler-throttle", "seed": 118, "effects": [
        ev(2395, "scheduler", "rate limiter enabled max_inflight=2"),
        {"kind": "scale", "component": "scheduler", "factor": 60, "start_s": 2400}],
     "truth": {"components": ["scheduler"], "cause": "config_change"}, "evidence": ["max_inflight"]},
    {"id": "fs-metadata-slow", "seed": 119, "effects": [
        {"kind": "scale", "component": "fs", "op": "stat", "factor": 1, "add_ms": 25, "start_s": 2050},
        {"kind": "scale", "component": "fs", "op": "create_many", "factor": 2.5, "start_s": 2050}],
     "truth": {"components": ["fs"], "cause": "storage_latency"}, "evidence": []},
    {"id": "read-cache-off", "seed": 120, "effects": [
        ev(2595, "fs", "mount option applied page_cache_mb=0 previous=512"),
        {"kind": "scale", "component": "fs", "op": "read", "factor": 4, "start_s": 2600}],
     "truth": {"components": ["fs"], "cause": "config_change"}, "evidence": ["page_cache_mb"]},
]

HELD_OUT = [
    {"id": "ho-disk-full", "seed": 201, "effects": [
        {"kind": "error", "component": "fs", "op": "write", "p": 0.5, "code": "ENOSPC", "start_s": 2500,
         "msg": "write {id}.part: [Errno 28] No space left on device"},
        {"kind": "error", "component": "fs", "op": "fsync", "p": 0.5, "code": "ENOSPC", "start_s": 2500,
         "msg": "fsync {id}.log: [Errno 28] No space left on device"}],
     "truth": {"components": ["fs"], "cause": "resource_exhausted"}, "evidence": ["No space left", "ENOSPC"]},
    {"id": "ho-token-expiry", "seed": 202, "effects": [
        {"kind": "error", "component": "worker", "op": "run", "p": 0.9, "code": "HTTP_401", "start_s": 2700,
         "msg": "callback to orchestrator rejected: 401 Unauthorized (token expired) req={id}"},
        {"kind": "error", "component": "worker", "op": "compute", "p": 0.9, "code": "HTTP_401", "start_s": 2700,
         "msg": "callback to orchestrator rejected: 401 Unauthorized (token expired) req={id}"}],
     "truth": {"components": ["worker"], "cause": "readonly_or_permission"}, "evidence": ["401 Unauthorized", "HTTP_401"]},
    {"id": "ho-gradual-leak", "seed": 203, "effects": [
        {"kind": "scale", "component": "worker", "op": op, "factor": f, "start_s": s, "end_s": e}
        for op in ("compute", "run") for (f, s, e) in ((1.4, 1200, 2100), (2.2, 2100, 2900), (3.5, 2900, 3600))] + [
        {"kind": "gauge", "series": "worker.cpu_pct", "add": a, "start_s": s, "end_s": e}
        for (a, s, e) in ((15, 1200, 2100), (35, 2100, 2900), (55, 2900, 3600))],
     "truth": {"components": ["worker"], "cause": "resource_exhausted"}, "evidence": []},
    {"id": "ho-intermittent-eio", "seed": 204, "effects": [
        {"kind": "error", "component": "fs", "op": "stat", "p": 0.4, "code": "EIO", "start_s": s, "end_s": s + 20,
         "msg": "stat data/{id}: [Errno 5] Input/output error"} for s in (1900, 2450, 2980, 3400)],
     "truth": {"components": ["fs"], "cause": "dependency_failure"}, "evidence": ["Input/output error", "EIO"]},
]

ALL = {i["id"]: i for i in INCIDENTS + HELD_OUT}

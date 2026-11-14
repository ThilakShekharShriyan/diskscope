"""Generic telemetry generator for AgentLab.

Simulates one hour of an agent platform where every tool call runs as a
microVM over a shared disk. Per-call timings are resampled from real DiskScope
observations (baselines.json). An incident is a list of generic *effects*
(see EFFECT_KINDS) applied over a time window; this module knows nothing
about specific faults.

Stdlib only, so the same code can run inside an Archil exec.
"""
import json
import os
import random

STEP_S = 10
WINDOW_S = 3600
T0_MS = 1_793_000_000_000  # fixed epoch so runs are reproducible

COMPONENTS = ["gateway", "scheduler", "exec-pool", "fs", "store", "worker", "commit"]
TASK_MIX = [("stat", 22), ("noop", 10), ("small-files", 14), ("fsync-4k", 14),
            ("seq-write", 10), ("seq-read", 14), ("sqlite-commit", 8), ("hash-64m", 8)]
TASK_COMPONENT = {"stat": "fs", "noop": "worker", "small-files": "fs", "fsync-4k": "fs",
                  "seq-write": "fs", "seq-read": "fs", "sqlite-commit": "store", "hash-64m": "worker"}
TASK_OP = {"stat": "stat", "noop": "run", "small-files": "create_many", "fsync-4k": "fsync",
           "seq-write": "write", "seq-read": "read", "sqlite-commit": "txn_commit", "hash-64m": "compute"}
WRITING = {"small-files", "fsync-4k", "seq-write", "sqlite-commit"}
EFFECT_KINDS = {"profile", "scale", "error", "event", "traffic", "attr", "gauge"}


def load_baselines(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines.json")
    with open(path) as f:
        return json.load(f)


def _in(e, t_s):
    return e.get("start_s", 0) <= t_s < e.get("end_s", WINDOW_S)


def _match(e, comp, op=None):
    if e.get("component") not in (None, comp):
        return False
    return e.get("op") in (None, op)


def _pct(xs, q):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _hex(rng, n):
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def generate(effects, seed=0, rate_per_s=1.0, baselines=None):
    """Return dict with spans, logs, metrics lists. Effects are generic dicts."""
    for e in effects:
        if e["kind"] not in EFFECT_KINDS:
            raise ValueError("unknown effect kind %r" % e["kind"])
    bl = baselines or load_baselines()
    rng = random.Random(seed)
    tasks, weights = zip(*TASK_MIX)
    spans, logs = [], []
    noop_boot = sorted(s["execute"] for s in bl["default"]["noop"])

    def log(ts_ms, level, comp, msg, trace_id=None, **attrs):
        logs.append({"ts_ms": int(ts_ms), "level": level, "component": comp, "msg": msg,
                     "trace_id": trace_id, "attrs": attrs})

    def active(kind, t_s):
        return [e for e in effects if e["kind"] == kind and _in(e, t_s)]

    # one-off events
    for e in effects:
        if e["kind"] == "event":
            log(T0_MS + e["at_s"] * 1000, e.get("level", "INFO"), e["component"], e["msg"],
                **e.get("attrs", {}))

    t = 0.0
    n = 0
    while t < WINDOW_S:
        rate = rate_per_s
        for e in active("traffic", t):
            rate *= e["factor"]
        t += rng.expovariate(rate)
        if t >= WINDOW_S:
            break
        n += 1
        task = rng.choices(tasks, weights)[0]
        comp, op = TASK_COMPONENT[task], TASK_OP[task]
        profile = "default"
        for e in active("profile", t):
            if _match(e, comp, op) or e.get("component") == "exec-pool":
                profile = e["profile"]
        base = rng.choice(bl["default"][task])
        alt = rng.choice(bl[profile][task]) if profile != "default" else base
        fields = set()
        for e in active("profile", t):
            fields |= set(e.get("fields", ["queue", "execute", "work", "network"]))
        s = {k: (alt[k] if k in fields else base[k]) for k in ("queue", "execute", "work", "network")}
        boot = rng.choice(noop_boot) * rng.uniform(0.9, 1.1)
        work = max(0.01, s["work"] * rng.uniform(0.85, 1.15))
        commit = max(0.0, s["execute"] - s["work"] - boot) if task in WRITING else 0.0
        dur = {"scheduler": rng.uniform(0.6, 2.5), "exec-pool.acquire": s["queue"] * rng.uniform(0.9, 1.1),
               "exec-pool.boot": boot, "work": work, "commit": commit,
               "network": s["network"] * rng.uniform(0.85, 1.15)}
        attrs = {"task": task, "image": "archil/default", "mount": "shared", "concurrency": 1}
        for e in active("attr", t):
            if e.get("task") in (None, task):
                attrs.update(e["set"])
        for e in active("scale", t):
            key = {"exec-pool": "exec-pool.%s" % e.get("op", "acquire"), "scheduler": "scheduler",
                   "commit": "commit"}.get(e.get("component"), "work")
            if key == "work" and not _match(e, comp, op):
                continue
            if key == "commit" and task not in WRITING:
                continue
            dur[key] *= e["factor"] * rng.uniform(0.85, 1.15)
            if "add_ms" in e:
                dur[key] += e["add_ms"] * rng.uniform(0.8, 1.2)
        error = None
        for e in active("error", t):
            if _match(e, comp, op) and rng.random() < e["p"]:
                error = e
                break
        tid, start = _hex(rng, 16), T0_MS + t * 1000
        total = sum(dur.values())
        status = 500 if error else 200
        root = _hex(rng, 8)
        spans.append({"trace_id": tid, "span_id": root, "parent_id": None, "name": "POST /v1/tool-call",
                      "component": "gateway", "start_ms": round(start, 1), "dur_ms": round(total, 2),
                      "status": "ERROR" if error else "OK", "attrs": dict(attrs, http_status=status)})
        cur = start + dur["network"] / 2

        def child(name, c, d, st="OK", extra=None):
            nonlocal cur
            spans.append({"trace_id": tid, "span_id": _hex(rng, 8), "parent_id": root, "name": name,
                          "component": c, "start_ms": round(cur, 1), "dur_ms": round(d, 2), "status": st,
                          "attrs": dict(attrs, **(extra or {}))})
            cur += d

        child("dispatch", "scheduler", dur["scheduler"])
        child("acquire_vm", "exec-pool", dur["exec-pool.acquire"])
        child("boot", "exec-pool", dur["exec-pool.boot"])
        child(op, comp, dur["work"], "ERROR" if error else "OK",
              {"error.code": error["code"]} if error else None)
        if commit and not error:
            child("flush", "commit", dur["commit"])
        if error:
            log(cur, "ERROR", comp, error["msg"].format(n=rng.randint(1, 9999), id=_hex(rng, 6)), tid,
                op=op, code=error["code"])
        log(start + total, "ERROR" if error else "INFO", "gateway",
            "tool call %s status=%d dur_ms=%d" % (task, status, total), tid, task=task)

    # background noise logs, independent of incidents
    for k in range(int(WINDOW_S / 30)):
        ts = T0_MS + k * 30_000 + rng.randint(0, 29_000)
        c = rng.choice(["exec-pool", "worker", "scheduler", "store"])
        msg = {"exec-pool": "pool refill ready_vms=%d" % rng.randint(16, 24),
               "worker": "heartbeat ok rss_mb=%d" % rng.randint(180, 240),
               "scheduler": "queue depth=%d" % rng.randint(0, 3),
               "store": "checkpoint pages=%d" % rng.randint(10, 400)}[c]
        log(ts, "INFO", c, msg)
        if rng.random() < 0.04:
            log(ts + 500, "WARN", "gateway", "slow client upload bytes=%d ms=%d" %
                (rng.randint(1, 900) * 1024, rng.randint(400, 1500)))

    metrics = _metrics(spans, effects, rng)
    logs.sort(key=lambda r: r["ts_ms"])
    spans.sort(key=lambda r: r["start_ms"])
    return {"spans": spans, "logs": logs, "metrics": metrics, "calls": n}


def _metrics(spans, effects, rng):
    steps = WINDOW_S // STEP_S
    buckets = {}
    for sp in spans:
        k = (int((sp["start_ms"] - T0_MS) / 1000 // STEP_S), sp["component"], sp["name"])
        b = buckets.setdefault(k, [[], 0])
        b[0].append(sp["dur_ms"])
        b[1] += sp["status"] == "ERROR"
    rows = []
    names = sorted({(c, n) for (_, c, n) in buckets})
    for i in range(steps):
        ts = T0_MS + i * STEP_S * 1000
        for c, name in names:
            durs, errs = buckets.get((i, c, name), [[], 0])
            series = "%s.%s" % (c, name.replace(" ", "_").replace("/", "_").strip("_"))
            rows.append({"ts_ms": ts, "series": series + ".requests", "value": len(durs)})
            rows.append({"ts_ms": ts, "series": series + ".errors", "value": errs})
            if durs:
                rows.append({"ts_ms": ts, "series": series + ".p50_ms", "value": round(_pct(durs, .5), 2)})
                rows.append({"ts_ms": ts, "series": series + ".p99_ms", "value": round(_pct(durs, .99), 2)})
        gauges = {"exec-pool.ready_vms": 20 + rng.randint(-2, 2), "worker.cpu_pct": 35 + rng.uniform(-6, 6),
                  "store.wal_pages": 300 + rng.randint(-40, 40)}
        for e in effects:
            if e["kind"] == "gauge" and _in(e, i * STEP_S):
                gauges[e["series"]] = gauges.get(e["series"], 0) * e.get("factor", 1) + e.get("add", 0)
        for s, v in gauges.items():
            rows.append({"ts_ms": ts, "series": s, "value": round(v, 2)})
    return rows

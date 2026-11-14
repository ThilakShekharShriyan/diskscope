"""AgentLab CLI.

  python3 -m agentlab prepare --root DIR [--incidents ids]
  python3 -m agentlab prepare-archil --prefix agentlab/<build> [--incidents ids]
  python3 -m agentlab run --backend local|archil --root DIR|--prefix P --arms files,sql,digest
                          [--incidents ids|main|held-out|rcaeval] [--trials N] [--model mock|claude-...|gpt-...]
                          [--live] [--effort medium] [--max-usd 5] [--parallel N] [--out FILE]
  python3 -m agentlab estimate --pilot RUN.json [--incidents ...] [--trials N]
  python3 -m agentlab.report RUN.json ... [--html FILE]

Each finished run is appended to OUT.partial.jsonl immediately, so an interrupted
run keeps what it paid for.

Live models need --live and credentials (ANTHROPIC_API_KEY for claude-*, OPENAI_API_KEY for gpt-*).
The mock model never calls the network.
"""
import argparse
import base64
import concurrent.futures
import json
import os
import random
import sys
import threading
import time
import zlib

from . import harness, incidents, prepare, tools

HERE = os.path.dirname(os.path.abspath(__file__))


def _ids(arg):
    if arg == "rcaeval":
        from . import rcaeval
        return ["rcaeval:" + c["case"] for c in rcaeval.load_subset()]
    if arg in (None, "all"):
        return [i["id"] for i in incidents.INCIDENTS + incidents.HELD_OUT]
    if arg == "main":
        return [i["id"] for i in incidents.INCIDENTS]
    if arg == "held-out":
        return [i["id"] for i in incidents.HELD_OUT]
    ids = arg.split(",")
    bad = [i for i in ids if i not in incidents.ALL and not i.startswith("rcaeval:")]
    if bad:
        sys.exit("unknown incidents: %s" % bad)
    return ids


def _remote_program(incs, prefix):
    """One self-contained Python program: generator + bundle + digest + prepare + these incidents."""
    src = {}
    for mod in ("generate", "bundle", "digest", "prepare"):
        src[mod] = open(os.path.join(HERE, mod + ".py")).read()
    baselines = open(os.path.join(HERE, "baselines.json")).read()
    prog = (
        "import sys,types,json,os\n"
        "SRC=%r\nBL=json.loads(%r)\n"
        "for name in ('generate','bundle','digest','prepare'):\n"
        "    m=types.ModuleType(name); sys.modules[name]=m; exec(SRC[name], m.__dict__)\n"
        "for inc in json.loads(%r):\n"
        "    print('AGENTLAB_JSON='+json.dumps(sys.modules['prepare'].prepare(inc, %r, baselines=BL)), flush=True)\n"
    ) % (src, baselines, json.dumps(incs), "/mnt/archil/" + prefix.strip("/"))
    blob = base64.b64encode(zlib.compress(prog.encode(), 9)).decode()
    return "python3 -S -c \"import base64,zlib;exec(zlib.decompress(base64.b64decode('%s')))\"" % blob


def cmd_prepare(a):
    for i in _ids(a.incidents):
        print(json.dumps(prepare.prepare(incidents.ALL[i], a.root)))


def cmd_prepare_archil(a):
    """Generate incidents inside Archil microVMs, --batch incidents per exec (default 8)."""
    from .archil import Archil
    client = Archil(max_calls=60)
    ids = _ids(a.incidents)
    for k in range(0, len(ids), a.batch):
        chunk = ids[k:k + a.batch]
        r = client.exec(_remote_program([incidents.ALL[i] for i in chunk], a.prefix), timeout=600)
        got = {}
        for x in r["stdout"].splitlines():
            if x.startswith("AGENTLAB_JSON="):
                res = json.loads(x.split("=", 1)[1])
                got[res["id"]] = res
        for i in chunk:
            res = got.get(i) or {"id": i, "error": (r["stderr"] or r["stdout"])[-400:], "exit": r["exit_code"]}
            if a.verify_root and "digest_sha" in res:
                res["matches_local"] = prepare.prepare(incidents.ALL[i], a.verify_root)["digest_sha"] == res["digest_sha"]
            print(json.dumps(res))
        print(json.dumps({"batch": chunk[0] + "..", "n": len(chunk), "wall_ms": round(r["wall_ms"]),
                          "execute_ms": r["execute_ms"]}))


def cmd_run(a):
    arms = a.arms.split(",")
    for arm in arms:
        if arm not in tools.ARMS:
            sys.exit("arm %r not available (implemented: %s; planned: %s)" % (arm, tools.ARMS, tools.PLANNED_ARMS))
    live = a.model != "mock"
    if live and not a.live:
        sys.exit("refusing to call a live model without --live")
    if live and a.backend == "local" and "files" in arms:
        sys.exit("the files arm needs --backend archil for live models (local shell is disabled)")
    jobs = [(i, arm, t) for i in _ids(a.incidents) for arm in arms for t in range(a.trials)]
    random.Random(a.seed).shuffle(jobs)
    # Archil calls per worker: enough for every job to use its whole tool-call budget
    max_calls = a.max_archil_calls or len(jobs) * (harness.MAX_TOOL_CALLS + 2)

    def make_backend():
        if a.backend == "local":
            return tools.LocalBackend(a.root, allow_shell=not live)
        from .archil import Archil
        return tools.ArchilBackend(Archil(max_calls=max_calls), a.prefix)

    obs, lock = [], threading.Lock()
    meter = harness.SpendMeter(a.max_usd if live else None)
    run_id = time.strftime("%Y%m%dT%H%M%S")
    trace_dir = os.path.join(os.path.dirname(a.out) or ".", "agentlab-traces-" + run_id)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    partial = open(a.out + ".partial.jsonl", "a")
    local = threading.local()

    def work(job):
        if meter.over():
            return
        i, arm, t = job
        try:
            if not hasattr(local, "backend"):  # one backend (one kept-alive Archil connection) per worker
                local.backend = make_backend()
            model = (harness.MockModel(arm, harness.task_spec(i)["mock_answer"]) if not live
                     else harness.make_model(a.model, a.effort))
            o = harness.run_one(i, arm, model, local.backend, trial=t, trace_dir=trace_dir, meter=meter)
        except Exception as e:  # noqa: BLE001 - keep the other jobs and everything already paid for
            print("job %s %s %d crashed: %s: %s" % (i, arm, t, type(e).__name__, e), flush=True)
            return
        o["parallel"] = a.parallel > 1
        with lock:
            obs.append(o)
            partial.write(json.dumps(o, default=str) + "\n")
            partial.flush()
            d = o["detail"]
            print("[%d/%d] %-20s %-6s ok=%-5s calls=%d turns=%d usd=%s total=$%.2f %s" % (
                len(obs), len(jobs), i, arm, o["ok"], o["operations"], d["turns"],
                "%.4f" % d["usd"] if d["usd"] is not None else "-", meter.spent, o["error"] or ""), flush=True)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.parallel) as pool:
            for f in [pool.submit(work, j) for j in jobs]:
                f.result()
    except KeyboardInterrupt:
        print("interrupted: writing the %d finished runs" % len(obs))
    partial.close()
    if meter.over():
        print("stopped at the spend cap: $%.2f >= --max-usd %.2f" % (meter.spent, a.max_usd))
    report = {"schema_version": 1, "backend": "agentlab-%s" % a.backend, "run_id": run_id,
              "timestamp_unix_secs": int(time.time()),
              "environment": {"variant": "agentlab · %s · %s" % (a.model, a.backend), "runs": a.trials,
                              "notes": "arms=%s output_cap=%d max_turns=%d max_tool_calls=%d parallel=%d effort=%s" % (
                                  arms, tools.OUTPUT_CAP, harness.MAX_TURNS, harness.MAX_TOOL_CALLS, a.parallel, a.effort),
                              "spent_usd": round(meter.spent, 4), "jobs_planned": len(jobs),
                              "shuffle_seed": a.seed,
                              "disk_suffix": os.environ.get("ARCHIL_DISK_ID", "")[-4:] if a.backend == "archil" else None},
              "observations": obs}
    with open(a.out, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("wrote %s (%d runs, traces in %s)" % (a.out, len(obs), trace_dir))


def cmd_estimate(a):
    """Project the cost of a full matrix from a pilot's measured per-run tokens (per arm)."""
    d = json.load(open(a.pilot))
    by = {}
    for o in d["observations"]:
        if o["detail"]["usd"] is not None:
            by.setdefault(o["detail"]["arm"], []).append(o["detail"]["usd"])
    if not by:
        sys.exit("pilot has no priced runs (mock runs are unpriced)")
    n_inc = len(_ids(a.incidents))
    total = 0.0
    for arm, xs in sorted(by.items()):
        xs.sort()
        med, hi = xs[len(xs) // 2], xs[-1]
        runs = n_inc * a.trials
        total += runs * hi
        print("%-7s pilot n=%d median $%.3f max $%.3f -> %d runs: $%.2f (median) .. $%.2f (max)" % (
            arm, len(xs), med, hi, runs, runs * med, runs * hi))
    print("upper estimate for all arms: $%.2f (uses each arm's most expensive pilot run)" % total)


def main(argv=None):
    p = argparse.ArgumentParser(prog="agentlab")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare")
    s.add_argument("--root", required=True)
    s.add_argument("--incidents")
    s.set_defaults(fn=cmd_prepare)
    s = sub.add_parser("prepare-archil")
    s.add_argument("--prefix", required=True, help="disk-relative dir, e.g. agentlab/b1")
    s.add_argument("--incidents")
    s.add_argument("--verify-root", help="also prepare locally here and compare digests")
    s.add_argument("--batch", type=int, default=8, help="incidents per exec")
    s.set_defaults(fn=cmd_prepare_archil)
    s = sub.add_parser("run")
    s.add_argument("--backend", choices=["local", "archil"], default="local")
    s.add_argument("--root", help="local layout root (local backend)")
    s.add_argument("--prefix", help="disk-relative layout root (archil backend)")
    s.add_argument("--arms", default="files,sql,digest")
    s.add_argument("--incidents")
    s.add_argument("--trials", type=int, default=1)
    s.add_argument("--model", default="mock")
    s.add_argument("--effort", default="medium")
    s.add_argument("--live", action="store_true")
    s.add_argument("--max-usd", type=float, default=5.0)
    s.add_argument("--max-archil-calls", type=int, default=0, help="per worker; 0 = sized from the job count")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--parallel", type=int, default=1,
                   help="concurrent runs; keep 1 when comparing Archil latency (fan-out >= 4 slows calls)")
    s.add_argument("--out", default="results/agentlab.json")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("estimate")
    s.add_argument("--pilot", required=True, help="RunReport from a live pilot")
    s.add_argument("--incidents")
    s.add_argument("--trials", type=int, default=3)
    s.set_defaults(fn=cmd_estimate)
    a = p.parse_args(argv)
    if a.cmd == "run" and a.backend == "local" and not a.root:
        p.error("--root is required for the local backend")
    if a.cmd == "run" and a.backend == "archil" and not a.prefix:
        p.error("--prefix is required for the archil backend")
    a.fn(a)


if __name__ == "__main__":
    main()

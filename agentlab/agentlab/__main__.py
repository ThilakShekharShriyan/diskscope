"""AgentLab CLI.

  python3 -m agentlab prepare --root DIR [--incidents ids]
  python3 -m agentlab prepare-archil --prefix agentlab/<build> [--incidents ids]
  python3 -m agentlab run --backend local|archil --root DIR|--prefix P --arms files,sql,digest
                          [--incidents ids] [--trials N] [--model mock|claude-...] [--live] [--out FILE]

Live models need --live and Anthropic credentials. The mock model never calls the network.
"""
import argparse
import base64
import json
import os
import random
import sys
import time
import zlib

from . import harness, incidents, prepare, tools

HERE = os.path.dirname(os.path.abspath(__file__))


def _ids(arg):
    if arg in (None, "all"):
        return [i["id"] for i in incidents.INCIDENTS + incidents.HELD_OUT]
    if arg == "main":
        return [i["id"] for i in incidents.INCIDENTS]
    if arg == "held-out":
        return [i["id"] for i in incidents.HELD_OUT]
    ids = arg.split(",")
    bad = [i for i in ids if i not in incidents.ALL]
    if bad:
        sys.exit("unknown incidents: %s" % bad)
    return ids


def _remote_program(incident, prefix):
    """One self-contained Python program: generator + bundle + digest + prepare + this incident."""
    src = {}
    for mod in ("generate", "bundle", "digest", "prepare"):
        src[mod] = open(os.path.join(HERE, mod + ".py")).read()
    baselines = open(os.path.join(HERE, "baselines.json")).read()
    prog = (
        "import sys,types,json,os\n"
        "SRC=%r\nBL=json.loads(%r)\n"
        "for name in ('generate','bundle','digest','prepare'):\n"
        "    m=types.ModuleType(name); sys.modules[name]=m; exec(SRC[name], m.__dict__)\n"
        "inc=json.loads(%r)\n"
        "print('AGENTLAB_JSON='+json.dumps(sys.modules['prepare'].prepare(inc, %r, baselines=BL)))\n"
    ) % (src, baselines, json.dumps(incident), "/mnt/archil/" + prefix.strip("/"))
    blob = base64.b64encode(zlib.compress(prog.encode(), 9)).decode()
    return "python3 -S -c \"import base64,zlib;exec(zlib.decompress(base64.b64decode('%s')))\"" % blob


def cmd_prepare(a):
    for i in _ids(a.incidents):
        print(json.dumps(prepare.prepare(incidents.ALL[i], a.root)))


def cmd_prepare_archil(a):
    from .archil import Archil
    client = Archil(max_calls=60)
    for i in _ids(a.incidents):
        local = prepare.prepare(incidents.ALL[i], os.path.join(a.verify_root, a.prefix)) if a.verify_root else None
        r = client.exec(_remote_program(incidents.ALL[i], a.prefix), timeout=300)
        line = [x for x in r["stdout"].splitlines() if x.startswith("AGENTLAB_JSON=")]
        if not line:
            print(json.dumps({"id": i, "error": (r["stderr"] or r["stdout"])[-400:], "exit": r["exit_code"]}))
            continue
        res = json.loads(line[0].split("=", 1)[1])
        res.update(wall_ms=round(r["wall_ms"]), execute_ms=r["execute_ms"])
        if local:
            res["matches_local"] = local["digest_sha"] == res["digest_sha"]
        print(json.dumps(res))


def cmd_run(a):
    arms = a.arms.split(",")
    for arm in arms:
        if arm not in tools.ARMS:
            sys.exit("arm %r not available (implemented: %s; planned: %s)" % (arm, tools.ARMS, tools.PLANNED_ARMS))
    live = a.model != "mock"
    if live and not a.live:
        sys.exit("refusing to call a live model without --live")
    if a.backend == "local":
        backend = tools.LocalBackend(a.root, allow_shell=not live)
    else:
        from .archil import Archil
        backend = tools.ArchilBackend(Archil(max_calls=a.max_archil_calls), a.prefix)
    jobs = [(i, arm, t) for i in _ids(a.incidents) for arm in arms for t in range(a.trials)]
    random.Random(a.seed).shuffle(jobs)
    obs = []
    run_id = time.strftime("%Y%m%dT%H%M%S")
    trace_dir = os.path.join(os.path.dirname(a.out) or ".", "agentlab-traces-" + run_id)
    for n, (i, arm, t) in enumerate(jobs, 1):
        model = harness.MockModel(arm) if not live else harness.LiveModel(a.model, a.effort)
        o = harness.run_one(i, arm, model, backend, trial=t, trace_dir=trace_dir)
        obs.append(o)
        d = o["detail"]
        print("[%d/%d] %-20s %-6s ok=%-5s calls=%d tok_in=%d usd=%s %s" % (
            n, len(jobs), i, arm, o["ok"], o["operations"], d["tokens"]["input_tokens"],
            "%.4f" % d["usd"] if d["usd"] is not None else "-", o["error"] or ""), flush=True)
        spent = sum(x["detail"]["usd"] or 0 for x in obs)
        if live and spent > a.max_usd:
            print("stopping: spent $%.2f > --max-usd %.2f" % (spent, a.max_usd))
            break
    report = {"schema_version": 1, "backend": "agentlab-%s" % a.backend, "run_id": run_id,
              "timestamp_unix_secs": int(time.time()),
              "environment": {"variant": "agentlab · %s · %s" % (a.model, a.backend), "runs": a.trials,
                              "notes": "arms=%s output_cap=%d max_turns=%d" % (arms, tools.OUTPUT_CAP, harness.MAX_TURNS),
                              "shuffle_seed": a.seed,
                              "disk_suffix": getattr(getattr(backend, "client", None), "disk_suffix", None)},
              "observations": obs}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("wrote %s (%d runs, traces in %s)" % (a.out, len(obs), trace_dir))


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
    s.add_argument("--max-archil-calls", type=int, default=300)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--out", default="results/agentlab.json")
    s.set_defaults(fn=cmd_run)
    a = p.parse_args(argv)
    if a.cmd == "run" and a.backend == "local" and not a.root:
        p.error("--root is required for the local backend")
    if a.cmd == "run" and a.backend == "archil" and not a.prefix:
        p.error("--prefix is required for the archil backend")
    a.fn(a)


if __name__ == "__main__":
    main()

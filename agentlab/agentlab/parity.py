"""Do the Archil arms return what the local arms return? Same tool calls through LocalBackend
(shell allowed: these are fixed commands, not model output) and ArchilBackend; prints per-call
equality and per-arm Archil latency medians (queue / execute / wall).

  python -m agentlab.parity --root ../data-ext/al-rcaeval --prefix agentlab/rc1 --incidents rcaeval --n 3

Local shell runs macOS (BSD) tools, Archil runs Ubuntu (GNU) tools, so the shell calls stick to
commands whose output format is the same on both; any remaining difference is reported, not hidden.
"""
import argparse
import json
import statistics
import sys

from . import tools

CALLS = {
    "files": [("shell", {"command": "head -c 300 README.md"}),
              ("shell", {"command": "head -n 2 spans.jsonl"}),
              ("shell", {"command": "grep -c . logs.jsonl"}),
              ("shell", {"command": "grep -c '\"status\":\"ERROR\"' spans.jsonl"}),
              ("shell", {"command": "tail -n 3 metrics.csv"}),
              ("shell", {"command": "jq -r .component spans.jsonl | sort | uniq -c | sort -rn | head -5"}),
              ("shell", {"command": "python3 -c \"import csv,collections;c=collections.Counter(r['series'] "
                                    "for r in csv.DictReader(open('metrics.csv')));print(len(c),c.most_common(3))\""})],
    "sql": [("query", {"sql": "SELECT count(*) AS n FROM spans"}),
            ("query", {"sql": "SELECT component, count(*) AS n, round(avg(dur_ms), 3) AS avg_ms FROM spans "
                              "GROUP BY component ORDER BY n DESC"}),
            ("query", {"sql": "SELECT level, count(*) FROM logs GROUP BY level ORDER BY level"}),
            ("query", {"sql": "SELECT series, max(value) FROM metrics WHERE series LIKE '%cpu' "
                              "GROUP BY series ORDER BY series"}),
            ("query", {"sql": "SELECT ts_ms, series, value FROM metrics ORDER BY ts_ms, series LIMIT 5"}),
            ("query", {"sql": "SELECT name, dur_ms FROM spans ORDER BY dur_ms DESC, span_id LIMIT 3"}),
            # known CLI-vs-Python csv differences: unrounded REALs, zero rows, strings with spaces
            ("query", {"sql": "SELECT avg(dur_ms), avg(value) FROM spans, (SELECT avg(value) AS value FROM metrics)"}),
            ("query", {"sql": "SELECT * FROM logs WHERE 0"}),
            ("query", {"sql": "SELECT DISTINCT name FROM spans WHERE name LIKE '% %' ORDER BY name LIMIT 3"})],
    "digest": [("get_digest", {}), ("drill", {"id": "M1"}), ("drill", {"id": "L1"}), ("drill", {"id": "E1"}),
               ("drill", {"id": "Z9"})],
}


def main(argv=None):
    p = argparse.ArgumentParser(prog="agentlab.parity")
    p.add_argument("--root", required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--incidents", default="rcaeval")
    p.add_argument("--n", type=int, default=3, help="incidents to check")
    p.add_argument("--arms", default="files,sql,digest")
    p.add_argument("--out")
    a = p.parse_args(argv)
    from .__main__ import _ids
    from .archil import Archil
    ids = _ids(a.incidents)[:a.n]
    local = tools.LocalBackend(a.root, allow_shell=True)
    remote = tools.ArchilBackend(Archil(max_calls=500), a.prefix)
    rows = []
    for arm in a.arms.split(","):
        for i in ids:
            for tool, args in CALLS[arm]:
                lo, _ = local.call(i, arm, tool, args)
                ro, meta = remote.call(i, arm, tool, args)
                rows.append({"arm": arm, "incident": i, "tool": tool, "args": args, "same": lo == ro,
                             "chars": len(lo), "wall_ms": round(meta["tool_ms"]), "queue_ms": meta.get("queue_ms"),
                             "execute_ms": meta.get("execute_ms"),
                             **({} if lo == ro else {"local": lo[:300], "archil": ro[:300]})})
                print("%-6s %-28s %-5s %6d chars  wall %5d ms  %s" % (
                    arm, i[-28:], lo == ro, len(lo), meta["tool_ms"], json.dumps(args)[:70]), flush=True)
    summary = {}
    for arm in a.arms.split(","):
        rs = [r for r in rows if r["arm"] == arm]
        med = lambda k: round(statistics.median([r[k] for r in rs if r[k] is not None]))
        summary[arm] = {"calls": len(rs), "identical": sum(r["same"] for r in rs),
                        "median_ms": {"queue": med("queue_ms"), "execute": med("execute_ms"), "wall": med("wall_ms")}}
    print(json.dumps(summary, indent=1))
    for r in rows:
        if not r["same"]:
            print("DIFF %s %s %s\n  local : %r\n  archil: %r" % (r["arm"], r["incident"], r["args"], r["local"][:200],
                                                                 r["archil"][:200]))
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"summary": summary, "rows": rows}, f, indent=1)
    return 0 if all(r["same"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

"""No-LLM baselines on the RCAEval subset, and the arm layouts for agent runs.

  python -m agentlab.rcaeval_baselines --data ../data-ext/rcaeval --root /tmp/al-rcaeval --out FILE

For each subset case: convert -> write files/sql/digest trees (prepare_data) ->
score two rankers that need no model:
  digest-rank  services in order of first appearance in the frozen digest
               (metric lines, then log lines, then rare events); no injection time
  baro         RCAEval's own metric BARO (RobustScaler z-score), given the
               injection time as in the RCAEval paper protocol
Metrics: AC@k = truth in top k; Avg@5 = mean of AC@1..AC@5 (RCAEval's definitions).
"""
import argparse
import json
import os
import re
import time

from . import digest as D
from . import prepare as P
from . import rcaeval as R

_LINE = re.compile(r"^\s+([MLE])\d+ (.*)$")


def digest_rank(text, services):
    """Service ranking read off the digest text, top to bottom."""
    order = []
    svc = set(services)
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        kind, rest = m.groups()
        if kind == "M":
            cand = R.service_of(rest.split()[0])
        elif kind == "L":
            cand = rest.split()[0]
        else:  # E: "+0m00s <component> <LEVEL> ..."
            cand = rest.split()[1]
        if cand in svc and cand not in order:
            order.append(cand)
    return order


def baro_rank(case_dir, services):
    import pandas as pd
    from RCAEval.e2e.baro import baro
    from RCAEval.benchmark.scoring import service_ranks
    # Mirrors RCAEval main.py for baro on re2-*: metrics only, +-10 min (600 rows) around the
    # injection, latency-50 dropped, latency-90 renamed latency, dk_select_useful=False.
    df = pd.read_parquet(os.path.join(case_dir, "metrics.parquet"))
    df = df.set_index("time").ffill().bfill().fillna(0.0).reset_index()
    inject = int(open(os.path.join(case_dir, "inject_time.txt")).read().strip())
    i = int((df["time"] - inject).abs().idxmin())
    df = df.iloc[max(0, i - 600): i + 600]
    df = df.loc[:, ~df.columns.str.endswith("_latency-50")]
    df.columns = [c.replace("_latency-90", "_latency") for c in df.columns]
    ranks = baro(df, inject, dataset="re2-ob", anomalies=None, dk_select_useful=False)["ranks"]
    return [n.entity for n in service_ranks(ranks)]


def ac(order, truth, k):
    return 1.0 if truth in order[:k] else 0.0


def summarize(rows, key):
    n = len(rows)
    acs = {k: sum(ac(r[key], r["service"], k) for r in rows) / n for k in (1, 3, 5)}
    acs["avg5"] = sum(sum(ac(r[key], r["service"], k) for k in range(1, 6)) / 5 for r in rows) / n
    return acs


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--root", required=True, help="where to write the arm layouts")
    p.add_argument("--out", required=True)
    p.add_argument("--no-baro", action="store_true")
    a = p.parse_args(argv)
    rows = []
    for c in R.load_subset():
        cdir = os.path.join(a.data, c["case"])
        t0 = time.time()
        data = R.convert(cdir)
        services = R.services(data)
        res = P.prepare_data("rcaeval:" + c["case"], data, a.root, readme=R.README)
        text = open(os.path.join(a.root, "digest", res["dir"]["digest"], "digest.txt")).read()
        row = dict(c, services=services, digest_rank=digest_rank(text, services), digest_sha=res["digest_sha"])
        if not a.no_baro:
            row["baro_rank"] = baro_rank(cdir, services)
        row["secs"] = round(time.time() - t0, 1)
        rows.append(row)
        print("%-40s truth=%-22s digest=%s baro=%s" % (c["case"], c["service"], row["digest_rank"][:3],
                                                         row.get("baro_rank", [])[:3]), flush=True)
    summary = {"n": len(rows), "digest_rank": summarize(rows, "digest_rank")}
    if not a.no_baro:
        summary["baro"] = summarize(rows, "baro_rank")
    by_fault = {}
    for f in R.FAULTS:
        rs = [r for r in rows if r["fault"] == f]
        by_fault[f] = {k: summarize(rs, k + "_rank" if k == "baro" else k)["avg5"]
                       for k in (["digest_rank"] + ([] if a.no_baro else ["baro"]))}
    summary["avg5_by_fault"] = by_fault
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

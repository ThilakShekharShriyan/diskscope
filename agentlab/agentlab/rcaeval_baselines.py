"""No-LLM baselines on the RCAEval subset, and the arm layouts for agent runs.

  python -m agentlab.rcaeval_baselines [--dataset re2-ob|re2-tt] --data ../data-ext/rcaeval --root /tmp/al-rcaeval --out FILE
  (--all-cases DIR: BARO/N-Sigma only, on every case directory in DIR; no layouts written)

For each subset case: convert -> write files/sql/digest trees (prepare_data) ->
score two rankers that need no model:
  digest-rank  services in order of first appearance in the frozen digest
               (metric lines, then log lines, then rare events); no injection time
  baro, nsigma RCAEval's own metric methods, given the injection time as in the
               RCAEval protocol; also with that time 120 s late (onset-sensitivity check)
  chance       uniform over the services that are ever a root cause in the subset
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


def classic_rank(case_dir, method="baro", shift_s=0, dataset="re2-ob"):
    """RCAEval's own metric method, called as its main.py does for re2-*: metrics only,
    +-10 min (600 rows) around the injection time, latency-50 dropped, latency-90 renamed
    latency, dk_select_useful=False. shift_s moves the injection time given to the method."""
    import pandas as pd
    import RCAEval.e2e as E
    from RCAEval.benchmark.scoring import service_ranks
    from RCAEval.e2e.baro import baro
    fn = {"baro": baro, "nsigma": E.nsigma}[method]
    df = pd.read_parquet(os.path.join(case_dir, "metrics.parquet"))
    df = df.set_index("time").ffill().bfill().fillna(0.0).reset_index()
    inject = int(open(os.path.join(case_dir, "inject_time.txt")).read().strip()) + shift_s
    i = int((df["time"] - inject).abs().idxmin())
    df = df.iloc[max(0, i - 600): i + 600]
    df = df.loc[:, ~df.columns.str.endswith("_latency-50")]
    df.columns = [c.replace("_latency-90", "_latency") for c in df.columns]
    ranks = fn(df, inject, dataset=dataset, anomalies=None, dk_select_useful=False)["ranks"]
    return [n.entity for n in service_ranks(ranks)]


def baro_rank(case_dir, services=None):
    return classic_rank(case_dir, "baro")


CLASSIC = {"baro": ("baro", 0), "nsigma": ("nsigma", 0), "baro+120s": ("baro", 120), "nsigma+120s": ("nsigma", 120)}


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
    p.add_argument("--root", help="where to write the arm layouts (required unless --all-cases)")
    p.add_argument("--out", required=True)
    p.add_argument("--no-baro", action="store_true")
    p.add_argument("--dataset", default="re2-ob", choices=sorted(DATASETS))
    p.add_argument("--jobs", type=int, default=1, help="cases converted in parallel (separate processes)")
    p.add_argument("--download", action="store_true", help="first fetch the subset's cases from Hugging Face into --data")
    p.add_argument("--all-cases", action="store_true", help="BARO/N-Sigma on every case in --data, no layouts")
    a = p.parse_args(argv)
    if a.all_cases:
        return all_cases(a.data, a.dataset, a.out)
    if not a.root:
        p.error("--root is required")
    suite = DATASETS[a.dataset]
    cases = R.load_subset(suite)
    if a.download:
        for c in cases:
            R.download(c["case"], a.data)
    if a.jobs > 1:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(a.jobs) as ex:
            rows = list(ex.map(_one, [(c, a.data, a.root, suite, a.no_baro) for c in cases]))
    else:
        rows = [_one((c, a.data, a.root, suite, a.no_baro)) for c in cases]
    _finish(rows, a)


def all_cases(data_root, dataset, out):
    """BARO/N-Sigma on every case directory (metrics only): the paper-reproduction check."""
    rows = []
    for case in sorted(os.listdir(data_root)):
        cdir = os.path.join(data_root, case)
        if not os.path.exists(os.path.join(cdir, "inject_time.txt")):
            continue
        svc, fault = case.split("_")[1], case.split("_")[2]
        row = {"case": case, "service": svc, "fault": fault}
        for name, (method, shift) in CLASSIC.items():
            row[name + "_rank"] = classic_rank(cdir, method, shift, dataset)
        rows.append(row)
    summary = {"n": len(rows), "dataset": dataset}
    for name in CLASSIC:
        summary[name] = summarize(rows, name + "_rank")
        summary[name]["by_fault_ac1"] = {f: summarize([r for r in rows if r["fault"] == f], name + "_rank")[1]
                                         for f in R.FAULTS if any(r["fault"] == f for r in rows)}
    with open(out, "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print(json.dumps(summary, indent=1))


DATASETS = {"re2-ob": "rcaeval", "re2-tt": "rcaevaltt"}


def _one(job):
    """Convert one case, write its layouts, score every ranker (one process per case when --jobs > 1)."""
    import resource
    c, data_root, root, suite, no_baro = job
    S = R.SUITES[suite]
    cdir = os.path.join(data_root, c["case"])
    t0 = time.time()
    data = R.convert(cdir)
    services = R.services(data)
    n_spans, n_logs = len(data["spans"]), len(data["logs"])
    t1 = time.time()
    res = P.prepare_data(suite + ":" + c["case"], data, root, readme=S["readme"])
    del data
    text = open(os.path.join(root, "digest", res["dir"]["digest"], "digest.txt")).read()
    row = dict(c, services=services, digest_rank=digest_rank(text, services), digest_sha=res["digest_sha"],
               n_spans=n_spans, n_logs=n_logs, convert_secs=round(t1 - t0, 1),
               prepare_secs=round(time.time() - t1, 1))
    if not no_baro:
        for name, (method, shift) in CLASSIC.items():
            row[name + "_rank"] = classic_rank(cdir, method, shift, S["rc_dataset"])
    row["secs"] = round(time.time() - t0, 1)
    # ru_maxrss is bytes on macOS, KiB on Linux
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    row["peak_rss_mb"] = round(rss / (1 << 20) if os.uname().sysname == "Darwin" else rss / 1024)
    print("%-40s truth=%-22s digest=%s nsigma=%s %ss" % (c["case"], c["service"], row["digest_rank"][:3],
                                                          row.get("nsigma_rank", [])[:3], row["secs"]), flush=True)
    return row


def _finish(rows, a):
    summary = {"n": len(rows), "dataset": a.dataset, "digest_rank": summarize(rows, "digest_rank")}
    names = ["digest_rank"]
    if not a.no_baro:
        for name in CLASSIC:
            summary[name] = summarize(rows, name + "_rank")
            names.append(name + "_rank")
    # Chance among the services that are ever a root cause in this subset (5 in RE2-OB and in RE2-TT)
    k_cand = len({r["service"] for r in rows})
    summary["chance_candidates"] = {"n_candidates": k_cand, "1": 1 / k_cand, "3": min(1, 3 / k_cand),
                                    "5": min(1, 5 / k_cand), "avg5": sum(min(1, k / k_cand) for k in range(1, 6)) / 5}
    summary["avg5_by_fault"] = {f: {n: summarize([r for r in rows if r["fault"] == f], n)["avg5"] for n in names}
                                for f in R.FAULTS}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

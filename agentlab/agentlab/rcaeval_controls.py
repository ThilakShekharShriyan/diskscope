"""Controls for the RCAEval suite: healthy (no-fault) windows and the digest decoy/placebo.

MIXED SUITE ("rcaevalmix"): every task offers a "no_fault" answer, and some tasks are healthy.
  rcaevalm:<case>  the real fault, window cropped to WINDOW_S (11 min) around the injection,
                   injection at a seeded offset of 210-450 s into the window (not the midpoint)
  rcaevalq:<case>  a healthy window: the first WINDOW_S of the same case, which ends GUARD_S
                   (60 s) before the injection, so it contains no fault. Truth = no_fault.
  The default suite is all 30 rcaevalm tasks + N_QUIET (6) seeded rcaevalq tasks (6/36 = 17%
  healthy); `rcaevalq` alone gives all 30 healthy windows for a larger false-positive study.

Why a mixed suite rather than offering "no_fault" only on healthy tasks: if the schema or
prompt differed between the two, the agent could tell a control from a real task without
looking at the telemetry, and real-fault tasks would never require ruling out "nothing is
wrong". Here the system prompt, user prompt, submit schema, README text, window length and
tool descriptions are byte-identical for both kinds (tests/test_controls.py checks this).

Why crop the real windows too: RCAEval RE2 windows are 24 min with the injection at exactly
12 min, and only 12 min (720 s) precede the injection. A healthy window can therefore be at
most ~11 min long; if real tasks stayed 24 min, the window length (stated in the README and
in the digest's first line) would give the answer away. Cropping to the same 11 min also
removes the midpoint leak: the injection sits at a seeded 210-450 s (32-68% of the window).
Cost of cropping: less post-fault data (210-450 s instead of 720 s) and less baseline before
it, so rcaevalm accuracy is not directly comparable to the 24-min rcaeval suite; compare
arms within the mixed suite.

Healthy windows are not screened by any arm's statistics (screening with the digest would
bias the controls toward the digest arm). Per-window digest statistics are recorded in the
manifest so unclean windows can be dropped after the fact, by a rule fixed in advance.

Prepare (writes a NEW layout dir; per-arm opaque ids keyed on the new task ids):
  python -m agentlab.rcaeval_controls --data ../data-ext/rcaeval --root ../data-ext/al-rcaeval-controls
which also writes digest-decoy/ and digest-placebo/ layouts for rcaeval:, rcaevalm: and
rcaevalq: tasks, and agentlab/rcaeval_decoys.json (which service is the decoy, per task id).
"""
import argparse
import json
import os
import random
import time

from . import rcaeval as R

CONTROL_SEED = 20261115
WINDOW_S = 660          # 11 min: what fits before every injection with the guard
GUARD_S = 60            # healthy windows end this long before the injection
OFFSET_RANGE = (210, 450)
N_QUIET = 6
NO_FAULT = "no_fault"
PREFIXES = ("rcaevalm:", "rcaevalq:")
HERE = os.path.dirname(os.path.abspath(__file__))
DECOY_FILE = os.path.join(HERE, "rcaeval_decoys.json")


# ---------------- suite definition (no data needed) ----------------
def inject_offset(case):
    """Seconds from the start of the cropped real window to the injection; seeded per case."""
    return random.Random("%s:offset:%s" % (CONTROL_SEED, case)).randint(*OFFSET_RANGE)


def quiet_cases(n=N_QUIET):
    cases = sorted(c["case"] for c in R.load_subset())
    return sorted(random.Random("%s:quiet" % CONTROL_SEED).sample(cases, n))


def mix_ids():
    return ["rcaevalm:" + c["case"] for c in R.load_subset()] + ["rcaevalq:" + c for c in quiet_cases()]


def quiet_ids():
    return ["rcaevalq:" + c["case"] for c in R.load_subset()]


def window_ms(variant, inject_s, t0_s):
    """[start, end) in epoch ms of the window shown for this variant of a case."""
    if variant == "rcaevalq":
        end = (inject_s - GUARD_S) * 1000
        return end - WINDOW_S * 1000, end
    raise ValueError(variant)


def real_window_ms(case, inject_s, last_s=None):
    """The seeded offset, moved earlier only if the case's data ends before a full window
    (re2ob_checkoutservice_mem_2 has 283 s after the injection), so every window is WINDOW_S long."""
    off = inject_offset(case)
    if last_s is not None:
        off = max(off, WINDOW_S - (last_s + 1 - inject_s))
    start = (inject_s - off) * 1000
    return start, start + WINDOW_S * 1000


def crop(data, start_ms, end_ms):
    """Keep only records that start inside [start_ms, end_ms)."""
    keep = lambda t: start_ms <= t < end_ms
    spans = [s for s in data["spans"] if keep(s["start_ms"])]
    return {"spans": spans, "logs": [r for r in data["logs"] if keep(r["ts_ms"])],
            "metrics": [r for r in data["metrics"] if keep(r["ts_ms"])], "calls": len(spans)}


# ---------------- agent task (identical text for real and healthy) ----------------
README = R.README.replace("The window is about 24 minutes.", "The window is about 11 minutes.")

SYSTEM = """You are the on-call engineer for Online Boutique, a microservice web shop (services: \
{services}). You have about 11 minutes of its telemetry: per-service metrics at 1 s, logs and traces.

Some windows contain a fault injected into exactly one service; others are healthy. Decide whether \
there is a fault. If there is, find the root-cause service (not just the services showing symptoms) \
and the fault type, and list up to 5 services, most likely first. If the window is healthy, answer \
fault "no_fault" with an empty services list. Use the tools to investigate, then call submit_answer \
exactly once. You have at most {turns} turns.

Fault types:
- cpu: CPU stress on the service's container
- mem: memory stress
- disk: disk I/O stress
- delay: added network latency
- loss: network packet loss
- socket: socket exhaustion
- no_fault: nothing was injected; the window is healthy"""

SUBMIT = json.loads(json.dumps(R.SUBMIT))
SUBMIT["input_schema"]["properties"]["fault"] = {"type": "string", "enum": R.FAULTS + [NO_FAULT],
                                                 "description": "Fault type, or no_fault if the window is healthy."}
SUBMIT["input_schema"]["properties"]["services"]["description"] = (
    "Up to 5 candidate root-cause services, most likely first; empty if no_fault.")


def task(incident_id):
    variant, case = incident_id.split(":", 1)
    c = {x["case"]: x for x in R.load_subset()}[case]
    quiet = variant == "rcaevalq"
    truth = {"components": [], "cause": NO_FAULT} if quiet else {"components": [c["service"]], "cause": c["fault"]}
    return {"truth": truth, "evidence": [], "variant": variant, "quiet": quiet,
            "system": SYSTEM.replace("{services}", ", ".join(R.OB_SERVICES)), "user": R.USER, "submit": SUBMIT,
            "tool_text": R.TOOL_TEXT, "grade": grade, "held_out": False,
            "mock_answer": {"services": ["frontend"], "fault": "cpu", "evidence": [], "summary": "mock"}}


def grade(t, answer, tool_outputs):
    """Real task: RCAEval AC@k (a no_fault answer is a miss and a false negative).
    Healthy task: correct iff the answer is no_fault; anything else is a false positive."""
    said_none = answer is not None and answer.get("fault") == NO_FAULT
    if t["quiet"]:
        ok = said_none
        return {"correct": ok, "component_ok": ok, "cause_ok": ok, "retrieved": None,
                "submitted": answer is not None, "quiet": True,
                "false_positive": (answer is not None and not ok),
                "named_first": (list(answer.get("services") or [None])[0] if answer and not ok else None)}
    g = R.grade(t, None if said_none else answer, tool_outputs)
    g.update(submitted=answer is not None, quiet=False, false_negative=said_none)
    return g


# ---------------- decoy manifest ----------------
_DECOYS = None


def decoy_for(task_id):
    """Decoy metadata for a task id (from rcaeval_decoys.json), or None."""
    global _DECOYS
    if _DECOYS is None:
        try:
            with open(DECOY_FILE) as f:
                _DECOYS = json.load(f)
        except (OSError, ValueError):
            _DECOYS = {}
    return _DECOYS.get(task_id)


# ---------------- prepare ----------------
def write_digest_variant(root, task_id, arm, text, index, salt=None):
    from . import prepare as P
    d = os.path.join(root, arm, P.opaque(task_id, salt or P.DEFAULT_SALT, arm))
    os.makedirs(os.path.join(d, "drill"), exist_ok=True)
    with open(os.path.join(d, "digest.txt"), "w") as f:
        f.write(text)
    with open(os.path.join(d, "index.json"), "w") as f:
        json.dump(index, f)
    for k, v in index.items():
        with open(os.path.join(d, "drill", k + ".txt"), "w") as f:
            f.write(v)


def prepare_task(task_id, data, root, truth_service, readme, full=True):
    """All arm layouts (full=True) or only the plain digest, plus the decoy and placebo digests."""
    from . import digest as D
    from . import digest_decoy as DD
    from . import prepare as P
    if full:
        res = P.prepare_data(task_id, data, root, readme=readme)
        dd = os.path.join(root, "digest", res["dir"]["digest"])
        text = open(os.path.join(dd, "digest.txt")).read()
        index = json.load(open(os.path.join(dd, "index.json")))
    else:
        text, index = D.digest(data)
        write_digest_variant(root, task_id, "digest", text, index)
        import hashlib
        res = {"id": task_id, "digest_sha": hashlib.sha256(text.encode()).hexdigest()[:12]}
    variants = DD.make_variants(task_id, data, text, index, truth_service)
    meta = None
    for kind, (t2, i2, m) in variants.items():
        write_digest_variant(root, task_id, "digest-" + kind, t2, i2)
        meta = dict(meta or {}, **{k: v for k, v in m.items() if k not in ("kind", "line")})
        meta["line_" + kind] = m["line"]
    found = DD._real_mlines(text)
    res["screen"] = {"n_metric_lines": len(found), "top_series": found[0]["series"] if found else None,
                     "top_score": found[0]["score"] if found else 0,
                     "n_log_lines": sum(1 for x in text.splitlines() if x.startswith("  L")),
                     "window_min": text.split(" min window")[0].split()[-1]}
    return res, meta


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="RCAEval raw cases (data-ext/rcaeval)")
    p.add_argument("--root", required=True, help="NEW layout dir to write")
    p.add_argument("--cases", help="comma-separated subset of case names (default: all 30)")
    p.add_argument("--skip-full", action="store_true", help="skip the 24-min rcaeval: digest variants")
    p.add_argument("--manifest", default=DECOY_FILE)
    p.add_argument("--out", help="also write per-task prepare results (screen stats) here")
    a = p.parse_args(argv)
    import pandas as pd
    subset = R.load_subset()
    if a.cases:
        want = set(a.cases.split(","))
        subset = [c for c in subset if c["case"] in want]
    manifest = {}
    if os.path.exists(a.manifest):
        manifest = json.load(open(a.manifest))
    results = []
    for c in subset:
        t_start = time.time()
        cdir = os.path.join(a.data, c["case"])
        inject_s = int(open(os.path.join(cdir, "inject_time.txt")).read().strip())
        tcol = pd.read_parquet(os.path.join(cdir, "metrics.parquet"), columns=["time"])["time"]
        t0_s, last_s = int(tcol.min()), int(tcol.max())
        data = R.convert(cdir)
        real_win = real_window_ms(c["case"], inject_s, last_s)
        jobs = [("rcaevalm:" + c["case"], crop(data, *real_win), c["service"], True),
                ("rcaevalq:" + c["case"], crop(data, *window_ms("rcaevalq", inject_s, t0_s)), None, True)]
        if not a.skip_full:
            jobs.append(("rcaeval:" + c["case"], data, c["service"], False))
        for tid, d, truth, full in jobs:
            res, meta = prepare_task(tid, d, a.root, truth, README, full=full)
            if tid.startswith("rcaevalm:"):
                res["screen"]["inject_offset_s"] = inject_s - real_win[0] // 1000
            if tid.startswith("rcaevalq:"):
                res["screen"]["window_end_before_inject_s"] = inject_s - max(r["ts_ms"] for r in d["metrics"]) // 1000
            if meta:
                manifest[tid] = meta
            results.append(res)
            print(json.dumps({k: res[k] for k in ("id", "digest_sha", "screen") if k in res}), flush=True)
        print("# %s %.0fs" % (c["case"], time.time() - t_start), flush=True)
    with open(a.manifest, "w") as f:
        json.dump(dict(sorted(manifest.items())), f, indent=1)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()

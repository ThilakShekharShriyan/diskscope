"""Digest decoy and placebo: one inserted line, by post-processing the frozen digest's OUTPUT.

digest.py is frozen and is not touched; this module only rewrites its (text, index).

Both variants insert exactly one line in the same slot (the first METRICS line, id M1;
the real metric lines are renumbered M2..), about one innocent service's metric series:

  digest-decoy    a FALSE change point: real "before" mean, fabricated "after" (x2.5-5),
                  a score above every real line, onset near the top real line's onset.
  digest-placebo  the SAME series and onset, with honest numbers (before/after means
                  and score computed from the data exactly as the digest computes them).

drill(M1) returns the series' real per-minute means (flat for an innocent series) in
the digest's own drill format, identically in both variants. So the decoy is
refutable from the evidence; the placebo is a true but uninformative mention at the top.

Innocent service: deterministic per task id (seeded), never the root-cause service,
preferably one the digest does not mention at all. The chosen service, series and
claimed numbers are returned as metadata so the report can compute how often the
agent names the decoy service first ("decoy capture"). Stdlib only.
"""
import random
import re
import statistics

try:
    from . import digest as D
except ImportError:  # pragma: no cover - flat namespace
    import digest as D

DECOY_SEED = 20261115
METRICS = ["cpu", "mem", "latency-90", "socket", "diskio"]  # preference pool; seeded choice among those present
_MLINE = re.compile(r"^  M(\d+) (\S+)  (.*)$")
_LINE = re.compile(r"^\s+([MLE])\d+ (.*)$")


def _service_of(series):
    return series.rsplit("_", 1)[0]


def mentioned_services(text, services):
    """Services the digest names, in order of first appearance (same reading as rcaeval_baselines.digest_rank)."""
    svc, order = set(services), []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        kind, rest = m.groups()
        parts = rest.split()
        cand = _service_of(parts[0]) if kind == "M" else (parts[0] if kind == "L" else (parts[1] if len(parts) > 1 else ""))
        if cand in svc and cand not in order:
            order.append(cand)
    return order


def _window(metrics):
    """Steps, t0, step and minutes exactly as digest.digest derives them."""
    steps = sorted({r["ts_ms"] for r in metrics})
    t0 = steps[0]
    gaps = sorted(b - a for a, b in zip(steps, steps[1:]))
    step_s = max(1, round(gaps[len(gaps) // 2] / 1000)) if gaps else 10
    mins = max(1, -(-(steps[-1] + step_s * 1000 - t0) // 60000))
    return steps, t0, step_s, mins


def _real_mlines(text):
    out = []
    for line in text.splitlines():
        m = _MLINE.match(line)
        if m:
            onset = re.search(r"from \+(\d+)m(\d+)s", line)
            score = re.search(r"score (\d+)", line)
            out.append({"id": int(m.group(1)), "series": m.group(2),
                        "onset_s": int(onset.group(1)) * 60 + int(onset.group(2)) if onset else None,
                        "score": int(score.group(1)) if score else 0})
    return out


def choose(task_id, data, text, truth_service=None, seed=DECOY_SEED):
    """Deterministic choice of (service, series, onset) for this task. None if no usable series."""
    rng = random.Random("%s:%s" % (seed, task_id))
    series = {}
    for r in data["metrics"]:
        series.setdefault(r["series"], []).append(r)
    services = sorted({_service_of(s) for s in series})
    named = mentioned_services(text, services)
    pool = [s for s in services if s != truth_service and s not in named]
    tier = "unmentioned"
    if not pool:
        pool, tier = [s for s in services if s != truth_service and s not in named[:3]], "mentioned-below-top3"
    steps, t0, step_s, mins = _window(data["metrics"])
    rng.shuffle(pool)
    for svc in pool:
        mets = [m for m in METRICS if svc + "_" + m in series]
        rng.shuffle(mets)
        for m in mets:
            name = svc + "_" + m
            pts = {r["ts_ms"]: r["value"] for r in series[name]}
            tss = [ts for ts in steps if ts in pts]
            v = [pts[ts] for ts in tss]
            if len(v) < 300 or statistics.mean(v) < 0.5:  # values that print as more than "0.0"
                continue
            real = _real_mlines(text)
            win_s = (steps[-1] - t0) // 1000
            if real and real[0]["onset_s"] is not None:
                onset = real[0]["onset_s"] + rng.randint(-40, 40)
            else:
                onset = rng.randint(win_s // 3, 2 * win_s // 3)
            onset = max(D.MIN_PRE_S, min(win_s - D.MIN_POST_S, onset))
            k = next((i for i, ts in enumerate(tss) if (ts - t0) // 1000 >= onset), len(tss) // 2)
            return {"service": svc, "series": name, "metric": m, "tier": tier, "v": v, "tss": tss, "k": k,
                    "t0": t0, "mins": mins, "rng": rng, "real": real}
    return None


def _ratio_txt(a, b):
    ratio = (b / a) if a else float("inf")
    return ("x%.1f" % ratio) if a and ratio < 1000 else "from ~0"


def make_variants(task_id, data, text, index, truth_service=None, seed=DECOY_SEED):
    """-> {"decoy": (text, index, meta), "placebo": (text, index, meta)}, or {} if no usable series."""
    c = choose(task_id, data, text, truth_service, seed)
    if c is None:
        return {}
    v, k, rng, real = c["v"], c["k"], c["rng"], c["real"]
    before, after = v[:k], v[k:]
    a, b = statistics.mean(before), statistics.mean(after)
    spread = statistics.pstdev(before) if len(before) > 1 else 0.0
    honest_score = abs(b - a) / max(spread, 0.05 * abs(a) + 0.5)
    onset_s = int((c["tss"][k] - c["t0"]) / 1000)
    factor = round(rng.uniform(2.5, 5.0), 2)
    top = max([m["score"] for m in real] or [0])
    fake_score = int(top * rng.uniform(1.05, 1.3)) + 1 if top else rng.randint(20, 80)
    drill = D._drill_metric(c["series"], v, c["tss"], k, data["spans"], c["t0"], c["mins"])
    lines = {
        "decoy": "  M1 %s  %s -> %s (%s) from %s  score %d" % (
            c["series"], D._num(a), D._num(a * factor), _ratio_txt(a, a * factor), D._fmt_t(onset_s), fake_score),
        "placebo": "  M1 %s  %s -> %s (%s) from %s  score %.0f" % (
            c["series"], D._num(a), D._num(b), _ratio_txt(a, b), D._fmt_t(onset_s), honest_score),
    }
    meta = {"service": c["service"], "series": c["series"], "metric": c["metric"], "tier": c["tier"],
            "onset_s": onset_s, "before": round(a, 6), "after_true": round(b, 6), "honest_score": round(honest_score, 2),
            "claimed_after": round(a * factor, 6), "claimed_factor": factor, "claimed_score": fake_score,
            "id": "M1", "top_real_series": real[0]["series"] if real else None}
    out = {}
    for kind, newline in lines.items():
        t2, i2 = insert_first_metric(text, index, newline, drill)
        out[kind] = (t2, i2, dict(meta, kind=kind, line=newline.strip()))
    return out


def insert_first_metric(text, index, newline, drill_text):
    """Put newline as M1 under METRICS, renumber real M lines (and their index keys) by +1."""
    out, in_metrics, placed = [], False, False
    for line in text.splitlines():
        if line.startswith("METRICS"):
            in_metrics = True
            out.append(line)
            continue
        if in_metrics and not placed:
            out.append(newline)
            placed = True
            if line.strip() == "none above threshold":
                continue
        if in_metrics and (not line.strip() or not line.startswith("  ")):
            in_metrics = False
        m = _MLINE.match(line) if in_metrics else None
        if m:
            line = "  M%d %s  %s" % (int(m.group(1)) + 1, m.group(2), m.group(3))
        out.append(line)
    if not placed:
        raise ValueError("digest text has no METRICS section")
    new_index = {"M1": drill_text}
    for key, val in index.items():
        if key[0] == "M" and key[1:].isdigit():
            new_index["M%d" % (int(key[1:]) + 1)] = val
        else:
            new_index[key] = val
    return "\n".join(out), new_index

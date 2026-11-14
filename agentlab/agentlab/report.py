"""AgentLab results: statistics, tables and charts from RunReport JSON files. Stdlib only.

  python -m agentlab.report RUN.json [RUN.json ...] [--baselines rcaeval-baselines.json]
                            [--html docs/agentlab.html] [--md FILE] [--json FILE]

Per (model, arm): accuracy (service AC@1/3/5, Avg@5, fault type, joint), each with a
95% interval; consistency over trials (pass@k: any trial right, pass^k: all right);
cost (tokens, cache share, dollars, tool calls, turns, model and tool time); tool
errors and empty results; fault-type confusion. Between arms of the same model:
paired per-case differences with a paired bootstrap interval and an exact McNemar
test on per-case majority correctness.

Statistics:
- Runs of the same case are not independent. Intervals for means resample CASES
  (cluster bootstrap, B=4000, seeded), averaging each case's trials first.
- Wilson intervals are shown for single-trial proportions (n = cases).
- With n around 30 cases, differences under ~15 points are within noise; the page says so.
"""
import argparse
import html
import json
import math
import os
import random
import statistics as st

B = 4000
EXCLUDED = []
SEED = 20261114


# ---------------- loading ----------------
def load(paths):
    """Graded runs. Infra failures (provider/Archil errors, spend cap) are excluded from every
    statistic and counted separately; runs served by a different model than requested too."""
    runs = []
    for p in paths:
        rep = json.load(open(p))
        for o in rep["observations"]:
            d = o["detail"]
            if d.get("model") == "mock":
                continue
            if d.get("infra_error") or d.get("model_mismatch"):
                EXCLUDED.append({"case": o["task"], "arm": d["arm"], "model": d["model"], "error": o.get("error"),
                                 "mismatch": bool(d.get("model_mismatch"))})
                continue
            g = d["grade"]
            tok = d["tokens"]
            calls = d.get("calls", [])
            outs = [c.get("output", "") for c in calls]
            runs.append({
                "case": o["task"].split(":", 1)[-1], "trial": o["trial"], "model": d["model"],
                "arm": d["arm"] + ("+time" if str(d.get("variant", "")).endswith("+t") else ""),
                "ac1": g.get("ac1", 1.0 if g.get("component_ok") else 0.0),
                "ac3": g.get("ac3", 1.0 if g.get("component_ok") else 0.0),
                "ac5": g.get("ac5", 1.0 if g.get("component_ok") else 0.0),
                "avg5": g.get("avg5", 1.0 if g.get("component_ok") else 0.0),
                "fault_ok": 1.0 if g.get("cause_ok") else 0.0,
                "correct": 1.0 if g.get("correct") else 0.0,
                "submitted": 1.0 if g.get("submitted") else 0.0,
                "truth_fault": d["truth"].get("cause"),
                "answer_fault": (d.get("answer") or {}).get("fault", (d.get("answer") or {}).get("cause")),
                "in_tok": tok["input_tokens"] + tok["cache_read_input_tokens"] + tok["cache_creation_input_tokens"],
                "out_tok": tok["output_tokens"],
                "cache_share": (tok["cache_read_input_tokens"] / max(1, tok["input_tokens"] + tok["cache_read_input_tokens"]
                                                                     + tok["cache_creation_input_tokens"])),
                "usd": d.get("usd") or 0.0, "calls": len(calls), "turns": d.get("turns", 0),
                "model_s": d.get("model_ms", 0) / 1000, "tool_s": d.get("tool_ms", 0) / 1000,
                "tool_err": sum(1 for x in outs if _is_err(x)), "tool_empty": sum(1 for x in outs if _is_empty(x)),
                "error": o.get("error"), "stop": d.get("stop_reason"),
            })
    return runs


def _is_err(out):
    s = out.lstrip()[:60].lower()
    return s.startswith(("sqlite error", "tool error", "error:", "parse error", "runtime error")) or "no such" in s


def _is_empty(out):
    lines = [x for x in out.strip().splitlines() if x.strip()]
    return len(lines) == 0 or (len(lines) == 1 and "," in lines[0] and not _is_err(out))  # header only


# ---------------- statistics ----------------
def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def by_case(rows, key):
    cases = {}
    for r in rows:
        cases.setdefault(r["case"], []).append(r[key])
    return {c: sum(v) / len(v) for c, v in cases.items()}


def cluster_ci(rows, key, seed=SEED):
    """Mean over cases (trials averaged within case) with a case-resampling bootstrap CI."""
    per = list(by_case(rows, key).values())
    if not per:
        return (float("nan"), (float("nan"), float("nan")))
    point = sum(per) / len(per)
    if len(per) < 3:
        return (point, (float("nan"), float("nan")))
    rng = random.Random(seed)
    n = len(per)
    boots = sorted(sum(per[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return (point, (boots[int(0.025 * B)], boots[int(0.975 * B) - 1]))


def paired(rows_a, rows_b, key, seed=SEED):
    """Mean of per-case differences a-b over cases present in both, paired bootstrap CI."""
    a, b = by_case(rows_a, key), by_case(rows_b, key)
    common = sorted(set(a) & set(b))
    diffs = [a[c] - b[c] for c in common]
    if not diffs:
        return None
    point = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    n = len(diffs)
    boots = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(B))
    return {"n_cases": n, "diff": point, "ci": (boots[int(0.025 * B)], boots[int(0.975 * B) - 1])}


def mcnemar(rows_a, rows_b, key):
    """Exact two-sided McNemar on per-case majority correctness (>= half of trials right)."""
    a, b = by_case(rows_a, key), by_case(rows_b, key)
    common = set(a) & set(b)
    only_a = sum(1 for c in common if a[c] >= 0.5 > b[c])
    only_b = sum(1 for c in common if b[c] >= 0.5 > a[c])
    n = only_a + only_b
    if n == 0:
        return {"only_a": 0, "only_b": 0, "p": 1.0}
    k = min(only_a, only_b)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"only_a": only_a, "only_b": only_b, "p": p}


def quant(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def consistency(rows, key="correct"):
    trials = {}
    for r in rows:
        trials.setdefault(r["case"], []).append(r[key])
    ks = [len(v) for v in trials.values()]
    if not ks or max(ks) < 2:
        return None
    return {"k": min(ks), "pass_at_k": sum(1 for v in trials.values() if max(v) == 1) / len(trials),
            "pass_hat_k": sum(1 for v in trials.values() if min(v) == 1) / len(trials)}


def confusion(rows):
    m = {}
    for r in rows:
        m.setdefault(r["truth_fault"], {}).setdefault(r["answer_fault"] or "-", 0)
        m[r["truth_fault"]][r["answer_fault"] or "-"] += 1
    return m


def summarize(runs):
    groups = {}
    for r in runs:
        groups.setdefault((r["model"], r["arm"]), []).append(r)
    out = []
    for (model, arm), rows in sorted(groups.items()):
        n_cases = len({r["case"] for r in rows})
        s = {"model": model, "arm": arm, "runs": len(rows), "cases": n_cases,
             "trials": round(len(rows) / max(1, n_cases), 2)}
        for k in ("ac1", "ac3", "avg5", "fault_ok", "correct"):
            point, ci = cluster_ci(rows, k)
            s[k] = {"mean": point, "ci": ci}
        s["ac1"]["wilson_cases"] = wilson(round(s["ac1"]["mean"] * n_cases), n_cases)
        s["consistency"] = consistency(rows)
        for k in ("in_tok", "out_tok", "usd", "calls", "turns", "model_s", "tool_s", "cache_share"):
            xs = [r[k] for r in rows]
            s[k] = {"median": quant(xs, 0.5), "p90": quant(xs, 0.9), "sum": sum(xs)}
        s["tool_err_rate"] = sum(r["tool_err"] for r in rows) / max(1, sum(r["calls"] for r in rows))
        s["tool_empty_rate"] = sum(r["tool_empty"] for r in rows) / max(1, sum(r["calls"] for r in rows))
        s["unsubmitted"] = sum(1 for r in rows if not r["submitted"])
        s["run_errors"] = sorted({r["error"] for r in rows if r["error"]})
        s["confusion"] = confusion(rows)
        s["usd_per_correct"] = s["usd"]["sum"] / max(1e-9, sum(r["correct"] for r in rows))
        out.append(s)
    comps = []
    models = sorted({r["model"] for r in runs})
    arms = sorted({r["arm"] for r in runs})
    for m in models:
        for i, a in enumerate(arms):
            for b in arms[i + 1:]:
                ra = [r for r in runs if r["model"] == m and r["arm"] == a]
                rb = [r for r in runs if r["model"] == m and r["arm"] == b]
                if ra and rb:
                    comps.append({"model": m, "a": a, "b": b,
                                  "ac1": paired(ra, rb, "ac1"), "fault_ok": paired(ra, rb, "fault_ok"),
                                  "correct": paired(ra, rb, "correct"), "usd": paired(ra, rb, "usd"),
                                  "in_tok": paired(ra, rb, "in_tok"), "mcnemar_correct": mcnemar(ra, rb, "correct")})
    for a in arms:
        for i, m1 in enumerate(models):
            for m2 in models[i + 1:]:
                ra = [r for r in runs if r["model"] == m1 and r["arm"] == a]
                rb = [r for r in runs if r["model"] == m2 and r["arm"] == a]
                if ra and rb:
                    comps.append({"arm": a, "a": m1, "b": m2, "correct": paired(ra, rb, "correct"),
                                  "usd": paired(ra, rb, "usd"), "mcnemar_correct": mcnemar(ra, rb, "correct")})
    holm(comps)
    return out, comps


def holm(comps):
    """Holm-adjusted McNemar p-values, within each family (one model's arm pairs, or one arm's model pair)."""
    fams = {}
    for c in comps:
        fams.setdefault(c.get("model") or ("arm:" + c["arm"]), []).append(c)
    for fam in fams.values():
        order = sorted(fam, key=lambda c: c["mcnemar_correct"]["p"])
        m, run = len(order), 0.0
        for i, c in enumerate(order):
            run = max(run, min(1.0, (m - i) * c["mcnemar_correct"]["p"]))
            c["mcnemar_correct"]["p_holm"] = run


# ---------------- rendering ----------------
def pct(x):
    return "—" if x is None or x != x else "%.0f%%" % (100 * x)


def ci_txt(m):
    lo, hi = m["ci"]
    return "%s (%s–%s)" % (pct(m["mean"]), pct(lo), pct(hi))


def markdown(summary, comps, baselines):
    L = ["| Model | Interface | Cases × trials | Service AC@1 | AC@3 | Fault type | Both right | pass^k | Input tok (med) | $/run (med) | $/correct | Calls (med) | Tool errors |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        c = s["consistency"]
        L.append("| %s | %s | %d × %s | %s | %s | %s | %s | %s | %.0f | %.3f | %.3f | %.0f | %s |" % (
            s["model"], s["arm"], s["cases"], s["trials"], ci_txt(s["ac1"]), pct(s["ac3"]["mean"]),
            ci_txt(s["fault_ok"]), ci_txt(s["correct"]), pct(c["pass_hat_k"]) if c else "—",
            s["in_tok"]["median"], s["usd"]["median"], s["usd_per_correct"], s["calls"]["median"],
            pct(s["tool_err_rate"])))
    if baselines:
        L += ["", "No-LLM reference on the same cases: " + "; ".join(
            "%s AC@1 %s, Avg@5 %s" % (k, pct(v["1"]), pct(v["avg5"])) for k, v in baselines.items())]
    L += ["", "Paired differences (per case, mean of trials; 95% paired bootstrap):", ""]
    for c in comps:
        who = ("%s: %s − %s" % (c["model"], c["a"], c["b"])) if "model" in c else ("%s: %s − %s" % (c["arm"], c["a"], c["b"]))
        parts = []
        for k in ("correct", "fault_ok", "usd"):
            v = c.get(k)
            if v:
                fmt = (lambda x: "%+.3f" % x) if k == "usd" else (lambda x: "%+.0fpt" % (100 * x))
                parts.append("%s %s (%s, %s)" % (k, fmt(v["diff"]), fmt(v["ci"][0]), fmt(v["ci"][1])))
        mc = c["mcnemar_correct"]
        parts.append("McNemar p=%.2f, Holm %.2f (%d vs %d cases)" % (mc["p"], mc.get("p_holm", mc["p"]), mc["only_a"], mc["only_b"]))
        L.append("- " + who + ": " + "; ".join(parts))
    return "\n".join(L)


COLORS = ["#a33a0b", "#2f5d8a", "#3f6b3a", "#8a5a9e", "#c08a1e", "#5a5a5a"]


def svg_dots(summary, key, title, baselines=None):
    rows = [s for s in summary]
    h = 34 + 26 * len(rows) + 30
    w, x0, x1 = 720, 230, 690
    X = lambda v: x0 + (x1 - x0) * v
    out = ['<svg class="vis" viewBox="0 0 %d %d" role="img" aria-label="%s">' % (w, h, html.escape(title)),
           '<text x="0" y="16" class="lab">%s</text>' % html.escape(title)]
    for t in range(0, 11, 2):
        x = X(t / 10)
        out.append('<line x1="%.1f" x2="%.1f" y1="26" y2="%d" class="g"/><text x="%.1f" y="%d" class="ax" text-anchor="middle">%d%%</text>'
                   % (x, x, h - 22, x, h - 6, t * 10))
    if baselines:
        for i, (name, v) in enumerate(baselines.items()):
            x = X(v)
            out.append('<line x1="%.1f" x2="%.1f" y1="26" y2="%d" class="ref"/><text x="%.1f" y="%d" class="reft">%s</text>'
                       % (x, x, h - 22, x + 3, 36 + 11 * i, html.escape(name)))
    models = sorted({s["model"] for s in rows})
    for i, s in enumerate(rows):
        y = 44 + 26 * i
        m = s[key]
        col = COLORS[models.index(s["model"]) % len(COLORS)]
        lo, hi = m["ci"]
        out.append('<text x="0" y="%d" class="ax" dominant-baseline="middle">%s · %s (n=%d)</text>' % (
            y, html.escape(s["model"]), html.escape(s["arm"]), s["cases"]))
        if lo == lo:
            out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s" class="win"/>' % (X(lo), X(hi), y, y, col))
        out.append('<circle cx="%.1f" cy="%d" r="5" fill="%s" class="dot"/><text x="%.1f" y="%d" class="ax" dominant-baseline="middle">%s</text>'
                   % (X(m["mean"]), y, col, min(X(m["mean"]) + 9, x1 - 30) if lo != lo else min(X(hi) + 8, x1 - 30), y, pct(m["mean"])))
    out.append("</svg>")
    return "".join(out)


def svg_cost(summary):
    pts = [(s["usd"]["median"], s["correct"]["mean"], s) for s in summary]
    if not pts:
        return ""
    w, h, x0, x1, y0, y1 = 720, 300, 60, 690, 260, 30
    xmax = max(p[0] for p in pts) * 1.15 or 1
    X = lambda v: x0 + (x1 - x0) * v / xmax
    Y = lambda v: y0 - (y0 - y1) * v
    models = sorted({s["model"] for s in summary})
    out = ['<svg class="vis" viewBox="0 0 %d %d" role="img" aria-label="Cost vs accuracy">' % (w, h),
           '<text x="0" y="16" class="lab">Cost per run vs fully-correct rate (up-left is better)</text>']
    for t in range(0, 11, 2):
        out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="g"/><text x="%d" y="%.1f" class="ax" text-anchor="end">%d%%</text>'
                   % (x0, x1, Y(t / 10), Y(t / 10), x0 - 6, Y(t / 10) + 4, t * 10))
    for i in range(6):
        v = xmax * i / 5
        out.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">$%.3f</text>' % (X(v), h - 8, v))
    for usd, acc, s in pts:
        col = COLORS[models.index(s["model"]) % len(COLORS)]
        out.append('<circle cx="%.1f" cy="%.1f" r="6" fill="%s"/><text x="%.1f" y="%.1f" class="ax">%s · %s</text>'
                   % (X(usd), Y(acc), col, X(usd) + 9, Y(acc) + 4, html.escape(s["model"]), html.escape(s["arm"])))
    out.append("</svg>")
    return "".join(out)


def svg_confusion(s):
    m = s["confusion"]
    truths = sorted(m)
    preds = sorted({p for row in m.values() for p in row} | set(truths))
    cw, chh, x0, y0 = 54, 22, 90, 46
    w, h = x0 + cw * len(preds) + 10, y0 + chh * len(truths) + 10
    out = ['<svg class="vis" viewBox="0 0 %d %d" style="max-width:%dpx" role="img" aria-label="fault confusion">' % (w, h, w),
           '<text x="0" y="14" class="lab">%s · %s</text><text x="%d" y="34" class="ax">answered →</text>'
           % (html.escape(s["model"]), html.escape(s["arm"]), x0)]
    for j, p in enumerate(preds):
        out.append('<text x="%d" y="%d" class="ax" text-anchor="middle">%s</text>' % (x0 + cw * j + cw // 2, y0 - 4, html.escape(p)))
    for i, t in enumerate(truths):
        tot = sum(m[t].values())
        out.append('<text x="%d" y="%d" class="ax" text-anchor="end">%s</text>' % (x0 - 6, y0 + chh * i + 15, html.escape(t)))
        for j, p in enumerate(preds):
            v = m[t].get(p, 0)
            a = v / tot if tot else 0
            fill = ("rgba(63,107,58,%.2f)" if p == t else "rgba(156,58,46,%.2f)") % (0.08 + 0.8 * a)
            out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/><text x="%d" y="%d" class="ax" text-anchor="middle">%s</text>'
                       % (x0 + cw * j, y0 + chh * i, cw - 2, chh - 2, fill if v else "transparent",
                          x0 + cw * j + cw // 2, y0 + chh * i + 15, v or ""))
    out.append("</svg>")
    return "".join(out)


def render_html(summary, comps, baselines, sources):
    md = markdown(summary, comps, baselines)
    bl = {k: v["1"] for k, v in (baselines or {}).items()}
    table_rows = []
    for s in summary:
        c = s["consistency"]
        table_rows.append("<tr>" + "".join("<td%s>%s</td>" % (' class="num"' if i > 1 else "", html.escape(str(x))) for i, x in enumerate([
            s["model"], s["arm"], "%d × %s" % (s["cases"], s["trials"]), ci_txt(s["ac1"]), pct(s["ac3"]["mean"]),
            ci_txt(s["fault_ok"]), ci_txt(s["correct"]), pct(c["pass_hat_k"]) if c else "—",
            "%.0f" % s["in_tok"]["median"], pct(s["cache_share"]["median"]), "$%.3f" % s["usd"]["median"],
            "$%.3f" % s["usd_per_correct"], "%.0f" % s["calls"]["median"], "%.0f s" % s["model_s"]["median"],
            pct(s["tool_err_rate"]), pct(s["tool_empty_rate"])])) + "</tr>")
    comp_items = "".join("<li>%s</li>" % html.escape(x[2:]) for x in md.split("\n") if x.startswith("- "))
    heads = ["Model", "Interface", "Cases × trials", "Service AC@1", "AC@3", "Fault type", "Both right", "pass^k",
             "Input tok", "Cached", "$/run", "$/correct", "Calls", "Model time", "Tool err", "Empty"]
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentLab results</title><style>%s</style></head><body>
<header class="wrap"><div class="eyebrow">DiskScope · Phase 3 · AgentLab</div><h1>Which interface helps an agent diagnose an incident?</h1>
<p class="lede">Same incidents, different tool interfaces, deterministic grading. Medians for cost; means for accuracy with 95%% intervals that resample incidents (trials of one incident are not independent).</p></header>
<section class="wrap"><div class="panel">%s</div></section>
<section class="wrap"><div class="panel">%s</div></section>
<section class="wrap"><div class="panel">%s</div></section>
<section class="wrap"><h2>All numbers</h2><div class="tablewrap"><table><tr>%s</tr>%s</table></div>
<p class="foot">Medians for tokens, cost, calls and time. Tool err = tool results that were errors; Empty = header-only or empty results.</p></section>
<section class="wrap"><h2>Paired differences</h2><ul class="notes" style="display:block">%s</ul>
<p class="foot">Per case, trials averaged; 95%% paired bootstrap over cases; exact McNemar on per-case majority correctness. With about 30 cases, differences under ~15 points are within noise.</p></section>
<section class="wrap"><h2>Which fault types get confused</h2><div class="panel" style="display:flex;flex-wrap:wrap;gap:24px">%s</div></section>
<footer class="wrap">Generated by <code>agentlab/report.py</code> from: %s</footer></body></html>""" % (
        STYLE, svg_dots(summary, "ac1", "Root-cause service ranked first (AC@1), 95% CI", bl),
        svg_dots(summary, "fault_ok", "Fault type correct, 95% CI"), svg_cost(summary),
        "".join("<th%s>%s</th>" % (' class="num"' if i > 1 else "", h) for i, h in enumerate(heads)), "".join(table_rows),
        comp_items, "".join(svg_confusion(s) for s in summary),
        html.escape(", ".join(os.path.basename(p) for p in sources)))


STYLE = r""":root{--plane:#f6f3ec;--surface:#fffdf8;--ink:#161513;--ink-2:#4b4842;--muted:#8b867c;--grid:#ebe6dc;--border:rgba(22,21,19,.09);--wash:#f1ede4}
@media (prefers-color-scheme:dark){:root{--plane:#161513;--surface:#1e1d1b;--ink:#f1ede4;--ink-2:#cfc9bd;--muted:#8b867c;--grid:#2c2a27;--border:rgba(255,255,255,.1);--wash:#262522}}
body{margin:0;background:var(--plane);color:var(--ink);font:15px/1.6 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px}header{padding:40px 0 8px}.eyebrow{font-size:11.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h1{font-size:clamp(26px,4vw,40px);line-height:1.1;margin:12px 0}h2{font-size:20px;margin:0 0 10px}.lede{color:var(--ink-2);max-width:760px}
section{padding:24px 0 4px}.panel{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 20px;overflow-x:auto}
.tablewrap{overflow:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}th{text-align:left;font-size:11px;color:var(--muted);font-weight:500;padding:10px 12px;border-bottom:1px solid var(--grid)}
td{padding:7px 12px;border-bottom:1px solid var(--grid);white-space:nowrap;color:var(--ink-2)}td.num,th.num{text-align:right}
.notes li{margin:0 0 6px;font-size:13.5px;color:var(--ink-2)}.foot{font-size:12.5px;color:var(--muted)}footer{color:var(--muted);font-size:12.5px;padding:32px 0 48px}
code{font:12.5px ui-monospace,Menlo,monospace;background:var(--wash);padding:1px 5px;border-radius:4px}
.vis{width:100%;height:auto;display:block}.vis .g{stroke:var(--grid)}.vis .ax{fill:var(--muted);font-size:11px}.vis .lab{fill:var(--ink);font-size:13px;font-weight:600}
.vis .ref{stroke:var(--ink-2);stroke-dasharray:3 3}.vis .reft{fill:var(--ink-2);font-size:10.5px}.vis .win{stroke-width:3;opacity:.55}"""


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("runs", nargs="+")
    p.add_argument("--baselines", help="rcaeval_baselines output (no-LLM reference lines)")
    p.add_argument("--html")
    p.add_argument("--md")
    p.add_argument("--json")
    a = p.parse_args(argv)
    runs = load(a.runs)
    summary, comps = summarize(runs)
    baselines = None
    if a.baselines:
        bs = json.load(open(a.baselines))["summary"]
        baselines = {"digest as ranking, no LLM, no time": bs["digest_rank"]}
        for k, label in (("nsigma", "N-Sigma (given time)"), ("baro", "BARO (given time)")):
            if k in bs:
                baselines[label] = bs[k]
        if "chance_candidates" in bs:
            baselines["chance (%d candidates)" % bs["chance_candidates"]["n_candidates"]] = bs["chance_candidates"]
    md = markdown(summary, comps, baselines)
    if EXCLUDED:
        md += "\n\nExcluded (infra failure or served by another model): %d runs: %s" % (
            len(EXCLUDED), "; ".join("%s/%s/%s: %s" % (x["model"], x["arm"], x["case"], x["error"]) for x in EXCLUDED[:10]))
    print(md)
    if a.md:
        open(a.md, "w").write(md + "\n")
    if a.json:
        json.dump({"summary": summary, "comparisons": comps}, open(a.json, "w"), indent=1, default=str)
    if a.html:
        os.makedirs(os.path.dirname(a.html) or ".", exist_ok=True)
        open(a.html, "w").write(render_html(summary, comps, baselines, a.runs))
        print("wrote", a.html)


if __name__ == "__main__":
    main()

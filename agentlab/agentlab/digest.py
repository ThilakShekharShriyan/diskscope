"""The agent digest: a compact, ranked text view of a telemetry window.

FROZEN: this file is fixed in git before any fault scenario is written and
must stay generic. It may only use statistics (change points, robust scores, template
counts, attribute shifts). tests/test_digest_freeze.py fails if it mentions
fault-specific words. Changing it after the freeze invalidates results.

digest(data)        -> (text, index); index maps evidence ids to drill-down text
Stdlib only.
"""
import re
import statistics

TOP_METRICS = 8
TOP_LOGS = 6
MIN_PRE_S = 120       # metric history needed before a change point
MIN_POST_S = 60       # metric data needed after a change point
# Window length and metric step are read from the data (60 min at 10 s for the
# generated scenarios). Log-rate change points need window/5 minutes before and
# window/10 after; a template is "new" if first seen after window/6.

_MASKS = [(re.compile(r"\b[0-9a-f]{6,}\b"), "<h>"), (re.compile(r"\d+(\.\d+)?"), "<n>")]


def template(msg):
    for rx, rep in _MASKS:
        msg = rx.sub(rep, msg)
    return msg


def _changepoint(v, min_pre, min_post):
    """Best single mean shift. Returns (score, k, before_mean, after_mean) or None."""
    n = len(v)
    if n < min_pre + min_post:
        return None
    pre = [0.0]
    for x in v:
        pre.append(pre[-1] + x)
    best = None
    for k in range(min_pre, n - min_post + 1):
        a, b = pre[k] / k, (pre[n] - pre[k]) / (n - k)
        gain = abs(b - a) * ((k * (n - k)) / n) ** 0.5
        if best is None or gain > best[0]:
            best = (gain, k, a, b)
    _, k, a, b = best
    before = v[:k]
    spread = statistics.pstdev(before) if len(before) > 1 else 0.0
    floor = 0.05 * abs(a) + 0.5
    score = abs(b - a) / max(spread, floor)
    return score, k, a, b


def _fmt_t(s):
    return "+%dm%02ds" % (s // 60, s % 60)


def _num(x):
    return ("%.0f" % x) if abs(x) >= 100 else ("%.1f" % x)


def digest(data):
    spans, logs, metrics = data["spans"], data["logs"], data["metrics"]
    steps = sorted({r["ts_ms"] for r in metrics})
    t0 = steps[0]
    gaps = sorted(b - a for a, b in zip(steps, steps[1:]))
    step_s = max(1, round(gaps[len(gaps) // 2] / 1000)) if gaps else 10
    mins = max(1, -(-(steps[-1] + step_s * 1000 - t0) // 60000))
    m_pre, m_post = -(-MIN_PRE_S // step_s), -(-MIN_POST_S // step_s)
    l_pre, l_post = max(3, round(mins / 5)), max(3, round(mins / 10))
    half_m = mins // 2
    index = {}
    out = ["DIGEST: %d min window, %d s steps; times are offsets from window start." % (mins, step_s),
           "Most anomalous first. Call drill(<id>) for raw evidence behind any line."]

    # metric change points
    series = {}
    for r in metrics:
        series.setdefault(r["series"], {})[r["ts_ms"]] = r["value"]
    found = []
    for name, pts in series.items():
        tss = steps if name.endswith((".requests", ".errors")) else [ts for ts in steps if ts in pts]
        v = [pts.get(ts, 0.0) for ts in tss]
        cp = _changepoint(v, m_pre, m_post)
        if cp and cp[0] >= 4:
            found.append((cp[0], name, cp, v, tss))
    found.sort(key=lambda x: -x[0])
    out.append("\nMETRICS (change points)")
    if not found:
        out.append("  none above threshold")
    for i, (score, name, (_, k, a, b), v, tss) in enumerate(found[:TOP_METRICS], 1):
        mid = "M%d" % i
        onset = int((tss[k] - t0) / 1000)
        ratio = (b / a) if a else float("inf")
        rtxt = ("x%.1f" % ratio) if a and ratio < 1000 else "from ~0"
        out.append("  %s %s  %s -> %s (%s) from %s  score %.0f" % (mid, name, _num(a), _num(b), rtxt, _fmt_t(onset), score))
        index[mid] = _drill_metric(name, v, tss, k, spans, t0, mins)

    # log templates
    groups = {}
    for r in logs:
        key = (r["component"], r["level"], template(r["msg"]))
        groups.setdefault(key, []).append(r)
    scored, rare = [], []
    for key, rows in groups.items():
        counts = [0] * mins
        for r in rows:
            counts[min(mins - 1, int((r["ts_ms"] - t0) / 60000))] += 1
        if len(rows) <= 3:
            rare.extend(rows)
            continue
        first = int((rows[0]["ts_ms"] - t0) / 1000)
        new = first >= mins * 10 and sum(counts[: first // 60]) == 0
        cp = _changepoint(counts, l_pre, l_post)
        score = cp[0] if cp else 0
        if new:
            score = max(score, 10 + len(rows) ** 0.5)
        if score >= 4 or (key[1] in ("ERROR", "WARN") and new):
            scored.append((score, key, rows, counts, new, first))
    scored.sort(key=lambda x: -x[0])
    out.append("\nLOGS (templates whose rate changed; <n>=number, <h>=hex id)")
    if not scored:
        out.append("  none above threshold")
    for i, (score, (comp, lvl, tpl), rows, counts, new, first) in enumerate(scored[:TOP_LOGS], 1):
        lid = "L%d" % i
        tag = "NEW since %s" % _fmt_t(first) if new else "rate %s -> %s /min" % (
            _num(sum(counts[:half_m]) / half_m), _num(sum(counts[half_m:]) / (mins - half_m)))
        out.append('  %s %s %s "%s"  n=%d  %s' % (lid, comp, lvl, tpl[:90], len(rows), tag))
        index[lid] = "\n".join(_line(r, t0) for r in rows[:5]) + ("\n... %d more" % (len(rows) - 5) if len(rows) > 5 else "")

    # rare events
    rare.sort(key=lambda r: r["ts_ms"])
    out.append("\nRARE EVENTS (log templates seen <= 3 times)")
    if not rare:
        out.append("  none")
    for i, r in enumerate(rare[:8], 1):
        eid = "E%d" % i
        out.append("  %s %s %s %s \"%s\"" % (eid, _fmt_t(int((r["ts_ms"] - t0) / 1000)), r["component"], r["level"], r["msg"][:100]))
        index[eid] = _line(r, t0)

    # overall load and errors
    gw = [s for s in spans if s["parent_id"] is None]
    half = t0 + half_m * 60000
    a = [s for s in gw if s["start_ms"] < half]
    b = [s for s in gw if s["start_ms"] >= half]
    er = lambda xs: 100.0 * sum(s["status"] == "ERROR" for s in xs) / max(1, len(xs))
    out.append("\nTOTALS  calls/min %s -> %s; error rate %.1f%% -> %.1f%% (first vs second half)" %
               (_num(len(a) / half_m), _num(len(b) / (mins - half_m)), er(a), er(b)))
    return "\n".join(out), index


def _line(r, t0):
    attrs = " ".join("%s=%s" % kv for kv in sorted(r.get("attrs", {}).items()))
    return "%s %s %s %s trace=%s %s" % (_fmt_t(int((r["ts_ms"] - t0) / 1000)), r["component"], r["level"],
                                         r["msg"], r.get("trace_id"), attrs)


def _drill_metric(name, v, tss, k, spans, t0, mins=60):
    lines = ["%s per minute (mean of points in each minute; - = no data), change point at %s:" %
             (name, _fmt_t(int((tss[k] - t0) / 1000)))]
    buckets = {}
    for ts, x in zip(tss, v):
        buckets.setdefault(int((ts - t0) / 60000), []).append(x)
    lines.append("  " + " ".join(_num(sum(buckets[m]) / len(buckets[m])) if m in buckets else "-" for m in range(mins)))
    parts = name.split(".")
    if len(parts) >= 3:
        comp, span_name = parts[0], ".".join(parts[1:-1])
        cut = tss[k]
        sel = [s for s in spans if s["component"] == comp and s["name"].replace(" ", "_").replace("/", "_").strip("_") == span_name]
        before = [s for s in sel if s["start_ms"] < cut]
        after = [s for s in sel if s["start_ms"] >= cut]
        if before and after:
            lines.append("Attribute values on %s spans, share before -> after the change:" % span_name)
            keys = sorted({kk for s in sel for kk in s["attrs"]})
            shifts = []
            for kk in keys:
                vals = {str(s["attrs"].get(kk)) for s in sel}
                if len(vals) > 12:
                    continue
                for val in vals:
                    pb = sum(str(s["attrs"].get(kk)) == val for s in before) / len(before)
                    pa = sum(str(s["attrs"].get(kk)) == val for s in after) / len(after)
                    if abs(pa - pb) >= 0.1:
                        shifts.append((abs(pa - pb), "  %s=%s  %.0f%% -> %.0f%%" % (kk, val, 100 * pb, 100 * pa)))
            shifts.sort(key=lambda x: -x[0])
            lines.extend(s for _, s in shifts[:6]) if shifts else lines.append("  no attribute shifted by >= 10 points")
            slow = sorted(after, key=lambda s: -s["dur_ms"])[:3]
            lines.append("Slowest spans after the change: " + ", ".join("trace=%s %.0fms" % (s["trace_id"], s["dur_ms"]) for s in slow))
    return "\n".join(lines)

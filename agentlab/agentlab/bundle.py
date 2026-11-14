"""Write one generated dataset into every on-disk form the interface arms read.

files arm   : logs.jsonl, spans.jsonl, metrics.csv, README.md
sql arm     : telemetry.db (SQLite; DuckDB/Parquet are not in the Archil exec image)
grafana arms: metrics.om (OpenMetrics, for promtool backfill), loki.json (Loki push body)
Stdlib only.
"""
import csv
import json
import os
import sqlite3

SCHEMA_README = """# Telemetry for the last 60 minutes

All timestamps are Unix epoch milliseconds (UTC).

- logs.jsonl   one JSON object per line: ts_ms, level, component, msg, trace_id, attrs
- spans.jsonl  one span per line: trace_id, span_id, parent_id, name, component,
               start_ms, dur_ms, status (OK|ERROR), attrs
- metrics.csv  ts_ms,series,value  (10 s steps; series = <component>.<span>.<stat> or a gauge)
- telemetry.db SQLite with tables logs, spans, metrics (same columns; attrs as JSON text)

Each tool call is one trace: gateway -> scheduler.dispatch -> exec-pool.acquire_vm ->
exec-pool.boot -> <work span> -> commit.flush (writing tasks only).
"""


def write_bundle(data, outdir):
    os.makedirs(outdir, exist_ok=True)
    p = lambda n: os.path.join(outdir, n)
    with open(p("logs.jsonl"), "w") as f:
        for r in data["logs"]:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    with open(p("spans.jsonl"), "w") as f:
        for r in data["spans"]:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    with open(p("metrics.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_ms", "series", "value"])
        for r in data["metrics"]:
            w.writerow([r["ts_ms"], r["series"], r["value"]])
    with open(p("README.md"), "w") as f:
        f.write(SCHEMA_README)
    _sqlite(data, p("telemetry.db"))
    _openmetrics(data, p("metrics.om"))
    _loki(data, p("loki.json"))
    return sorted(os.listdir(outdir))


def _sqlite(data, path):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE logs(ts_ms INTEGER, level TEXT, component TEXT, msg TEXT, trace_id TEXT, attrs TEXT);
      CREATE TABLE spans(trace_id TEXT, span_id TEXT, parent_id TEXT, name TEXT, component TEXT,
                         start_ms REAL, dur_ms REAL, status TEXT, attrs TEXT);
      CREATE TABLE metrics(ts_ms INTEGER, series TEXT, value REAL);""")
    db.executemany("INSERT INTO logs VALUES (?,?,?,?,?,?)",
                   [(r["ts_ms"], r["level"], r["component"], r["msg"], r["trace_id"], json.dumps(r["attrs"]))
                    for r in data["logs"]])
    db.executemany("INSERT INTO spans VALUES (?,?,?,?,?,?,?,?,?)",
                   [(r["trace_id"], r["span_id"], r["parent_id"], r["name"], r["component"], r["start_ms"],
                     r["dur_ms"], r["status"], json.dumps(r["attrs"])) for r in data["spans"]])
    db.executemany("INSERT INTO metrics VALUES (?,?,?)",
                   [(r["ts_ms"], r["series"], r["value"]) for r in data["metrics"]])
    db.executescript("""CREATE INDEX logs_ts ON logs(ts_ms); CREATE INDEX spans_trace ON spans(trace_id);
                        CREATE INDEX metrics_series ON metrics(series, ts_ms);""")
    db.commit()
    db.close()


def _prom_name(series):
    out = "".join(c if c.isalnum() else "_" for c in series)
    return "agentlab_" + out


def _openmetrics(data, path):
    by = {}
    for r in data["metrics"]:
        by.setdefault(r["series"], []).append(r)
    with open(path, "w") as f:
        for s in sorted(by):
            n = _prom_name(s)
            comp = s.split(".")[0]
            f.write("# TYPE %s gauge\n" % n)
            for r in by[s]:
                f.write('%s{component="%s"} %s %.3f\n' % (n, comp, r["value"], r["ts_ms"] / 1000))
        f.write("# EOF\n")


def _loki(data, path):
    streams = {}
    for r in data["logs"]:
        key = (r["component"], r["level"])
        streams.setdefault(key, []).append([str(r["ts_ms"] * 1_000_000),
                                            json.dumps({"msg": r["msg"], "trace_id": r["trace_id"], **r["attrs"]})])
    body = {"streams": [{"stream": {"job": "agentlab", "component": c, "level": lv}, "values": v}
                        for (c, lv), v in sorted(streams.items())]}
    with open(path, "w") as f:
        json.dump(body, f)

/* DiskScope view-models: raw RunReports -> chart inputs + headline numbers.
 * Pure (no DOM), shared by the dashboard and scripts/readme-charts.mjs so both always agree. */
(function (root) {
  'use strict';
  const C = root.DiskScopeCharts || (typeof require === 'function' ? require('./charts.js') : null);
  const { fmtMs, fmtNum, fmtX, median, medianCI, ratioCI, taskRank } = C;
  const MiB = 1048576;

  function prepare(reports) {
    const valid = reports.filter(r => r && r.schema_version === 1 && Array.isArray(r.observations));
    const rows = C.flatten(valid).map(r => ({ ...r, run_parallel: (valid[r.run_index].environment || {}).parallel || 1 }));
    const slots = C.seriesMap([...new Set(rows.map(r => r.backend))]);
    return { reports: valid, rows, slots };
  }
  const laneName = r => r.run_parallel > 1 ? `${r.backend} (par ${r.run_parallel})` : r.backend;
  const laneSort = slots => (a, b) => (slots.get(a.backend) - slots.get(b.backend)) || a.name.localeCompare(b.name);
  const num = v => typeof v === 'number' && Number.isFinite(v);
  const callTip = (r, v, fmt) => [fmt(v), `${r.task} · ${laneName(r)} · trial ${r.trial + 1}`,
    num(r.remote_queue_ms) ? `queue ${fmtMs(r.remote_queue_ms)} · execute ${fmtMs(r.remote_execute_ms)}` : 'local process',
    num(r.work_ms) ? `measured work ${fmtMs(r.work_ms)}` : ''].filter(Boolean);
  function byTask(rows) {
    const m = new Map();
    for (const r of rows) { if (!m.has(r.task)) m.set(r.task, []); m.get(r.task).push(r); }
    return [...m].sort((a, b) => taskRank(a[0]) - taskRank(b[0]) || a[0].localeCompare(b[0]));
  }
  function lanesOf(rows, slots, value, fmt) {
    const m = new Map();
    for (const r of rows) {
      const v = value(r); if (!(v > 0)) continue;
      const name = laneName(r);
      if (!m.has(name)) m.set(name, { name, backend: r.backend, slot: slots.get(r.backend), values: [] });
      m.get(name).values.push({ v, tip: callTip(r, v, fmt) });
    }
    return [...m.values()].sort(laneSort(slots));
  }
  const suite = rows => C.measured(rows).filter(r => r.task !== 'fanout-stat');

  function atlas({ rows, slots }, metric) {
    return byTask(suite(rows)).map(([task, rs]) => ({ label: task, sub: C.TASK_INFO[task], lanes: lanesOf(rs, slots, r => r[metric], fmtMs) }))
      .filter(g => g.lanes.length);
  }

  function anatomy({ rows }) {
    const remote = suite(rows).filter(r => num(r.remote_queue_ms) && num(r.remote_execute_ms));
    return byTask(remote).map(([task, rs]) => {
      const q = median(rs.map(r => r.remote_queue_ms));
      const s = median(rs.map(r => Math.max(0, r.remote_execute_ms - (r.work_ms || 0))));
      const w = median(rs.map(r => r.work_ms || 0));
      const o = median(rs.map(r => Math.max(0, r.client_wall_ms - (num(r.remote_total_ms) ? r.remote_total_ms : r.remote_queue_ms + r.remote_execute_ms))));
      return { label: task, n: rs.length, queue: q, startup: s, work: w, other: o, total: q + s + w + o };
    });
  }

  function slowdown({ rows }) {
    const out = [];
    for (const [task, rs] of byTask(suite(rows))) {
      const a = rs.filter(r => r.backend === 'archil' && r.work_ms > 0).map(r => r.work_ms);
      const l = rs.filter(r => r.backend === 'local' && r.work_ms > 0).map(r => r.work_ms);
      if (!a.length || !l.length) continue;
      const { point, ci } = ratioCI(a, l, 'ratio:' + task);
      out.push({ label: task, sub: C.TASK_INFO[task], points: [{ name: 'archil ÷ local', slot: 1, v: point, ci,
        tip: [fmtX(point) + (point >= 1 ? ' slower on Archil' : ' — Archil faster'), `${task}: Archil ${fmtMs(median(a))} vs local ${fmtMs(median(l))} (median work)`,
          ci ? `95% CI ${fmtX(ci[0])} – ${fmtX(ci[1])}` : 'too few samples for an interval', `n = ${a.length} Archil, ${l.length} local`] }] });
    }
    return out;
  }

  function ecdf({ rows, slots }, task) {
    const rs = suite(rows).filter(r => task === 'all' || r.task === task);
    const m = new Map();
    for (const r of rs) {
      if (!num(r.client_wall_ms)) continue;
      if (!m.has(r.backend)) m.set(r.backend, { name: r.backend, slot: slots.get(r.backend), values: [] });
      m.get(r.backend).values.push(r.client_wall_ms);
    }
    return [...m.values()].sort((a, b) => a.slot - b.slot);
  }

  function fanout({ rows, slots }) {
    const rs = C.measured(rows).filter(r => r.task === 'fanout-stat');
    const levels = [...new Set(rs.map(r => r.fanout))].sort((a, b) => a - b);
    return levels.map(f => ({ label: `${f} at once`, sub: f === 1 ? 'one microVM' : `${f} concurrent microVMs`,
      lanes: lanesOf(rs.filter(r => r.fanout === f).map(r => ({ ...r, run_parallel: 1 })), slots, r => r.client_wall_ms, fmtMs) }));
  }

  function timeline({ reports, rows }) {
    return reports.map((rep, ri) => {
      const rs = rows.filter(r => r.run_index === ri && r.ok && num(r.client_wall_ms));
      if (rep.backend !== 'archil' || !rs.length) return null;
      rs.sort((a, b) => (num(a.started_ms) && num(b.started_ms) ? a.started_ms - b.started_ms : a.seq - b.seq));
      const p = (rep.environment || {}).parallel || 1;
      const d = new Date(rep.timestamp_unix_secs * 1000);
      return { label: `${d.toISOString().slice(5, 16).replace('T', ' ')} UTC · ${p > 1 ? p + ' parallel' : 'sequential'}`,
        points: rs.map(r => ({ v: r.client_wall_ms, warm: r.task === 'image-warmup', tip: [fmtMs(r.client_wall_ms), `${r.task}${r.fanout > 1 ? ' ×' + r.fanout : ''} · call ${r.seq + 1}`, `queue ${fmtMs(r.remote_queue_ms)} · execute ${fmtMs(r.remote_execute_ms)}`] })) };
    }).filter(Boolean);
  }

  function throughput({ rows, slots }) {
    const tasks = ['seq-write', 'seq-read', 'hash-64m'];
    const fmt = v => fmtNum(v, 'MiB/s');
    return byTask(suite(rows).filter(r => tasks.includes(r.task) && r.work_ms > 0 && r.bytes > 0)).map(([task, rs]) => {
      const m = new Map();
      for (const r of rs) { if (!m.has(r.backend)) m.set(r.backend, []); m.get(r.backend).push(r.bytes / MiB / (r.work_ms / 1000)); }
      return { label: task, sub: C.TASK_INFO[task], points: [...m].sort((a, b) => slots.get(a[0]) - slots.get(b[0])).map(([b, vals]) => {
        const v = median(vals), ci = medianCI(vals);
        return { name: b, slot: slots.get(b), v, ci, tip: [fmt(v) + ' median', `${task} · ${b}`, ci ? `${(ci.level * 100).toFixed(1)}% CI ${fmt(ci[0])} – ${fmt(ci[1])}` : 'n < 5: no interval', `n=${vals.length}`] };
      }) };
    });
  }

  /** Headline numbers for hero tiles, verdict sentence and README table. */
  function headline(data) {
    const { rows, reports } = data;
    const ok = rows.filter(r => r.task !== 'image-warmup');
    const remote = suite(rows).filter(r => r.backend === 'archil');
    const pick = (b, t, k) => suite(rows).filter(r => r.backend === b && r.task === t && num(r[k])).map(r => r[k]);
    const noop = pick('archil', 'noop', 'client_wall_ms');
    const ratios = Object.fromEntries(slowdown(data).map(g => [g.label, g.points[0]]));
    let par = null;
    reports.forEach((rep, ri) => {
      const p = (rep.environment || {}).parallel || 1;
      const rs = rows.filter(r => r.run_index === ri && num(r.started_ms) && num(r.client_wall_ms));
      if (rep.backend !== 'archil' || p < 2 || rs.length < 10) return;
      const span = Math.max(...rs.map(r => r.started_ms + r.client_wall_ms)) - Math.min(...rs.map(r => r.started_ms));
      const serial = rs.reduce((s, r) => s + r.client_wall_ms, 0);
      if (!par || rs.length > par.calls) par = { parallel: p, calls: rs.length, seconds: span / 1000, speedup: serial / span };
    });
    return {
      observations: ok.length, success: ok.length ? ok.filter(r => r.ok).length / ok.length : NaN,
      archilCalls: rows.filter(r => r.backend === 'archil').length,
      noop: noop.length ? { median: median(noop), ci: medianCI(noop), n: noop.length } : null,
      queue: remote.length ? median(remote.map(r => r.remote_queue_ms).filter(num)) : NaN,
      network: remote.length ? median(remote.map(r => r.client_wall_ms - (r.remote_total_ms || 0)).filter(num)) : NaN,
      fsync: ratios['fsync-4k'] || null, sqlite: ratios['sqlite-commit'] || null, cpu: ratios['hash-64m'] || null,
      sqliteCommitMs: { archil: median(pick('archil', 'sqlite-commit', 'work_ms')) / 25, local: median(pick('local', 'sqlite-commit', 'work_ms')) / 25 },
      parallel: par,
      regions: [...new Set(reports.map(r => (r.environment || {}).region).filter(Boolean))],
      firstTs: Math.min(...reports.map(r => r.timestamp_unix_secs)), lastTs: Math.max(...reports.map(r => r.timestamp_unix_secs)),
      cleanupIssues: rows.filter(r => r.cleanup_error).length,
    };
  }

  /** Markdown summary table (README): task, local median, Archil median, ratio [CI], n. */
  function markdownTable(data) {
    const lines = ['| Task | What it does | Local (median work) | Archil (median work) | Archil ÷ local [95% CI] | n (Archil / local) |', '|---|---|--:|--:|--:|--:|'];
    for (const [task, rs] of byTask(suite(data.rows))) {
      const a = rs.filter(r => r.backend === 'archil' && num(r.work_ms)).map(r => r.work_ms);
      const l = rs.filter(r => r.backend === 'local' && num(r.work_ms)).map(r => r.work_ms);
      let ratio = '—';
      if (a.length && l.length && median(l) > 0 && median(a) > 0) { const { point, ci } = ratioCI(a, l, 'ratio:' + task); ratio = `**${fmtX(point)}**` + (ci ? ` [${fmtX(ci[0])}–${fmtX(ci[1])}]` : ''); }
      lines.push(`| \`${task}\` | ${C.TASK_INFO[task] || ''} | ${l.length && median(l) > 0 ? fmtMs(median(l)) : '—'} | ${a.length && median(a) > 0 ? fmtMs(median(a)) : '—'} | ${ratio} | ${a.length} / ${l.length} |`);
    }
    return lines.join('\n');
  }

  root.DiskScopeViews = { prepare, laneName, atlas, anatomy, slowdown, ecdf, fanout, timeline, throughput, headline, markdownTable };
  if (typeof module === 'object' && module.exports) module.exports = root.DiskScopeViews;
})(typeof globalThis !== 'undefined' ? globalThis : this);

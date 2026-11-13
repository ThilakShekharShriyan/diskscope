/* DiskScope view-models: raw RunReports -> chart inputs + headline numbers.
 * Pure (no DOM), shared by the dashboard and scripts/readme-charts.mjs so both always agree.
 *
 * Runs are grouped by *variant* (configuration: backend + image/mode + guest version), never pooled
 * across variants. Headline numbers use each backend's primary variant (see PRIMARY). */
(function (root) {
  'use strict';
  const C = root.DiskScopeCharts || (typeof require === 'function' ? require('./charts.js') : null);
  const { fmtMs, fmtNum, fmtX, median, medianCI, ratioCI, taskRank } = C;
  const MiB = 1048576;
  const NOT_MEASURED = new Set(['image-warmup', 'sandbox-boot']);
  // Preferred variant per backend for headline numbers; falls back to the backend's most recent run.
  const PRIMARY = { archil: 'exec · default image', 'archil-sandbox': 'sandbox · persistent worker' };
  const V01 = 'exec · python:3.13-slim image · v0.1 guest';
  const VARIANT_ORDER = [V01, 'exec · python:3.13-slim image', 'exec · default image',
    'sandbox · exec per task', 'sandbox · persistent worker', 'local SSD · durable fsync'];

  const num = v => typeof v === 'number' && Number.isFinite(v);
  function variantOf(rep) {
    const env = rep.environment || {};
    if (env.variant) return env.variant;
    if (rep.backend === 'archil') return `exec · ${env.image || 'default'} image · v0.1 guest`; // reports before variants existed
    if (rep.backend === 'local') return 'local SSD · durable fsync';
    return rep.backend;
  }
  const variantRank = v => { const i = VARIANT_ORDER.indexOf(v); return i < 0 ? 99 : i; };
  const shortVariant = v => v.replace(/^exec · /, 'exec, ').replace(/^sandbox · /, 'sandbox, ').replace(' image', '')
    .replace('local SSD · durable fsync', 'local SSD');

  // Compact lane labels for charts (full names stay in tooltips, the table and the legend chips).
  const COMPACT = { [V01]: 'v0.1 exec, slim', 'exec · python:3.13-slim image': 'exec, slim image', 'exec · default image': 'exec, default',
    'sandbox · exec per task': 'sandbox exec()', 'sandbox · persistent worker': 'sandbox worker', 'local SSD · durable fsync': 'local SSD' };
  const compact = v => { const base = v.replace(/ \(par (\d+)\)$/, ''), par = /\(par (\d+)\)$/.exec(v); return (COMPACT[base] || shortVariant(base)) + (par ? ` ×${par[1]}` : ''); };

  function prepare(reports) {
    const valid = reports.filter(r => r && r.schema_version === 1 && Array.isArray(r.observations));
    const rows = C.flatten(valid).map(r => ({ ...r, variant: variantOf(valid[r.run_index]), run_parallel: (valid[r.run_index].environment || {}).parallel || 1 }));
    const slots = C.seriesMap([...new Set(rows.map(r => r.backend))]);
    const primary = new Map();
    for (const b of new Set(valid.map(r => r.backend))) {
      const runs = valid.filter(r => r.backend === b);
      primary.set(b, PRIMARY[b] && runs.some(r => variantOf(r) === PRIMARY[b]) ? PRIMARY[b]
        : variantOf(runs.reduce((a, r) => (r.timestamp_unix_secs > a.timestamp_unix_secs ? r : a))));
    }
    return { reports: valid, rows, slots, primary };
  }
  const isMeasured = r => r.ok && !NOT_MEASURED.has(r.task);
  const suite = rows => rows.filter(r => isMeasured(r) && r.task !== 'fanout-stat');
  const onlyPrimary = (data, rows) => rows.filter(r => data.primary.get(r.backend) === r.variant);
  const laneKey = r => r.run_parallel > 1 ? `${r.variant} (par ${r.run_parallel})` : r.variant;
  /** Network + API share of a remote call: client round-trip minus Archil's own total (or queue + execute). */
  const networkMs = r => r.client_wall_ms - (num(r.remote_total_ms) ? r.remote_total_ms : (r.remote_queue_ms || 0) + (r.remote_execute_ms || 0));
  const callTip = (r, v, fmt) => [fmt(v), `${r.task} · ${shortVariant(laneKey(r))} · trial ${r.trial + 1}`,
    num(r.remote_queue_ms) ? `queue ${fmtMs(r.remote_queue_ms)} · execute ${fmtMs(r.remote_execute_ms)}` : (r.backend === 'local' ? 'local process' : 'no server-side timing'),
    num(r.work_ms) ? `measured work ${fmtMs(r.work_ms)}` : ''].filter(Boolean);
  function byTask(rows) {
    const m = new Map();
    for (const r of rows) { if (!m.has(r.task)) m.set(r.task, []); m.get(r.task).push(r); }
    return [...m].sort((a, b) => taskRank(a[0]) - taskRank(b[0]) || a[0].localeCompare(b[0]));
  }
  /** Lanes of a strip plot: one per `key(r)`; label via `label(key)`; ordered by `rank(key)`. */
  function lanesOf(rows, slots, value, key = laneKey, label = compact, rank = k => variantRank(k.replace(/ \(par \d+\)$/, ''))) {
    const m = new Map();
    for (const r of rows) {
      const v = value(r); if (!(v > 0)) continue;
      const k = key(r);
      if (!m.has(k)) m.set(k, { key: k, name: label(k), slot: slots.get(r.backend), values: [] });
      m.get(k).values.push({ v, tip: callTip(r, v, fmtMs) });
    }
    return [...m.values()].sort((a, b) => rank(a.key) - rank(b.key) || a.name.localeCompare(b.name));
  }

  /** Every call, every variant: one group per task, one lane per variant. */
  function atlas(data, metric) {
    return byTask(suite(data.rows)).map(([task, rs]) => ({ label: task, sub: C.TASK_INFO[task], lanes: lanesOf(rs, data.slots, r => r[metric]) }))
      .filter(g => g.lanes.length);
  }

  /** The optimisation ladder: noop and fsync-4k end-to-end per variant, in VARIANT_ORDER. */
  function ladder(data) {
    const rs = suite(data.rows).filter(r => r.task === 'noop' || r.task === 'fsync-4k');
    return [...new Set(rs.map(r => r.variant))].sort((a, b) => variantRank(a) - variantRank(b)).map(v => ({
      label: shortVariant(v), sub: v === V01 ? 'first published run' : '',
      lanes: lanesOf(rs.filter(r => r.variant === v), data.slots, r => r.client_wall_ms, r => r.task, k => k, taskRank),
    }));
  }

  /** Median phase durations per task for the primary exec variant, plus noop for every other exec variant. */
  function anatomy(data) {
    const remote = suite(data.rows).filter(r => num(r.remote_queue_ms) && num(r.remote_execute_ms));
    const row = (label, rs) => {
      const q = median(rs.map(r => r.remote_queue_ms));
      const s = median(rs.map(r => Math.max(0, r.remote_execute_ms - (r.work_ms || 0))));
      const w = median(rs.map(r => r.work_ms || 0));
      const o = median(rs.map(r => Math.max(0, networkMs(r))));
      return { label, n: rs.length, queue: q, startup: s, work: w, other: o, total: q + s + w + o };
    };
    const rows = byTask(onlyPrimary(data, remote)).map(([task, rs]) => row(task, rs));
    const others = [...new Set(remote.map(r => r.variant))].filter(v => v !== data.primary.get('archil')).sort((a, b) => variantRank(a) - variantRank(b));
    for (const v of others) {
      const rs = remote.filter(r => r.variant === v && r.task === 'noop');
      if (rs.length) rows.push(row(`noop · ${compact(v)}`, rs));
    }
    return rows;
  }

  /** Ratio of median guest work vs local, for each remote primary variant. */
  function slowdown(data) {
    const prim = onlyPrimary(data, suite(data.rows));
    const remotes = [...data.primary].filter(([b]) => b !== 'local');
    const out = [];
    for (const [task, rs] of byTask(prim)) {
      const l = rs.filter(r => r.backend === 'local' && r.work_ms > 0).map(r => r.work_ms);
      if (!l.length) continue;
      const points = [];
      for (const [b, v] of remotes) {
        const a = rs.filter(r => r.backend === b && r.work_ms > 0).map(r => r.work_ms);
        if (!a.length) continue;
        const { point, ci } = ratioCI(a, l, `ratio:${task}:${b}`);
        points.push({ name: compact(v), backend: b, slot: data.slots.get(b), v: point, ci,
          tip: [`${fmtX(point)} ${point >= 1 ? 'slower than local' : 'faster than local'}`, `${task} · ${shortVariant(v)}: ${fmtMs(median(a))} vs local ${fmtMs(median(l))} (median work)`,
            ci ? `≈95% bootstrap CI ${fmtX(ci[0])} – ${fmtX(ci[1])}` : 'too few samples for an interval', `n = ${a.length} vs ${l.length}`] });
      }
      if (points.length) out.push({ label: task, sub: C.TASK_INFO[task], points });
    }
    return out;
  }

  function ecdf(data, task) {
    const rs = onlyPrimary(data, suite(data.rows)).filter(r => task === 'all' || r.task === task);
    const m = new Map();
    for (const r of rs) {
      if (!num(r.client_wall_ms)) continue;
      if (!m.has(r.variant)) m.set(r.variant, { name: shortVariant(r.variant), slot: data.slots.get(r.backend), values: [] });
      m.get(r.variant).values.push(r.client_wall_ms);
    }
    return [...m.entries()].sort((a, b) => variantRank(a[0]) - variantRank(b[0])).map(e => e[1]);
  }

  function fanout(data) {
    const rs = data.rows.filter(r => isMeasured(r) && r.task === 'fanout-stat' && data.primary.get(r.backend) === r.variant);
    const levels = [...new Set(rs.map(r => r.fanout))].sort((a, b) => a - b);
    return levels.map(f => ({ label: `${f} at once`, sub: f === 1 ? 'one microVM' : `${f} concurrent microVMs`,
      lanes: lanesOf(rs.filter(r => r.fanout === f), data.slots, r => r.client_wall_ms, r => r.variant) }));
  }

  function timeline(data) {
    return data.reports.map((rep, ri) => {
      const rs = data.rows.filter(r => r.run_index === ri && r.ok && num(r.client_wall_ms) && r.task !== 'sandbox-boot');
      if (rep.backend === 'local' || !rs.length) return null;
      rs.sort((a, b) => (num(a.started_ms) && num(b.started_ms) ? a.started_ms - b.started_ms : a.seq - b.seq));
      const v = variantOf(rep);
      return { label: shortVariant(v), rank: variantRank(v),
        points: rs.map(r => ({ v: r.client_wall_ms, warm: r.task === 'image-warmup', tip: [fmtMs(r.client_wall_ms), `${r.task}${r.fanout > 1 ? ' ×' + r.fanout : ''} · call ${r.seq + 1}`, num(r.remote_queue_ms) ? `queue ${fmtMs(r.remote_queue_ms)} · execute ${fmtMs(r.remote_execute_ms)}` : shortVariant(v)] })) };
    }).filter(Boolean).sort((a, b) => a.rank - b.rank);
  }

  function throughput(data) {
    const tasks = ['seq-write', 'seq-read', 'hash-64m'];
    const fmt = v => fmtNum(v, 'MiB/s');
    return byTask(onlyPrimary(data, suite(data.rows)).filter(r => tasks.includes(r.task) && r.work_ms > 0 && r.bytes > 0)).map(([task, rs]) => {
      const m = new Map();
      for (const r of rs) { if (!m.has(r.variant)) m.set(r.variant, { b: r.backend, vals: [] }); m.get(r.variant).vals.push(r.bytes / MiB / (r.work_ms / 1000)); }
      return { label: task, sub: C.TASK_INFO[task], points: [...m].sort((a, b) => variantRank(a[0]) - variantRank(b[0])).map(([v, { b, vals }]) => {
        const med = median(vals), ci = medianCI(vals);
        return { name: compact(v), slot: data.slots.get(b), v: med, ci, tip: [fmt(med) + ' median', `${task} · ${shortVariant(v)}`, ci ? `${(ci.level * 100).toFixed(1)}% CI ${fmt(ci[0])} – ${fmt(ci[1])}` : 'n < 5: no interval', `n=${vals.length}`] };
      }) };
    });
  }

  /** Headline numbers for hero tiles, verdict sentence and README. */
  function headline(data) {
    const all = data.rows.filter(r => !NOT_MEASURED.has(r.task));
    const pick = (variant, task, key = 'client_wall_ms') => suite(data.rows).filter(r => r.variant === variant && r.task === task && num(r[key])).map(r => r[key]);
    const stat = xs => xs.length ? { median: median(xs), ci: medianCI(xs), n: xs.length } : null;
    const exec = data.primary.get('archil'), sbx = data.primary.get('archil-sandbox');
    const execRows = suite(data.rows).filter(r => r.variant === exec);
    const slim = suite(data.rows).filter(r => /python:3\.13-slim/.test(r.variant)).map(r => r.remote_queue_ms).filter(num);
    const ratios = Object.fromEntries(slowdown(data).map(g => [g.label, g.points.find(p => p.backend === 'archil')]).filter(e => e[1]));
    const boots = data.rows.filter(r => r.task === 'sandbox-boot' && r.ok).map(r => r.client_wall_ms);
    return {
      observations: all.length, success: all.length ? all.filter(r => r.ok).length / all.length : NaN,
      execVariant: exec, sandboxVariant: sbx,
      noop: stat(pick(exec, 'noop')), sandboxNoop: sbx ? stat(pick(sbx, 'noop')) : null, v01Noop: stat(pick(V01, 'noop')),
      fsyncExec: stat(pick(exec, 'fsync-4k')), fsyncSandbox: sbx ? stat(pick(sbx, 'fsync-4k')) : null,
      queue: execRows.length ? median(execRows.map(r => r.remote_queue_ms).filter(num)) : NaN,
      slimQueue: slim.length ? median(slim) : NaN,
      network: execRows.length ? median(execRows.map(networkMs).filter(num)) : NaN,
      fsync: ratios['fsync-4k'] || null, sqlite: ratios['sqlite-commit'] || null, cpu: ratios['hash-64m'] || null,
      sandboxBoot: boots.length ? median(boots) : NaN,
      regions: [...new Set(data.reports.map(r => (r.environment || {}).region).filter(Boolean))],
      firstTs: Math.min(...data.reports.map(r => r.timestamp_unix_secs)), lastTs: Math.max(...data.reports.map(r => r.timestamp_unix_secs)),
      cleanupIssues: data.rows.filter(r => r.cleanup_error).length,
    };
  }

  /** Markdown summary table (README): local vs each remote primary variant, guest-measured work. */
  function markdownTable(data) {
    const remotes = [...data.primary].filter(([b]) => b !== 'local');
    const head = ['Task', 'Local SSD', ...remotes.map(([, v]) => shortVariant(v)), ...remotes.map(([, v]) => `${shortVariant(v)} ÷ local`)];
    const lines = ['| ' + head.join(' | ') + ' |', '|' + head.map((_, i) => (i < 1 ? '---' : '--:')).join('|') + '|'];
    for (const [task, rs] of byTask(onlyPrimary(data, suite(data.rows)))) {
      if (!rs.some(r => r.work_ms > 0)) continue; // e.g. noop: nothing measured inside the guest
      const l = rs.filter(r => r.backend === 'local' && num(r.work_ms)).map(r => r.work_ms);
      const cells = [`\`${task}\``, l.length && median(l) > 0 ? fmtMs(median(l)) : '—'];
      const ratios = [];
      for (const [b] of remotes) {
        const a = rs.filter(r => r.backend === b && num(r.work_ms)).map(r => r.work_ms);
        cells.push(a.length && median(a) > 0 ? fmtMs(median(a)) : '—');
        if (a.length && l.length && median(a) > 0 && median(l) > 0) { const { point, ci } = ratioCI(a, l, `ratio:${task}:${b}`); ratios.push(`**${fmtX(point)}**` + (ci ? ` <sub>${fmtX(ci[0])}–${fmtX(ci[1])}</sub>` : '')); }
        else ratios.push('—');
      }
      lines.push('| ' + [...cells, ...ratios].join(' | ') + ' |');
    }
    return lines.join('\n');
  }

  root.DiskScopeViews = { prepare, variantOf, shortVariant, compact, atlas, ladder, anatomy, slowdown, ecdf, fanout, timeline, throughput, headline, markdownTable };
  if (typeof module === 'object' && module.exports) module.exports = root.DiskScopeViews;
})(typeof globalThis !== 'undefined' ? globalThis : this);

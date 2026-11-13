/* DiskScope chart kernel.
 * Pure functions: data in, SVG string out. No DOM access, so the same code renders the
 * interactive dashboard (browser) and the static README charts (Node, scripts/readme-charts.mjs).
 * Every value placed into markup goes through esc(). Colors are CSS classes resolved by the
 * host stylesheet (dashboard/style.css, or the <style> block embedded in standalone SVGs). */
(function (root) {
  'use strict';

  // ---------- formatting ----------
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = s => String(s).replace(/[&<>"']/g, c => ESC[c]);
  function fmtMs(v) {
    if (!Number.isFinite(v)) return '—';
    if (v === 0) return '0';
    if (v >= 10000) return (v / 1000).toFixed(1) + ' s';
    if (v >= 1000) return (v / 1000).toFixed(2) + ' s';
    if (v >= 100) return v.toFixed(0) + ' ms';
    if (v >= 10) return v.toFixed(1) + ' ms';
    if (v >= 1) return v.toFixed(2) + ' ms';
    if (v >= 0.01) return (v * 1000).toFixed(0) + ' µs';
    return (v * 1000).toFixed(1) + ' µs';
  }
  function fmtNum(v, unit) {
    if (!Number.isFinite(v)) return '—';
    const a = Math.abs(v);
    const s = a >= 1000 ? (v / 1000).toFixed(a >= 10000 ? 0 : 1) + 'k' : a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toPrecision(2);
    return unit ? s + ' ' + unit : s;
  }
  const fmtX = v => !Number.isFinite(v) ? '—' : (v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2)) + '×';

  // ---------- statistics ----------
  // Quantile type 7 (linear interpolation), the default in R/NumPy.
  function qSorted(v, p) {
    if (!v.length) return NaN;
    const i = p * (v.length - 1), lo = Math.floor(i), hi = Math.ceil(i);
    return v[lo] + (v[hi] - v[lo]) * (i - lo);
  }
  const quantile = (xs, p) => qSorted([...xs].sort((a, b) => a - b), p);
  const median = xs => quantile(xs, 0.5);
  // Deterministic PRNG so the published dashboard and README always show identical intervals.
  function mulberry32(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function seedOf(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  const B = 4000;
  function resampleMedian(xs, r, buf) { for (let i = 0; i < xs.length; i++) buf[i] = xs[(r() * xs.length) | 0]; buf.sort((a, b) => a - b); return qSorted(buf, 0.5); }
  /** Distribution-free CI for the median from order statistics: (x(k), x(n-k+1)) with coverage
   *  1 - 2·P(Bin(n, ½) <= k-1). Picks the tightest k that still reaches 95%; for 5 <= n < 6 the best
   *  possible is (min, max) at 93.75%, which is returned with its true level. Null below n = 5.
   *  (A bootstrap of the median is degenerate at these sample sizes — see README methodology.) */
  function binomCdfHalf(k, n) { let c = 0, t = 0.5 ** n; for (let i = 0; i <= k; i++) { c += t; t = t * (n - i) / (i + 1); } return c; }
  function medianCI(xs) {
    const n = xs.length;
    if (n < 5) return null;
    const v = [...xs].sort((a, b) => a - b);
    let k = 1;
    while (k + 1 <= n / 2 && 1 - 2 * binomCdfHalf(k, n) >= 0.95) k++;
    const ci = [v[k - 1], v[n - k]];
    ci.level = 1 - 2 * binomCdfHalf(k - 1, n);
    return ci;
  }
  /** Ratio of medians a/b with an independent two-sample percentile bootstrap CI (nominal 95%). At n of
   *  5–15 per side the bootstrap of a median is coarse, so the interval is labelled approximate (≈95%). */
  function ratioCI(a, b, seed) {
    // Sort first: the resampling stream then depends only on the values, not on input-file order.
    a = [...a].sort((x, y) => x - y); b = [...b].sort((x, y) => x - y);
    const point = median(a) / median(b);
    if (a.length < 3 || b.length < 3) return { point, ci: null };
    const r = mulberry32(seedOf(seed)), ba = new Array(a.length), bb = new Array(b.length), out = new Float64Array(B);
    for (let i = 0; i < B; i++) out[i] = resampleMedian(a, r, ba) / resampleMedian(b, r, bb);
    out.sort();
    return { point, ci: [qSorted(out, 0.025), qSorted(out, 0.975)] };
  }
  function summarize(xs) {
    const v = xs.filter(Number.isFinite);
    return { n: v.length, median: median(v), p90: quantile(v, 0.9), min: Math.min(...v), max: Math.max(...v), ci: medianCI(v) };
  }

  // ---------- data shaping ----------
  const TASK_ORDER = ['noop', 'stat', 'small-files', 'fsync-4k', 'seq-write', 'seq-read', 'sqlite-commit', 'hash-64m', 'fanout-stat'];
  const TASK_INFO = {
    'noop': 'Empty command: pure exec round-trip',
    'stat': 'One stat() on the mount root',
    'small-files': 'Create 64 × 16 B files',
    'fsync-4k': 'Write 4 KiB + fsync',
    'seq-write': 'Sequential write + fsync',
    'seq-read': 'Re-read a just-written file (warm)',
    'sqlite-commit': '25 synchronous=FULL commits',
    'hash-64m': 'SHA-256 of 64 MiB (CPU only)',
    'fanout-stat': 'stat() under concurrent fan-out',
  };
  const taskRank = t => { const i = TASK_ORDER.indexOf(t); return i < 0 ? 99 : i; };
  // Color follows the entity: fixed slots for known backends, later ones in order of first sight.
  function seriesMap(backends) {
    // archil exec = near-black, local baseline = light grey, archil sandbox = mid grey.
    const m = new Map([['archil', 1], ['local', 2], ['archil-sandbox', 3]]);
    let next = 4;
    for (const b of backends) if (!m.has(b)) m.set(b, Math.min(8, next++));
    return m;
  }
  function flatten(reports) {
    const rows = [];
    reports.forEach((r, ri) => (r.observations || []).forEach((o, i) => rows.push({
      ...o, backend: r.backend, run_id: r.run_id, run_index: ri, seq: i,
      region: (r.environment && r.environment.region) || 'local', ts: r.timestamp_unix_secs,
    })));
    return rows;
  }
  const measured = rows => rows.filter(r => r.ok && r.task !== 'image-warmup' && r.task !== 'sandbox-boot');

  // ---------- scales ----------
  const NICE = [1, 2, 5];
  function logDomain(vals) {
    const pos = vals.filter(v => v > 0);
    if (!pos.length) return [1, 10];
    const lo = Math.min(...pos), hi = Math.max(...pos);
    let a = 10 ** Math.floor(Math.log10(lo)), b = 10 ** Math.ceil(Math.log10(hi));
    for (const m of [5, 2]) if (a * m <= lo) { a *= m; break; }
    for (const m of [0.2, 0.5]) if (b * m >= hi) { b *= m; break; }
    if (a === b) b = a * 10;
    return [a, b];
  }
  function logScale([d0, d1], [r0, r1]) {
    const l0 = Math.log10(d0), l1 = Math.log10(d1);
    const f = v => r0 + (Math.log10(Math.min(Math.max(v, d0), d1)) - l0) / (l1 - l0) * (r1 - r0);
    f.ticks = () => {
      const decades = l1 - l0, t = [];
      for (let e = Math.floor(l0); e <= Math.ceil(l1); e++) for (const m of (decades > 2.5 ? [1] : NICE)) {
        const v = m * 10 ** e; if (v >= d0 * 0.999 && v <= d1 * 1.001) t.push(+v.toPrecision(6));
      }
      return t;
    };
    return f;
  }
  function linearScale([d0, d1], [r0, r1]) {
    const f = v => r0 + (v - d0) / (d1 - d0 || 1) * (r1 - r0);
    f.ticks = (n = 5) => {
      const span = d1 - d0, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
      const step = [1, 2, 5, 10].map(m => m * mag).find(s => span / s <= n) || mag * 10;
      const t = []; for (let v = Math.ceil(d0 / step) * step; v <= d1 + 1e-9; v += step) t.push(+v.toPrecision(6));
      return t;
    };
    return f;
  }

  // ---------- svg primitives ----------
  const W = 960;
  const tip = lines => ` data-tip="${esc(lines.join('\n'))}" tabindex="0"`;
  const open = (h, label) => `<svg viewBox="0 0 ${W} ${h}" width="100%" role="img" aria-label="${esc(label)}" class="ds-chart" xmlns="http://www.w3.org/2000/svg">`;
  // Bar with a 4px rounded data-end and a square baseline end (horizontal bars grow rightwards).
  function hbar(x, y, w, h, rounded, cls, extra = '') {
    if (w <= 0) return '';
    const r = rounded ? Math.min(4, w, h / 2) : 0;
    if (!r) return `<rect x="${x.toFixed(1)}" y="${y}" width="${w.toFixed(1)}" height="${h}" class="${cls}"${extra}/>`;
    const x1 = x + w;
    return `<path d="M${x.toFixed(1)},${y}H${(x1 - r).toFixed(1)}Q${x1.toFixed(1)},${y} ${x1.toFixed(1)},${y + r}V${y + h - r}Q${x1.toFixed(1)},${y + h} ${(x1 - r).toFixed(1)},${y + h}H${x.toFixed(1)}Z" class="${cls}"${extra}/>`;
  }
  function xAxis(scale, y0, y1, fmt) {
    return scale.ticks().map(t => { const x = scale(t).toFixed(1); return `<line x1="${x}" x2="${x}" y1="${y0}" y2="${y1}" class="ds-grid"/><text x="${x}" y="${y1 + 16}" class="ds-axis" text-anchor="middle">${esc(fmt(t))}</text>`; }).join('');
  }
  function legend(items, x, y) {
    let cx = x, out = '';
    for (const it of items) {
      out += it.kind === 'line' ? `<line x1="${cx}" x2="${cx + 14}" y1="${y - 4}" y2="${y - 4}" class="st-s${it.slot}" stroke-width="2"/>`
        : it.kind === 'dot' ? `<circle cx="${cx + 5}" cy="${y - 4}" r="4.5" class="f-s${it.slot}"/>`
        : `<rect x="${cx}" y="${y - 10}" width="12" height="12" rx="3" class="${it.cls || 'f-s' + it.slot}"/>`;
      out += `<text x="${cx + 19}" y="${y}" class="ds-legend">${esc(it.label)}</text>`;
      cx += 30 + it.label.length * 7.2;
    }
    return out;
  }
  const jitter = (key, i, amp) => ((mulberry32(seedOf(key + ':' + i))() - 0.5) * 2 * amp);

  // ---------- charts ----------
  /**
   * Dot (strip) plot: every observation is a dot; the median is a tick and the bootstrap
   * 95% CI of the median is a translucent band behind it. Log x axis by default.
   * groups: [{label, sub, lanes:[{name, slot, values:[{v, tip:[...]}]}]}]
   */
  function stripPlot(groups, { title = 'Latency', unit = 'ms', fmt = fmtMs, log = true, legendItems = null } = {}) {
    groups = groups.filter(g => g.lanes.some(l => l.values.length));
    if (!groups.length) return null;
    const hasLanes = groups.some(g => g.lanes.filter(l => l.values.length).length > 1);
    const left = hasLanes ? 318 : 230, right = 104, top = legendItems ? 40 : 14, laneH = 26, gap = 14;
    const all = groups.flatMap(g => g.lanes.flatMap(l => l.values.map(d => d.v)));
    const plotBottom = top + groups.reduce((s, g) => s + g.lanes.filter(l => l.values.length).length * laneH + gap, 0) - gap;
    const h = plotBottom + 34;
    const dom = log ? logDomain(all) : [0, Math.max(...all) * 1.08];
    const x = (log ? logScale : linearScale)(dom, [left, W - right]);
    let s = open(h, title);
    if (legendItems) s += legend(legendItems, left, 18);
    s += `<text x="${W - right + 12}" y="${top - 2 + (legendItems ? 0 : 10)}" class="ds-axis ds-strong" ${legendItems ? '' : 'visibility="hidden"'}>median</text>`;
    s += xAxis(x, top - 6, plotBottom + 4, fmt);
    let y = top;
    groups.forEach((g, gi) => {
      const lanes = g.lanes.filter(l => l.values.length);
      const bandH = lanes.length * laneH;
      if (gi) s += `<line x1="8" x2="${W - 8}" y1="${y - gap / 2}" y2="${y - gap / 2}" class="ds-sep"/>`;
      s += `<text x="8" y="${y + bandH / 2 + (g.sub ? -2 : 4)}" class="ds-label">${esc(g.label)}</text>`;
      if (g.sub) s += `<text x="8" y="${y + bandH / 2 + 12}" class="ds-sub">${esc(g.sub)}</text>`;
      lanes.forEach(l => {
        const cy = y + laneH / 2, vals = l.values.map(d => d.v), st = summarize(vals);
        if (lanes.length > 1) s += `<text x="${left - 10}" y="${cy + 4}" class="ds-muted" text-anchor="end">${esc(l.name)}</text>`;
        if (st.ci) s += `<rect x="${x(st.ci[0]).toFixed(1)}" y="${cy - 7}" width="${Math.max(3, x(st.ci[1]) - x(st.ci[0])).toFixed(1)}" height="14" rx="7" class="ci-s${l.slot}"/>`;
        l.values.forEach((d, i) => {
          const cx = x(d.v).toFixed(1), jy = (cy + jitter(g.label + l.name, i, 6)).toFixed(1);
          s += `<circle cx="${cx}" cy="${jy}" r="4.75" class="f-s${l.slot} ds-ring ds-dot"/><circle cx="${cx}" cy="${jy}" r="11" class="ds-hit"${tip(d.tip)}/>`;
        });
        const mx = x(st.median).toFixed(1);
        s += `<line x1="${mx}" x2="${mx}" y1="${cy - 11}" y2="${cy + 11}" class="ds-median-halo"/><line x1="${mx}" x2="${mx}" y1="${cy - 10}" y2="${cy + 10}" class="ds-median"/>`;
        s += `<rect x="${left}" y="${y + 2}" width="${W - right - left}" height="${laneH - 4}" class="ds-hit ds-lanehit"${tip([fmt(st.median) + ' median', `${g.label} · ${l.name}`, st.ci ? `${(st.ci.level * 100).toFixed(1)}% CI ${fmt(st.ci[0])} – ${fmt(st.ci[1])}` : 'n < 5: no interval', `n=${st.n} · min ${fmt(st.min)} · max ${fmt(st.max)}`])}/>`;
        s += `<text x="${W - right + 12}" y="${cy + 4}" class="ds-value">${esc(fmt(st.median))}</text>`;
        y += laneH;
      });
      y += gap;
    });
    return s + '</svg>';
  }

  /** Empirical CDF per series, log x. Points carry percentile tooltips; p50/p95 reference rules. */
  function ecdf(series, { title = 'ECDF', fmt = fmtMs } = {}) {
    series = series.filter(s => s.values.length);
    if (!series.length) return null;
    const left = 52, right = 24, top = 40, ph = 280, h = top + ph + 34;
    const x = logScale(logDomain(series.flatMap(s => s.values)), [left, W - right]);
    const y = p => top + ph - p * ph;
    let s = open(h, title) + legend(series.map(se => ({ label: `${se.name} (n=${se.values.length})`, slot: se.slot, kind: 'line' })), left, 18);
    s += xAxis(x, top, top + ph, fmt);
    for (const p of [0, 0.25, 0.5, 0.75, 0.95, 1]) {
      s += `<line x1="${left}" x2="${W - right}" y1="${y(p)}" y2="${y(p)}" class="${p === 0.5 || p === 0.95 ? 'ds-ref' : 'ds-grid'}"/><text x="${left - 8}" y="${y(p) + 4}" class="ds-axis" text-anchor="end">p${Math.round(p * 100)}</text>`;
    }
    for (const se of series) {
      const v = [...se.values].sort((a, b) => a - b), n = v.length;
      let d = `M${x(v[0]).toFixed(1)},${y(0).toFixed(1)}`;
      v.forEach((val, i) => { const xx = x(val).toFixed(1); d += `V${y((i + 1) / n).toFixed(1)}`; if (i + 1 < n) d += `H${x(v[i + 1]).toFixed(1)}`; });
      s += `<path d="${d}" class="st-s${se.slot} ds-line"/>`;
      v.forEach((val, i) => {
        const cx = x(val).toFixed(1), cy = y((i + 1) / n).toFixed(1);
        s += `<circle cx="${cx}" cy="${cy}" r="9" class="ds-hit"${tip([fmt(val), `${se.name}: ${Math.round((i + 1) / n * 100)}% of calls at or below`])}/>`;
      });
      const p95 = qSorted(v, 0.95);
      s += `<circle cx="${x(p95).toFixed(1)}" cy="${y(0.95).toFixed(1)}" r="4.5" class="f-s${se.slot} ds-ring"/>`;
    }
    return s + '</svg>';
  }

  /** Horizontal stacked bars of median phase durations: queue | execute | other. */
  function anatomy(rows, { title = 'Execution anatomy' } = {}) {
    rows = rows.filter(r => r.total > 0);
    if (!rows.length) return null;
    const left = 214, right = 92, top = 44, rowH = 34, barH = 18, h = top + rows.length * rowH + 30;
    const max = Math.max(...rows.map(r => r.total));
    const x = linearScale([0, max * 1.04], [left, W - right]);
    const parts = [['queue', 'f-phase-q', 'Queue · VM · mount'], ['startup', 'f-phase-s', 'Start-up · commit'], ['work', 'f-phase-w', 'Measured work'], ['other', 'f-phase-o', 'Network · API']];
    let s = open(h, title) + legend(parts.map(p => ({ label: p[2], cls: p[1] })), left, 18);
    s += xAxis(x, top - 6, top + rows.length * rowH - 6, fmtMs);
    rows.forEach((r, i) => {
      const y = top + i * rowH + (rowH - barH) / 2 - 6;
      s += `<text x="8" y="${y + 13}" class="ds-label">${esc(r.label)}</text>`;
      let off = 0; const segs = parts.map(p => [p, r[p[0]]]).filter(([, v]) => v > 0);
      segs.forEach(([p, v], k) => {
        const x0 = x(off) + (k ? 1 : 0), w = x(off + v) - x(off) - (k ? 1 : 0) - (k < segs.length - 1 ? 1 : 0);
        s += hbar(x0, y, w, barH, k === segs.length - 1, p[1], tip([fmtMs(v) + ' ' + p[2].toLowerCase(), `${r.label} · median of n=${r.n}`, `${Math.round(v / r.total * 100)}% of ${fmtMs(r.total)}`]));
        off += v;
      });
      s += `<text x="${(x(r.total) + 8).toFixed(1)}" y="${y + 13}" class="ds-value">${esc(fmtMs(r.total))}</text>`;
    });
    return s + '</svg>';
  }

  /** Cleveland dot plot with CI whiskers on a log axis; optional vertical reference (e.g. parity = 1×). */
  function dotCI(groups, { title = '', fmt = fmtNum, ref = null, refLabel = '', legendItems = null, sideLabels = null } = {}) {
    groups = groups.filter(g => g.points.length);
    if (!groups.length) return null;
    const left = groups.some(g => g.points.length > 1) ? 318 : 250, right = 96, top = legendItems || sideLabels ? 44 : 16, laneH = 26, gap = 12;
    const all = groups.flatMap(g => g.points.flatMap(p => [p.v, ...(p.ci || [])])).concat(ref ? [ref] : []);
    const x = logScale(logDomain(all), [left, W - right]);
    const plotBottom = top + groups.reduce((a, g) => a + g.points.length * laneH + gap, 0) - gap;
    const h = plotBottom + 34;
    let s = open(h, title);
    if (legendItems) s += legend(legendItems, left, 18);
    s += xAxis(x, top - 6, plotBottom + 4, fmt);
    if (ref) {
      const rx = x(ref).toFixed(1);
      s += `<line x1="${rx}" x2="${rx}" y1="${top - 10}" y2="${plotBottom + 4}" class="ds-refv"/>`;
      if (refLabel && !sideLabels) s += `<text x="${rx}" y="${top - 14}" class="ds-axis ds-strong" text-anchor="middle">${esc(refLabel)}</text>`;
      if (sideLabels) s += `<text x="${(x(ref) - 10).toFixed(1)}" y="18" class="ds-muted" text-anchor="end">← ${esc(sideLabels[0])}</text><text x="${(x(ref) + 10).toFixed(1)}" y="18" class="ds-muted">${esc(sideLabels[1])} →</text>`;
    }
    let y = top;
    groups.forEach((g, gi) => {
      const bandH = g.points.length * laneH;
      if (gi) s += `<line x1="8" x2="${W - 8}" y1="${y - gap / 2}" y2="${y - gap / 2}" class="ds-sep"/>`;
      s += `<text x="8" y="${y + bandH / 2 + (g.sub ? -2 : 4)}" class="ds-label">${esc(g.label)}</text>`;
      if (g.sub) s += `<text x="8" y="${y + bandH / 2 + 12}" class="ds-sub">${esc(g.sub)}</text>`;
      g.points.forEach(p => {
        const cy = y + laneH / 2;
        if (g.points.length > 1) s += `<text x="${left - 10}" y="${cy + 4}" class="ds-muted" text-anchor="end">${esc(p.name)}</text>`;
        if (p.ci) s += `<line x1="${x(p.ci[0]).toFixed(1)}" x2="${x(p.ci[1]).toFixed(1)}" y1="${cy}" y2="${cy}" class="st-s${p.slot} ds-whisker"/>`;
        s += `<circle cx="${x(p.v).toFixed(1)}" cy="${cy}" r="5.5" class="f-s${p.slot} ds-ring"/>`;
        s += `<rect x="${left}" y="${y + 2}" width="${W - right - left}" height="${laneH - 4}" class="ds-hit ds-lanehit"${tip(p.tip)}/>`;
        s += `<text x="${W - right + 12}" y="${cy + 4}" class="ds-value">${esc(fmt(p.v))}</text>`;
        y += laneH;
      });
      y += gap;
    });
    return s + '</svg>';
  }

  /** Every call in submission order (log y) — exposes cold starts, drift and stragglers. */
  function timeline(runs, { title = 'Call timeline' } = {}) {
    const pts = runs.flatMap(r => r.points);
    if (!pts.length) return null;
    const left = 64, right = 20, top = 40, ph = 230, h = top + ph + 40;
    const dom = logDomain(pts.map(p => p.v));
    const y = logScale(dom, [top + ph, top]);
    const n = pts.length, x = i => left + (n === 1 ? 0.5 : i / (n - 1)) * (W - left - right);
    let s = open(h, title) + legend([{ label: 'measured call', slot: 1, kind: 'dot' }, { label: 'image warmup', cls: 'ds-warm-key' }, { label: 'run median', cls: 'ds-ref-key' }], left, 18);
    for (const t of y.ticks()) s += `<line x1="${left}" x2="${W - right}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" class="ds-grid"/><text x="${left - 8}" y="${(y(t) + 4).toFixed(1)}" class="ds-axis" text-anchor="end">${esc(fmtMs(t))}</text>`;
    let i = 0;
    runs.forEach((r, ri) => {
      const start = i, xs = x(start);
      if (ri) s += `<line x1="${(xs - 3).toFixed(1)}" x2="${(xs - 3).toFixed(1)}" y1="${top}" y2="${top + ph}" class="ds-sep"/>`;
      const med = median(r.points.filter(p => !p.warm).map(p => p.v));
      const xe = x(start + r.points.length - 1);
      if (Number.isFinite(med)) s += `<line x1="${xs.toFixed(1)}" x2="${xe.toFixed(1)}" y1="${y(med).toFixed(1)}" y2="${y(med).toFixed(1)}" class="ds-ref"/>`;
      s += `<text x="${xs.toFixed(1)}" y="${top + ph + 18}" class="ds-axis">${esc(r.label)}</text>`;
      r.points.forEach(p => {
        const cx = x(i).toFixed(1), cy = y(p.v).toFixed(1);
        s += p.warm ? `<circle cx="${cx}" cy="${cy}" r="5" class="ds-warm"/>` : `<circle cx="${cx}" cy="${cy}" r="3.5" class="f-s1 ds-ring"/>`;
        s += `<circle cx="${cx}" cy="${cy}" r="10" class="ds-hit"${tip(p.tip)}/>`;
        i++;
      });
    });
    return s + '</svg>';
  }

  root.DiskScopeCharts = {
    esc, fmtMs, fmtNum, fmtX, quantile, median, medianCI, ratioCI, summarize,
    TASK_ORDER, TASK_INFO, taskRank, seriesMap, flatten, measured,
    stripPlot, ecdf, anatomy, dotCI, timeline,
  };
  if (typeof module === 'object' && module.exports) module.exports = root.DiskScopeCharts;
})(typeof globalThis !== 'undefined' ? globalThis : this);

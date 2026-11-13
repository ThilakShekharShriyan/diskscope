/* DiskScope dashboard: DOM + interaction. Offline, no libraries, no telemetry.
 * Chart geometry lives in charts.js, data shaping in views.js. */
(() => {
  'use strict';
  const C = window.DiskScopeCharts, V = window.DiskScopeViews;
  const $ = id => document.getElementById(id);
  let reports = [];
  try { reports = JSON.parse($('payload').textContent || '[]'); } catch (_) { reports = []; }
  const state = { backend: 'all', task: 'all', metric: 'client_wall_ms' };

  // ---- theme (viewer convenience only; storage may be unavailable) ----
  const store = { get: k => { try { return localStorage.getItem(k); } catch (_) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} } };
  const savedTheme = store.get('diskscope-theme');
  if (savedTheme === 'light' || savedTheme === 'dark') document.documentElement.dataset.theme = savedTheme;
  $('themeBtn').addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = dark ? 'light' : 'dark';
    store.set('diskscope-theme', document.documentElement.dataset.theme);
  });

  // ---- helpers ----
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  function put(id, svg, emptyMsg) {
    const box = $(id);
    if (svg) box.innerHTML = svg; // chart kernel escapes every data-derived string
    else { box.replaceChildren(el('div', 'empty', emptyMsg || 'No matching measurements for this filter.')); }
  }
  const filtered = data => ({ ...data, rows: data.rows.filter(r => (state.backend === 'all' || r.backend === state.backend) && (state.task === 'all' || r.task === state.task)) });

  // ---- hero ----
  function tile(k, v, unit, d) {
    const t = el('div', 'tile'); t.append(el('div', 'k', k));
    const vv = el('div', 'v', v); if (unit) vv.append(el('small', null, unit)); t.append(vv, el('div', 'd', d)); return t;
  }
  function splitMs(v) { const s = C.fmtMs(v); const i = s.lastIndexOf(' '); return i > 0 ? [s.slice(0, i), s.slice(i + 1)] : [s, '']; }
  function hero(data) {
    const h = V.headline(data);
    const verdict = $('verdict'); verdict.replaceChildren();
    if (!data.rows.length) { verdict.textContent = 'No measurements loaded. Run diskscope local / archil, then diskscope report.'; return; }
    const parts = [];
    if (h.noop) parts.push(['A warm Archil tool call completes in ', C.fmtMs(h.noop.median), ' end-to-end. ']);
    if (h.fsync) parts.push(['A durable 4 KiB fsync is only ', C.fmtX(h.fsync.v) + ' slower', ' than on a local SSD, ']);
    if (h.sqlite) parts.push(['but commit-heavy SQLite work is ', C.fmtX(h.sqlite.v) + ' slower', '. ']);
    if (h.parallel) parts.push(['Fanning out across ', h.parallel.parallel + ' microVMs', ` ran ${h.parallel.calls} calls ${h.parallel.speedup.toFixed(1)}× faster than one at a time.`]);
    for (const [a, b, c] of parts) { verdict.append(a); verdict.append(el('strong', null, b)); verdict.append(c); }

    const meta = $('meta'); meta.replaceChildren();
    const chip = (text, slot) => { const c = el('span', 'chip'); if (slot) { const i = el('i'); i.style.background = `var(--s${slot})`; c.append(i); } c.append(text); meta.append(c); };
    for (const [b, s] of data.slots) if (data.rows.some(r => r.backend === b)) chip(b === 'archil' ? `Archil · ${h.regions.join(', ') || 'remote'}` : b === 'local' ? 'Local SSD (durable fsync)' : b, s);
    chip(`${h.observations.toLocaleString()} observations · ${(h.success * 100).toFixed(1)}% valid`);
    if (Number.isFinite(h.firstTs)) chip(new Date(h.lastTs * 1000).toISOString().slice(0, 10));

    const tiles = $('tiles'); tiles.replaceChildren();
    if (h.noop) { const [v, u] = splitMs(h.noop.median); tiles.append(tile('Warm tool call', v, u, h.noop.ci ? `median, ${(h.noop.ci.level * 100).toFixed(0)}% CI ${C.fmtMs(h.noop.ci[0])}–${C.fmtMs(h.noop.ci[1])} · n=${h.noop.n}` : `median · n=${h.noop.n}`)); }
    if (Number.isFinite(h.queue)) { const [v, u] = splitMs(h.queue); tiles.append(tile('Queue · VM · mount', v, u, `median Archil-reported queue; network + API adds ~${C.fmtMs(h.network)}`)); }
    if (h.fsync) tiles.append(tile('Durable fsync vs local', C.fmtX(h.fsync.v), '', h.fsync.ci ? `95% CI ${C.fmtX(h.fsync.ci[0])}–${C.fmtX(h.fsync.ci[1])} · SQLite commit ${C.fmtMs(h.sqliteCommitMs.archil)} vs ${C.fmtMs(h.sqliteCommitMs.local)}` : 'Archil ÷ local, median work'));
    if (h.parallel) tiles.append(tile(`${h.parallel.parallel} microVMs in parallel`, h.parallel.speedup.toFixed(1) + '×', 'faster', `${h.parallel.calls} calls in ${h.parallel.seconds.toFixed(1)} s vs ~${(h.parallel.seconds * h.parallel.speedup).toFixed(0)} s serially`));
    else tiles.append(tile('Observations', h.observations.toLocaleString(), '', `${(h.success * 100).toFixed(1)}% passed validation`));
  }

  // ---- charts ----
  function render() {
    const all = V.prepare(reports), data = filtered(all);
    hero(all);
    const n = C.measured(data.rows).length;
    $('sampleHint').textContent = n ? `${n} measured calls in view${n < 30 ? ' · small sample: read intervals, not tails' : ''}` : '';
    const legendFor = d => [...d.slots].filter(([b]) => d.rows.some(r => r.backend === b)).map(([b, s]) => ({ label: b, slot: s, kind: 'dot' }));
    const metricFmt = C.fmtMs;
    $('atlasDesc').textContent = {
      client_wall_ms: 'End-to-end: from the client sending the request to the full response. Each dot is one call; the black tick is the median; the shaded band is a distribution-free confidence interval for the median. Log scale.',
      remote_execute_ms: 'Archil-reported execute time: the guest command inside the microVM, including Python start-up. Local runs have no equivalent and are hidden. Log scale.',
      work_ms: 'Guest-measured work: only the timed operation itself, identical code on both sides. This is the fair cross-environment comparison. Log scale.',
    }[state.metric];
    put('atlasChart', C.stripPlot(V.atlas(data, state.metric), { title: 'Latency by task', fmt: metricFmt, legendItems: legendFor(data) }));
    const anat = V.anatomy(data);
    put('anatomyChart', C.anatomy(anat), 'No Archil calls in this filter.');
    const noop = anat.find(r => r.label === 'noop');
    $('anatomyInsight').replaceChildren();
    if (noop) {
      const fixed = noop.queue + noop.startup + noop.other;
      $('anatomyInsight').append('Fixed cost per call: ', el('strong', null, C.fmtMs(fixed)), ` before any work happens — ${Math.round(noop.queue / fixed * 100)}% queue/VM/mount, ${Math.round(noop.startup / fixed * 100)}% guest start-up, ${Math.round(noop.other / fixed * 100)}% network/API. Batching several operations per call amortizes it.`);
    }
    put('slowdownChart', C.dotCI(V.slowdown(data), { title: 'Archil vs local', fmt: C.fmtX, ref: 1, refLabel: 'parity', sideLabels: ['Archil faster', 'Archil slower'] }), 'Needs both local and Archil runs of the same task.');
    put('ecdfChart', C.ecdf(V.ecdf(data, state.task), { title: 'ECDF of end-to-end latency' }));
    const fan = V.fanout({ ...all, rows: all.rows.filter(r => state.backend === 'all' || r.backend === state.backend) });
    put('fanoutChart', C.stripPlot(fan, { title: 'Fan-out' }), 'No fan-out runs in this filter.');
    $('fanoutInsight').replaceChildren();
    if (fan.length > 1) {
      const meds = fan.map(g => C.median(g.lanes.flatMap(l => l.values.map(v => v.v))));
      $('fanoutInsight').append('Median moves from ', el('strong', null, C.fmtMs(meds[0])), ' (one call) to ', el('strong', null, C.fmtMs(meds[meds.length - 1])), ` (${fan[fan.length - 1].label}): concurrent microVMs ${Math.abs(meds[meds.length - 1] / meds[0] - 1) < 0.15 ? 'do not measurably slow each other at this scale' : 'interfere measurably'}.`);
    }
    put('timelineChart', C.timeline(V.timeline({ ...all, rows: all.rows.filter(r => state.task === 'all' || r.task === state.task || r.task === 'image-warmup') })), 'No Archil runs loaded.');
    put('throughputChart', C.dotCI(V.throughput(data), { title: 'Throughput', fmt: v => C.fmtNum(v, 'MiB/s'), legendItems: legendFor(data) }), 'No bulk-I/O tasks in this filter.');
    table(data);
  }

  const ICON = { ok: '<svg viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    fail: '<svg viewBox="0 0 12 12"><path d="M3 3l6 6M9 3 3 9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    warn: '<svg viewBox="0 0 12 12"><path d="M6 1.5 11 10.5H1Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M6 5v2.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>' };
  function table(data) {
    const body = $('rawRows'); body.replaceChildren();
    const rows = data.rows, LIMIT = 600;
    for (const r of rows.slice(0, LIMIT)) {
      const tr = el('tr');
      const cells = [r.backend, r.task + (r.fanout > 1 ? ' ×' + r.fanout : ''), String(r.trial + 1), String(r.parallel || r.run_parallel || 1), C.fmtMs(r.client_wall_ms), C.fmtMs(r.remote_queue_ms), C.fmtMs(r.remote_execute_ms), C.fmtMs(r.work_ms)];
      cells.forEach((c, i) => tr.append(el('td', i >= 2 ? 'num' : null, c)));
      const kind = !r.ok ? 'fail' : r.cleanup_error ? 'warn' : 'ok';
      const st = el('span', 'status ' + kind); st.innerHTML = ICON[kind]; st.append(kind === 'fail' ? 'Failed' : kind === 'warn' ? 'Passed · cleanup' : 'Passed');
      const td = el('td'); td.append(st); tr.append(td);
      const notes = [r.error, r.cleanup_error && 'Cleanup: ' + r.cleanup_error].filter(Boolean);
      if (notes.length) tr.title = notes.join('\n');
      body.append(tr);
    }
    $('rawCount').textContent = `${rows.length.toLocaleString()} rows${rows.length > LIMIT ? ` (first ${LIMIT} shown; CSV export has all)` : ''} · ${rows.filter(r => r.cleanup_error).length} with cleanup warnings (Archil returned EROFS removing a directory after fsync).`;
  }

  // ---- filters ----
  function options(select, names) {
    const prev = select.value, first = select.options[0];
    select.replaceChildren(first);
    names.forEach(n => select.append(new Option(n, n)));
    if ([...select.options].some(o => o.value === prev)) select.value = prev;
  }
  function refresh() {
    const rows = V.prepare(reports).rows;
    options($('backendFilter'), [...new Set(rows.map(r => r.backend))].sort());
    options($('taskFilter'), [...new Set(rows.map(r => r.task))].filter(t => t !== 'image-warmup').sort((a, b) => C.taskRank(a) - C.taskRank(b)));
    render();
  }
  $('backendFilter').addEventListener('change', e => { state.backend = e.target.value; render(); });
  $('taskFilter').addEventListener('change', e => { state.task = e.target.value; render(); });
  $('metricSeg').addEventListener('click', e => {
    const b = e.target.closest('button[data-metric]'); if (!b) return;
    state.metric = b.dataset.metric;
    for (const x of $('metricSeg').querySelectorAll('button')) x.setAttribute('aria-pressed', String(x === b));
    render();
  });

  // ---- tooltip (hover + keyboard focus) ----
  const tipEl = $('tip');
  function showTip(target, x, y) {
    const lines = (target.getAttribute('data-tip') || '').split('\n');
    tipEl.replaceChildren(el('b', null, lines[0]));
    lines.slice(1).forEach(l => tipEl.append(el('div', null, l)));
    tipEl.classList.add('on');
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x + 14)) + 'px';
    tipEl.style.top = (y + h + 22 > window.innerHeight ? y - h - 12 : y + 16) + 'px';
  }
  document.addEventListener('pointermove', e => { const t = e.target.closest && e.target.closest('[data-tip]'); if (t) showTip(t, e.clientX, e.clientY); else tipEl.classList.remove('on'); });
  document.addEventListener('focusin', e => { const t = e.target.closest && e.target.closest('[data-tip]'); if (t) { const b = t.getBoundingClientRect(); showTip(t, b.right, b.top); } });
  document.addEventListener('focusout', () => tipEl.classList.remove('on'));
  window.addEventListener('scroll', () => tipEl.classList.remove('on'), { passive: true });

  // ---- import / export ----
  $('fileInput').addEventListener('change', async e => {
    for (const f of e.target.files) {
      try { const j = JSON.parse(await f.text()); const batch = Array.isArray(j) ? j : [j];
        if (batch.some(r => r.schema_version !== 1 || !Array.isArray(r.observations))) throw new Error('not a DiskScope v1 report');
        reports.push(...batch);
      } catch (err) { console.warn(`${f.name}: ${err.message}`); $('rawCount').textContent = `${f.name}: ${err.message}`; }
    }
    refresh();
  });
  $('exportBtn').addEventListener('click', () => {
    const cols = ['backend', 'run_id', 'task', 'trial', 'fanout', 'parallel', 'ok', 'started_ms', 'client_wall_ms', 'remote_total_ms', 'remote_queue_ms', 'remote_execute_ms', 'work_ms', 'operations', 'bytes', 'error', 'cleanup_error'];
    const q = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
    const csv = [cols.join(','), ...filtered(V.prepare(reports)).rows.map(r => cols.map(k => q(r[k])).join(','))].join('\n');
    const a = el('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'diskscope-observations.csv'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  // ---- flamegraph: auto-load a committed docs/flamegraph.svg, or open one locally ----
  function showFlame(url) { $('flameObject').data = url; $('flameObject').hidden = false; $('flameEmpty').hidden = true; }
  $('flameInput').addEventListener('change', e => { const f = e.target.files && e.target.files[0]; if (f && /\.svg$/i.test(f.name)) showFlame(URL.createObjectURL(f)); });
  if (location.protocol.startsWith('http')) fetch('flamegraph.svg', { method: 'HEAD' }).then(r => { if (r.ok) showFlame('flamegraph.svg'); }).catch(() => {});

  refresh();
})();

/* DiskScope dashboard: DOM + interaction. Offline, no libraries, no telemetry.
 * Chart geometry lives in charts.js, data shaping in views.js. */
(() => {
  'use strict';
  const C = window.DiskScopeCharts, V = window.DiskScopeViews;
  const $ = id => document.getElementById(id);
  let reports = [];
  try { reports = JSON.parse($('payload').textContent || '[]'); } catch (_) { reports = []; }
  const state = { backend: 'all', task: 'all', metric: 'client_wall_ms' };

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  function put(id, svg, emptyMsg) {
    const box = $(id);
    if (svg) box.innerHTML = svg; // the chart kernel escapes every data-derived string
    else box.replaceChildren(el('div', 'empty', emptyMsg || 'No matching measurements for this filter.'));
  }
  const filtered = data => ({ ...data, rows: data.rows.filter(r => (state.backend === 'all' || r.backend === state.backend) && (state.task === 'all' || r.task === state.task)) });
  const legendFor = data => V.ecdf(data, 'all').map(s => ({ label: s.name, slot: s.slot, kind: 'dot' }));
  const splitMs = v => { const s = C.fmtMs(v), i = s.lastIndexOf(' '); return i > 0 ? [s.slice(0, i), s.slice(i + 1)] : [s, '']; };
  const append = (node, parts) => { for (const p of parts) node.append(typeof p === 'string' ? p : el('strong', null, p.b)); };

  // ---- hero ----
  function tile(k, v, unit, d) {
    const t = el('div', 'tile'); t.append(el('div', 'k', k));
    const vv = el('div', 'v', v); if (unit) vv.append(el('small', null, unit)); t.append(vv, el('div', 'd', d)); return t;
  }
  function hero(data) {
    const h = V.headline(data);
    const verdict = $('verdict'); verdict.replaceChildren();
    if (!data.rows.length) { verdict.textContent = 'No measurements loaded. Run diskscope local / archil, then diskscope report.'; return; }
    const parts = [];
    if (h.noop) parts.push('A warm serverless call completes in ', { b: C.fmtMs(h.noop.median) }, ' end-to-end');
    if (h.noop && h.v01Noop) parts.push(` (${C.fmtX(h.v01Noop.median / h.noop.median)} faster than our first run)`);
    if (h.sandboxNoop) parts.push('; a persistent worker in a warm sandbox answers in ', { b: C.fmtMs(h.sandboxNoop.median) }, '.');
    else if (h.noop) parts.push('.');
    if (h.fsync) parts.push(' A durable 4 KiB fsync is ', { b: `${C.fmtX(h.fsync.v)} ${h.fsync.v >= 1 ? 'slower' : 'faster'}` }, ' than on a local SSD');
    if (h.fsync && h.sqlite) parts.push(h.sqlite.v > h.fsync.v ? '; commit-heavy SQLite is ' : ', and commit-heavy SQLite only ', { b: `${C.fmtX(h.sqlite.v)} ${h.sqlite.v >= 1 ? 'slower' : 'faster'}` }, '.');
    else if (h.fsync) parts.push('.');
    append(verdict, parts);

    const meta = $('meta'); meta.replaceChildren();
    const chip = (text, slot) => { const c = el('span'); if (slot) { const i = el('i'); i.style.background = `var(--s${slot})`; c.append(i); } c.append(text); meta.append(c); };
    for (const s of V.ecdf(data, 'all')) chip(s.name, s.slot);
    chip(`${h.observations.toLocaleString()} observations · ${(h.success * 100).toFixed(1)}% valid`);
    if (h.regions.length) chip(h.regions.join(', '));
    if (Number.isFinite(h.lastTs)) chip(new Date(h.lastTs * 1000).toISOString().slice(0, 10));

    const tiles = $('tiles'); tiles.replaceChildren();
    if (h.noop) { const [v, u] = splitMs(h.noop.median); tiles.append(tile('Serverless call', v, u, `median no-op, default image · n=${h.noop.n}${h.v01Noop ? ` · was ${C.fmtMs(h.v01Noop.median)}` : ''}`)); }
    if (h.sandboxNoop) { const [v, u] = splitMs(h.sandboxNoop.median); tiles.append(tile('Warm sandbox worker', v, u, `median no-op · one-time boot ${C.fmtMs(h.sandboxBoot)}`)); }
    if (Number.isFinite(h.queue)) { const [v, u] = splitMs(h.queue); tiles.append(tile('Queue · VM · mount', v, u, Number.isFinite(h.slimQueue) ? `warm pool · ${C.fmtMs(h.slimQueue)} with a custom image` : 'median Archil-reported')); }
    if (h.fsync) tiles.append(tile('Durable fsync vs local', C.fmtX(h.fsync.v), '', h.fsync.ci ? `≈95% CI ${C.fmtX(h.fsync.ci[0])}–${C.fmtX(h.fsync.ci[1])} · guest-measured` : 'guest-measured work'));
  }

  // ---- charts ----
  function render() {
    const all = V.prepare(reports), data = filtered(all);
    hero(all);
    const n = C.measured(data.rows).length;
    $('sampleHint').textContent = n ? `${n.toLocaleString()} measured calls in view${n < 30 ? ' · small sample' : ''}` : '';
    $('atlasDesc').textContent = {
      client_wall_ms: 'End-to-end: from the client sending the request to receiving the full response, for every configuration.',
      remote_execute_ms: 'Archil-reported execute time: the command inside the microVM, including interpreter start and the exit commit. Only serverless exec reports it.',
      work_ms: 'Guest-measured work: only the timed operation itself — identical code everywhere. The fair cross-environment comparison.',
    }[state.metric];

    put('ladderChart', C.stripPlot(V.ladder(data), { title: 'Optimisation ladder' }));
    const h = V.headline(all);
    $('ladderInsight').replaceChildren();
    if (h.v01Noop && h.noop) {
      append($('ladderInsight'), ['A no-op went from ', { b: C.fmtMs(h.v01Noop.median) }, ' to ', { b: C.fmtMs(h.noop.median) }, ' as a serverless call',
        ...(h.sandboxNoop ? [', and to ', { b: C.fmtMs(h.sandboxNoop.median) }, ' through a warm sandbox worker'] : []), '. A sandbox pays off once a session makes enough calls to amortise its one-time boot.']);
      $('ladderTitle').textContent = `From ${C.fmtMs(h.v01Noop.median)} to ${C.fmtMs((h.sandboxNoop || h.noop).median)}`;
    }

    put('atlasChart', C.stripPlot(V.atlas(data, state.metric), { title: 'Latency by task' }));

    const anat = V.anatomy(data);
    put('anatomyChart', C.anatomy(anat), 'No serverless calls in this filter.');
    $('anatomyInsight').replaceChildren();
    const noop = anat.find(r => r.label === 'noop'), fs = anat.find(r => r.label === 'fsync-4k');
    if (noop) {
      const fixed = noop.queue + noop.startup + noop.other;
      append($('anatomyInsight'), ['Fixed cost per call: ', { b: C.fmtMs(fixed) }, ` — ${Math.round(noop.other / fixed * 100)}% network and API, ${Math.round(noop.queue / fixed * 100)}% queue, ${Math.round(noop.startup / fixed * 100)}% guest start-up.`,
        ...(fs && fs.startup > noop.startup ? [' A call that writes adds about ', { b: C.fmtMs(fs.startup - noop.startup) }, ' — Archil\'s exit commit (~70 ms in a shell-only test) plus the guest\'s own cleanup.'] : [])]);
    }

    put('slowdownChart', C.dotCI(V.slowdown(data), { fmt: C.fmtX, ref: 1, sideLabels: ['faster than local', 'slower than local'] }), 'Needs both local and Archil runs of the same task.');
    put('ecdfChart', C.ecdf(V.ecdf(data, state.task), { title: 'ECDF of end-to-end latency' }));

    const fan = V.fanout(data);
    put('fanoutChart', C.stripPlot(fan, { title: 'Fan-out' }), 'No fan-out runs in this filter.');
    $('fanoutInsight').replaceChildren();
    if (fan.length > 1) {
      const meds = fan.map(g => C.median(g.lanes.flatMap(l => l.values.map(v => v.v))));
      const last = meds[meds.length - 1], change = last / meds[0] - 1;
      append($('fanoutInsight'), ['Median moves from ', { b: C.fmtMs(meds[0]) }, ' (one call) to ', { b: C.fmtMs(last) }, ` (${fan[fan.length - 1].label}): ${Math.abs(change) < 0.15 ? 'no measurable interference at this scale' : `${change > 0 ? '+' : ''}${Math.round(change * 100)}% — concurrent calls interfere`}.`]);
    }

    put('timelineChart', C.timeline(V.timeline({ ...all, rows: all.rows.filter(r => (state.backend === 'all' || r.backend === state.backend) && (state.task === 'all' || r.task === state.task || r.task === 'image-warmup')) })), 'No remote runs loaded.');
    put('throughputChart', C.dotCI(V.throughput(data), { fmt: v => C.fmtNum(v, 'MiB/s'), legendItems: legendFor(data) }), 'No bulk-I/O tasks in this filter.');
    table(data);
  }

  const ICON = { ok: '<svg viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    fail: '<svg viewBox="0 0 12 12"><path d="M3 3l6 6M9 3 3 9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    warn: '<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M6 3.8v2.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>' };
  function table(data) {
    const body = $('rawRows'); body.replaceChildren();
    const rows = data.rows, LIMIT = 600;
    for (const r of rows.slice(0, LIMIT)) {
      const tr = el('tr');
      [V.shortVariant(r.variant), r.task + (r.fanout > 1 ? ' ×' + r.fanout : ''), String(r.trial + 1), C.fmtMs(r.client_wall_ms), C.fmtMs(r.remote_queue_ms), C.fmtMs(r.remote_execute_ms), C.fmtMs(r.work_ms)]
        .forEach((c, i) => tr.append(el('td', i >= 2 ? 'num' : null, c)));
      const kind = !r.ok ? 'fail' : r.cleanup_error ? 'warn' : 'ok';
      const st = el('span', 'status ' + kind); st.innerHTML = ICON[kind]; st.append(kind === 'fail' ? 'Failed' : kind === 'warn' ? 'Passed · cleanup' : 'Passed');
      const td = el('td'); td.append(st); tr.append(td);
      const notes = [r.error, r.cleanup_error && 'Cleanup: ' + r.cleanup_error].filter(Boolean);
      if (notes.length) tr.title = notes.join('\n');
      body.append(tr);
    }
    $('rawCount').textContent = `${rows.length.toLocaleString()} rows${rows.length > LIMIT ? ` · first ${LIMIT} shown, CSV has all` : ''} · ${rows.filter(r => r.cleanup_error).length} cleanup warnings (cross-exec EROFS)`;
  }

  // ---- filters ----
  function options(select, names, label = x => x) {
    const prev = select.value, first = select.options[0];
    select.replaceChildren(first);
    names.forEach(n => select.append(new Option(label(n), n)));
    if ([...select.options].some(o => o.value === prev)) select.value = prev;
  }
  function refresh() {
    const rows = V.prepare(reports).rows;
    options($('backendFilter'), [...new Set(rows.map(r => r.backend))].sort(), b => ({ archil: 'Archil exec', 'archil-sandbox': 'Archil sandbox', local: 'Local SSD' }[b] || b));
    options($('taskFilter'), [...new Set(rows.map(r => r.task))].filter(t => t !== 'image-warmup' && t !== 'sandbox-boot').sort((a, b) => C.taskRank(a) - C.taskRank(b)));
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
      } catch (err) { $('rawCount').textContent = `${f.name}: ${err.message}`; }
    }
    refresh();
  });
  $('exportBtn').addEventListener('click', () => {
    const cols = ['backend', 'variant', 'run_id', 'task', 'trial', 'fanout', 'parallel', 'ok', 'started_ms', 'client_wall_ms', 'remote_total_ms', 'remote_queue_ms', 'remote_execute_ms', 'work_ms', 'operations', 'bytes', 'error', 'cleanup_error'];
    const q = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
    const csv = [cols.join(','), ...filtered(V.prepare(reports)).rows.map(r => cols.map(k => q(r[k])).join(','))].join('\n');
    const a = el('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'diskscope-observations.csv'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });

  // ---- flamegraph: probe a committed flamegraph.svg (works on Pages and from file://) ----
  $('flameInput').addEventListener('change', e => {
    const f = e.target.files && e.target.files[0];
    if (f && /\.svg$/i.test(f.name)) { const o = $('flameObject'); o.data = URL.createObjectURL(f); o.hidden = false; o.classList.remove('probing'); $('flameEmpty').hidden = true; }
  });
  (() => {
    const probe = $('flameObject');
    probe.hidden = false; probe.classList.add('probing'); // display:none objects are never loaded
    probe.addEventListener('load', () => {
      const doc = probe.contentDocument, root = doc && doc.documentElement;
      if (root && root.nodeName.toLowerCase() !== 'svg') { probe.hidden = true; return; }
      probe.classList.remove('probing'); $('flameEmpty').hidden = true;
      const w = root && parseFloat(root.getAttribute('width')), h = root && parseFloat(root.getAttribute('height'));
      if (w > 0 && h > 0) { const fit = () => { probe.style.height = Math.round(probe.clientWidth * h / w) + 'px'; }; fit(); addEventListener('resize', fit); }
    }, { once: true });
    probe.addEventListener('error', () => { probe.hidden = true; probe.classList.remove('probing'); }, { once: true });
    probe.data = 'flamegraph.svg';
  })();

  refresh();
})();

#!/usr/bin/env node
// Render static, self-contained SVG charts for the README from DiskScope JSON runs, using the exact
// chart + view code the dashboard uses (so the README and the dashboard can never disagree).
//
//   node scripts/readme-charts.mjs data/*.json            # writes docs/assets/*.svg, prints a markdown table
//
// The SVGs embed their own light/dark palette via prefers-color-scheme, contain no scripts and no
// webfonts, so GitHub renders them safely through <img>.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const C = require(join(root, 'dashboard/charts.js'));
const V = require(join(root, 'dashboard/views.js'));
const themeCss = readFileSync(join(root, 'dashboard/chart-theme.css'), 'utf8')
  // GitHub's <img> sandbox has no data-theme toggle; keep only the OS-preference rules.
  .replace(/:root\[data-theme="dark"\][^}]*}/, '')
  .replace(/:root:not\(\[data-theme="light"\]\)/g, ':root');

const files = process.argv.slice(2);
if (!files.length) { console.error('usage: node scripts/readme-charts.mjs <run.json>...'); process.exit(2); }
const data = V.prepare(files.map(f => JSON.parse(readFileSync(f, 'utf8'))));
const outDir = join(root, 'docs/assets');
mkdirSync(outDir, { recursive: true });

function standalone(svg, title) {
  if (!svg) return null;
  const h = Number(/viewBox="0 0 \d+ (\d+(?:\.\d+)?)"/.exec(svg)[1]);
  const pad = 24, W = 960 + pad * 2, H = h + pad * 2 + 30;
  const inner = svg
    .replace(/ data-tip="[^"]*"/g, '').replace(/ tabindex="0"/g, '')
    .replace(/^<svg[^>]*>/, `<svg x="${pad}" y="${pad + 30}" width="960" height="${h}" viewBox="0 0 960 ${h}" class="ds-chart">`);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${C.esc(title)}">
<style>${themeCss}</style>
<rect width="${W}" height="${H}" rx="14" fill="var(--surface)" stroke="var(--border)"/>
<text x="${pad}" y="${pad + 12}" class="ds-label" style="font-size:16px;font-family:system-ui,-apple-system,'Segoe UI',sans-serif">${C.esc(title)}</text>
${inner}
</svg>
`;
}

function heroCard(h) {
  const W = 1008, H = 220, pad = 28, colW = (W - pad * 2) / 4;
  const tiles = [
    ['Warm tool call', h.noop ? C.fmtMs(h.noop.median) : '—', h.noop ? `median end-to-end · n=${h.noop.n}` : ''],
    ['Queue · VM · mount', C.fmtMs(h.queue), 'median Archil-reported'],
    ['Durable fsync', h.fsync ? C.fmtX(h.fsync.v) : '—', 'Archil ÷ local SSD'],
    h.parallel ? [`${h.parallel.parallel} parallel microVMs`, h.parallel.speedup.toFixed(1) + '×', `${h.parallel.calls} calls in ${h.parallel.seconds.toFixed(0)} s`]
      : ['SQLite commit', h.sqlite ? C.fmtX(h.sqlite.v) : '—', 'Archil ÷ local SSD'],
  ];
  const font = "font-family:system-ui,-apple-system,'Segoe UI',sans-serif";
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="DiskScope headline results">
<style>${themeCss} .k{fill:var(--muted);font-size:13px;font-weight:600;${font}} .v{fill:var(--ink);font-size:40px;font-weight:700;letter-spacing:-0.5px;${font}} .d{fill:var(--ink-2);font-size:13px;${font}} .t{fill:var(--ink);font-size:20px;font-weight:700;${font}} .e{fill:var(--muted);font-size:12px;font-weight:600;letter-spacing:1.5px;${font}}</style>
<rect width="${W}" height="${H}" rx="16" fill="var(--surface)" stroke="var(--border)"/>
<circle cx="${pad + 10}" cy="${pad + 10}" r="9" fill="none" stroke="var(--s1)" stroke-width="2"/><circle cx="${pad + 10}" cy="${pad + 10}" r="3" fill="var(--s1)"/>
<text x="${pad + 28}" y="${pad + 16}" class="t">DiskScope</text>
<text x="${W - pad}" y="${pad + 15}" class="e" text-anchor="end">ARCHIL ${C.esc((h.regions[0] || '').toUpperCase())} · ${h.observations} OBSERVATIONS</text>`;
  tiles.forEach(([k, v, d], i) => {
    const x = pad + i * colW;
    if (i) s += `<line x1="${x - 12}" x2="${x - 12}" y1="${pad + 52}" y2="${H - pad}" stroke="var(--grid)"/>`;
    s += `<text x="${x}" y="${pad + 72}" class="k">${C.esc(k)}</text><text x="${x}" y="${pad + 122}" class="v">${C.esc(v)}</text><text x="${x}" y="${pad + 148}" class="d">${C.esc(d)}</text>`;
  });
  return s + '</svg>\n';
}

const legend = [...data.slots].filter(([b]) => data.rows.some(r => r.backend === b)).map(([b, s]) => ({ label: b, slot: s, kind: 'dot' }));
const outputs = {
  'hero.svg': heroCard(V.headline(data)),
  'vs-local.svg': standalone(C.dotCI(V.slowdown(data), { fmt: C.fmtX, ref: 1, sideLabels: ['Archil faster', 'Archil slower'] }), 'Archil ÷ local disk — median guest work time, 95% bootstrap CI (log scale)'),
  'latency.svg': standalone(C.stripPlot(V.atlas(data, 'client_wall_ms'), { legendItems: legend }), 'End-to-end latency per call — dots are calls, tick = median, band = CI (log scale)'),
  'anatomy.svg': standalone(C.anatomy(V.anatomy(data)), 'Anatomy of an Archil call — median time per phase'),
  'timeline.svg': standalone(C.timeline(V.timeline(data)), 'Every Archil call in submission order (log scale)'),
};
for (const [name, svg] of Object.entries(outputs)) {
  if (!svg) { console.error(`skip ${name}: no data`); continue; }
  writeFileSync(join(outDir, name), svg);
  console.error(`wrote docs/assets/${name} (${(svg.length / 1024).toFixed(0)} KiB)`);
}
console.log(V.markdownTable(data));

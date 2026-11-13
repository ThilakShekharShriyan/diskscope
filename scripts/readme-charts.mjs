#!/usr/bin/env node
// Render static, self-contained SVG charts for the README from DiskScope JSON runs, using the exact
// chart + view code the dashboard uses (so the README and the dashboard can never disagree).
//
//   node scripts/readme-charts.mjs data/*.json data/archive/v0.1/2026-11-13-archil-sequential.json
//
// Writes docs/assets/*.svg and prints the README results table. The SVGs embed their own palette,
// contain no scripts and no webfonts, so GitHub renders them safely through <img>.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const C = require(join(root, 'dashboard/charts.js'));
const V = require(join(root, 'dashboard/views.js'));
const themeCss = readFileSync(join(root, 'dashboard/chart-theme.css'), 'utf8');
const FONT = "font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif";

const files = process.argv.slice(2);
if (!files.length) { console.error('usage: node scripts/readme-charts.mjs <run.json>...'); process.exit(2); }
const data = V.prepare(files.map(f => JSON.parse(readFileSync(f, 'utf8'))));
const outDir = join(root, 'docs/assets');
mkdirSync(outDir, { recursive: true });

function standalone(svg, title, subtitle = '') {
  if (!svg) return null;
  const h = Number(/viewBox="0 0 \d+ (\d+(?:\.\d+)?)"/.exec(svg)[1]);
  const pad = 28, top = subtitle ? 64 : 46, W = 960 + pad * 2, H = h + top + pad;
  const inner = svg
    .replace(/ data-tip="[^"]*"/g, '').replace(/ tabindex="0"/g, '')
    .replace(/^<svg[^>]*>/, `<svg x="${pad}" y="${top}" width="960" height="${h}" viewBox="0 0 960 ${h}" class="ds-chart">`);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${C.esc(title)}">
<style>${themeCss}</style>
<rect x="0.5" y="0.5" width="${W - 1}" height="${H - 1}" rx="10" fill="var(--surface)" stroke="var(--grid)"/>
<text x="${pad}" y="${pad + 8}" style="fill:var(--ink);font-size:15px;font-weight:600;${FONT}">${C.esc(title)}</text>
${subtitle ? `<text x="${pad}" y="${pad + 28}" style="fill:var(--muted);font-size:12px;${FONT}">${C.esc(subtitle)}</text>` : ''}
${inner}
</svg>
`;
}

function heroCard(h) {
  const W = 1016, H = 196, pad = 32, colW = (W - pad * 2) / 4;
  const tiles = [
    ['Serverless call', h.noop ? C.fmtMs(h.noop.median) : '—', h.v01Noop ? `no-op, default image · was ${C.fmtMs(h.v01Noop.median)}` : 'no-op, default image'],
    ['Warm sandbox worker', h.sandboxNoop ? C.fmtMs(h.sandboxNoop.median) : '—', Number.isFinite(h.sandboxBoot) ? `no-op · one-time boot ${C.fmtMs(h.sandboxBoot)}` : 'no-op'],
    ['Queue · VM · mount', C.fmtMs(h.queue), Number.isFinite(h.slimQueue) ? `warm pool · ${C.fmtMs(h.slimQueue)} custom image` : 'Archil-reported'],
    ['Durable fsync vs local', h.fsync ? C.fmtX(h.fsync.v) : '—', 'guest-measured, 4 KiB'],
  ];
  let s = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="DiskScope headline results">
<style>${themeCss} .k{fill:var(--muted);font-size:12.5px;${FONT}} .v{fill:var(--ink);font-size:38px;font-weight:600;letter-spacing:-1px;${FONT}} .d{fill:var(--ink-2);font-size:12.5px;${FONT}} .t{fill:var(--ink);font-size:17px;font-weight:650;letter-spacing:-.2px;${FONT}} .e{fill:var(--muted);font-size:11px;font-weight:600;letter-spacing:1.6px;${FONT}}</style>
<rect x="0.5" y="0.5" width="${W - 1}" height="${H - 1}" rx="10" fill="var(--surface)" stroke="var(--grid)"/>
<circle cx="${pad + 8}" cy="${pad + 7}" r="7.5" fill="none" stroke="var(--ink)" stroke-width="1.4"/><circle cx="${pad + 8}" cy="${pad + 7}" r="2.2" fill="var(--ink)"/>
<text x="${pad + 24}" y="${pad + 13}" class="t">DiskScope</text>
<text x="${W - pad}" y="${pad + 12}" class="e" text-anchor="end">ARCHIL ${C.esc((h.regions[0] || '').toUpperCase())} · ${h.observations} OBSERVATIONS</text>
<line x1="${pad}" x2="${W - pad}" y1="${pad + 34}" y2="${pad + 34}" stroke="var(--ink)" stroke-width="1"/>`;
  tiles.forEach(([k, v, d], i) => {
    const x = pad + i * colW + (i ? 20 : 0);
    if (i) s += `<line x1="${x - 20}" x2="${x - 20}" y1="${pad + 34}" y2="${H - pad + 4}" stroke="var(--grid)"/>`;
    s += `<text x="${x}" y="${pad + 62}" class="k">${C.esc(k)}</text><text x="${x}" y="${pad + 106}" class="v">${C.esc(v)}</text><text x="${x}" y="${pad + 128}" class="d">${C.esc(d)}</text>`;
  });
  return s + '</svg>\n';
}

const h = V.headline(data);
const outputs = {
  'hero.svg': heroCard(h),
  'ladder.svg': standalone(C.stripPlot(V.ladder(data)), 'Optimisation ladder — no-op and 4 KiB fsync, end-to-end', 'Each dot is one call; tick = median; band = distribution-free CI. Log scale.'),
  'vs-local.svg': standalone(C.dotCI(V.slowdown(data), { fmt: C.fmtX, ref: 1, sideLabels: ['faster than local', 'slower than local'] }), 'Archil ÷ local SSD — median guest work time', 'Same Python operations; local uses durable F_FULLFSYNC. Whiskers: ≈95% bootstrap CI. Log scale.'),
  'anatomy.svg': standalone(C.anatomy(V.anatomy(data)), 'Anatomy of a serverless call — median time per phase', 'Default image per task, then a no-op on each other exec configuration.'),
  'latency.svg': standalone(C.stripPlot(V.atlas(data, 'client_wall_ms')), 'End-to-end latency per call, every configuration', 'Dots are calls; tick = median; band = CI. Log scale.'),
  'timeline.svg': standalone(C.timeline(V.timeline(data)), 'Every remote call in submission order', 'Log scale; open circles are warm-up calls.'),
};
for (const [name, svg] of Object.entries(outputs)) {
  if (!svg) { console.error(`skip ${name}: no data`); continue; }
  writeFileSync(join(outDir, name), svg);
  console.error(`wrote docs/assets/${name} (${(svg.length / 1024).toFixed(0)} KiB)`);
}
console.log(V.markdownTable(data));

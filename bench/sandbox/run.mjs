#!/usr/bin/env node
// Warm-sandbox benchmark: run the exact DiskScope guest inside ONE persistent Archil sandbox, two ways:
//
//   exec   — `sandbox.exec(cmd)` per task: warm VM, but a fresh process (and process connection) per task.
//   serve  — one long-lived `guest_worker.py --serve` process; tasks are written to its stdin over a single
//            open connection. No per-task VM, interpreter start, or connection setup: the warmest path.
//
//   cd bench/sandbox && npm ci
//   set -a; source ../../.env.local; set +a      # ARCHIL_API_KEY, ARCHIL_DISK_ID, ARCHIL_REGION
//   node run.mjs --runs 10 --output ../../results/sandbox      # writes sandbox-exec.json, sandbox-serve.json
//
// The sandbox mounts the disk (a plain mount is EXCLUSIVE while it runs) and is always stopped and deleted
// at the end; the mounted disk and its data are kept. Output: DiskScope RunReports, backend "archil-sandbox".
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import * as archil from 'disk';

const here = dirname(fileURLToPath(import.meta.url));
const WORKER = readFileSync(join(here, '../../src/guest_worker.py'), 'utf8');
const TASKS = ['noop', 'stat', 'small-files', 'fsync-4k', 'seq-write', 'seq-read', 'sqlite-commit', 'hash-64m'];
const MARKER = 'DISKSCOPE_JSON=', ERROR_MARKER = 'DISKSCOPE_ERROR=';
const IMAGE = 'python:3.13-slim';

const arg = (name, dflt) => { const i = process.argv.indexOf('--' + name); return i > 0 ? process.argv[i + 1] : dflt; };
const runs = Number(arg('runs', 10)), sizeMib = Number(arg('size-mib', 4));
const modes = arg('modes', 'exec,serve').split(',');
const outPrefix = arg('output', join(here, '../../results/sandbox'));
const { ARCHIL_API_KEY: apiKey, ARCHIL_DISK_ID: diskId, ARCHIL_REGION: region = 'aws-us-east-1' } = process.env;
if (!apiKey || !/^dsk-[0-9a-f]{16}$/.test(diskId || '')) { console.error('Set ARCHIL_API_KEY and ARCHIL_DISK_ID (dsk- + 16 hex)'); process.exit(2); }
if (!(runs >= 1 && runs <= 20) || !(sizeMib >= 1 && sizeMib <= 64) || !modes.every(m => m === 'exec' || m === 'serve')) {
  console.error('--runs 1..20, --size-mib 1..64, --modes exec,serve'); process.exit(2);
}

const quote = s => `'${s.replace(/'/g, `'\\''`)}'`;
const stamp = `${process.pid}-${Math.floor(Date.now() / 1000)}`;
const scratch = (scope, task, trial) => `/mnt/archil/.diskscope/${scope}/${task}-${trial}-1`;
const seed = (Date.now() ^ process.pid) >>> 0;
function shuffled(items, s) {
  let a = s;
  const r = () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const x = [...items];
  for (let i = x.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [x[i], x[j]] = [x[j], x[i]]; }
  return x;
}
const jobs = shuffled(TASKS.flatMap(t => Array.from({ length: runs }, (_, i) => [t, i])), seed);
const obs = (task, trial) => ({ task, trial, fanout: 1, ok: false, client_wall_ms: 0, remote_total_ms: null, remote_queue_ms: null, remote_execute_ms: null, work_ms: null, operations: null, bytes: null, error: null, cleanup_error: null, started_ms: null, parallel: 1 });
function newReport(mode, scope) {
  return {
    schema_version: 1, backend: 'archil-sandbox', run_id: scope, timestamp_unix_secs: Math.floor(Date.now() / 1000),
    environment: { region, disk_id_suffix: diskId.slice(-4), host_os: process.platform, workload_language: 'Python 3 standard library (guest)', image: IMAGE,
      size_mib: sizeMib, runs_per_task: runs, parallel: 1, shuffle_seed: seed, diskscope_version: '0.2.0',
      variant: mode === 'exec' ? 'sandbox · exec per task' : 'sandbox · persistent worker',
      notes: (mode === 'exec'
        ? 'One warm Archil sandbox (1 vCPU, 2 GiB) mounting the disk; each task is a new `python3 -S` process started with sandbox.exec(), which opens a new process connection per call.'
        : 'One warm Archil sandbox (1 vCPU, 2 GiB) mounting the disk; a single long-lived `guest_worker.py --serve` process receives each task on stdin over one open connection.')
        + ' client_wall_ms is the client round-trip per task. No server-side timing exists for sandbox processes.' },
    observations: [],
  };
}
function applyLine(o, line) {
  if (line.startsWith(MARKER)) {
    const w = JSON.parse(line.slice(MARKER.length));
    Object.assign(o, { work_ms: w.elapsed_ms, operations: w.operations, bytes: w.bytes, ok: !!w.valid, cleanup_error: w.cleanup_error ?? null });
    if (!w.valid) o.error = 'Guest post-run assertion failed';
  } else if (line.startsWith(ERROR_MARKER)) o.error = line.slice(ERROR_MARKER.length).slice(0, 600);
}
const log = (k, n, o) => console.log(`[${String(k + 1).padStart(3)}/${n}] ${o.task.padEnd(14)} trial ${String(o.trial + 1).padStart(2)} ${o.client_wall_ms.toFixed(1).padStart(9)} ms  ${o.ok ? 'OK' : 'FAIL ' + (o.error || '')}`);

async function runExec(sandbox, report) {
  const scope = report.run_id, t0 = performance.now();
  const command = (task, trial) => `python3 -S -c ${quote(WORKER)} ${quote(task)} ${quote(scratch(scope, task, trial))} ${sizeMib}`;
  for (let i = 0; i < 3; i++) {
    const w = obs('image-warmup', i); w.started_ms = performance.now() - t0; const s = performance.now();
    const r = await sandbox.exec(command('noop', 900 + i));
    w.client_wall_ms = performance.now() - s; w.ok = r.exitCode === 0; report.observations.push(w);
  }
  for (const [k, [task, trial]] of jobs.entries()) {
    const o = obs(task, trial); o.started_ms = performance.now() - t0; const s = performance.now();
    const r = await sandbox.exec(command(task, trial), { timeoutSeconds: 300 });
    o.client_wall_ms = performance.now() - s;
    const line = (r.stdout || '').split('\n').find(l => l.startsWith(MARKER));
    if (line) applyLine(o, line);
    if (r.exitCode !== 0) { o.ok = false; o.error = `exit ${r.exitCode}: ${(r.stderr || '').slice(-600)}`; }
    else if (!line) o.error = 'missing DISKSCOPE_JSON line';
    report.observations.push(o); log(k, jobs.length, o);
  }
}

async function runServe(sandbox, report) {
  const scope = report.run_id, t0 = performance.now();
  let buffer = '', waiter = null;
  const decoder = new TextDecoder();
  const proc = await sandbox.run(`python3 -S -u -c ${quote(WORKER)} --serve`, {
    collectOutput: false,
    onOutput: ({ stream, data }) => {
      if (stream !== 'stdout') return;
      buffer += decoder.decode(data, { stream: true });
      let nl;
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl); buffer = buffer.slice(nl + 1);
        if (waiter && (line.startsWith(MARKER) || line.startsWith(ERROR_MARKER))) { const w = waiter; waiter = null; w(line); }
      }
    },
  });
  const ask = (task, trial) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => { waiter = null; reject(new Error(`timeout waiting for ${task}`)); }, 300_000);
    waiter = line => { clearTimeout(timer); resolve(line); };
    proc.sendInput(`${task} ${scratch(scope, task, trial)} ${sizeMib}\n`).catch(reject);
  });
  try {
    for (let i = 0; i < 3; i++) { // first request also pays interpreter start-up inside the sandbox
      const w = obs('image-warmup', i); w.started_ms = performance.now() - t0; const s = performance.now();
      applyLine(w, await ask('noop', 900 + i)); w.client_wall_ms = performance.now() - s; report.observations.push(w);
    }
    for (const [k, [task, trial]] of jobs.entries()) {
      const o = obs(task, trial); o.started_ms = performance.now() - t0; const s = performance.now();
      applyLine(o, await ask(task, trial));
      o.client_wall_ms = performance.now() - s;
      report.observations.push(o); log(k, jobs.length, o);
    }
  } finally {
    await proc.closeStdin().catch(() => {});
    await Promise.race([proc.wait(), new Promise(r => setTimeout(r, 5000))]).catch(() => {});
  }
}

const client = new archil.Archil({ apiKey, region });
const reports = Object.fromEntries(modes.map(m => [m, newReport(m, `sbx-${m}-${stamp}`)]));
let sandbox, boot;
try {
  const b0 = performance.now();
  sandbox = await client.sandboxes.create({ name: `diskscope-${stamp}`, baseImage: IMAGE, vcpuCount: 1, memSizeMiB: 2048, idleTtlSeconds: 300, maxTtlSeconds: 1800, mounts: [{ disk: diskId }] }, { wait: true });
  boot = performance.now() - b0;
  console.log(`sandbox ${sandbox.status} after ${boot.toFixed(0)} ms`);
  if (sandbox.status !== 'running') throw new Error(`sandbox status ${sandbox.status}`);
  for (const m of modes) {
    const b = obs('sandbox-boot', 0); b.client_wall_ms = boot; b.ok = true; b.started_ms = 0;
    reports[m].observations.push(b);
    console.log(`== mode: ${m}`);
    await (m === 'exec' ? runExec : runServe)(sandbox, reports[m]);
  }
} catch (err) {
  const msg = String((err && err.message) || err).split(diskId).join('dsk-…');
  for (const r of Object.values(reports)) r.environment.notes += ` RUN ABORTED: ${msg}`;
  console.error('aborted:', msg);
  process.exitCode = 1;
} finally {
  if (sandbox) {
    try { await sandbox.stop(); await sandbox.delete(); console.log('sandbox stopped and deleted (disk data kept)'); }
    catch (err) { console.error(`CLEANUP FAILED — delete sandbox ${sandbox.id} manually:`, (err && err.message) || err); process.exitCode = 1; }
  }
  mkdirSync(dirname(outPrefix), { recursive: true });
  for (const [m, r] of Object.entries(reports)) {
    const file = `${outPrefix}-${m}.json`;
    writeFileSync(file, JSON.stringify(r, null, 2));
    console.log(`saved ${r.observations.length} observations -> ${file}`);
  }
}

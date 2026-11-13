<p align="center">
  <a href="https://thilakshekharshriyan.github.io/diskscope/"><img src="docs/assets/hero.svg" alt="DiskScope headline results: serverless call 157 ms, warm sandbox worker 77 ms, queue 33 ms, durable fsync 1.4x local" width="100%"></a>
</p>

# DiskScope

**Reproducible microbenchmarks for serverless microVMs and network filesystems — a Rust harness, measured on [Archil](https://archil.com).**

[Dashboard](https://thilakshekharshriyan.github.io/diskscope/) · [Results](#results) · [Findings](#platform-findings) · [Method](#methodology) · [Run it](#run-it-yourself)

[![CI](https://img.shields.io/github/actions/workflow/status/ThilakShekharShriyan/diskscope/ci.yml?branch=main&style=flat-square&label=ci&color=161513&labelColor=4b4842)](https://github.com/ThilakShekharShriyan/diskscope/actions/workflows/ci.yml)
![Rust](https://img.shields.io/badge/rust-stable-161513?style=flat-square&labelColor=4b4842)
![Observations](https://img.shields.io/badge/observations-510-161513?style=flat-square&labelColor=4b4842)
![Region](https://img.shields.io/badge/archil-aws--us--east--1-161513?style=flat-square&labelColor=4b4842)
![License](https://img.shields.io/badge/license-MIT-161513?style=flat-square&labelColor=4b4842)

An agent's tool call increasingly runs as a fresh microVM attached to a shared disk. What does that cost per call, per `fsync`, per database commit — and how much of it can you engineer away? DiskScope measures every call, publishes the raw data, and reports medians with distribution-free confidence intervals.

## Results

<p align="center"><img src="docs/assets/ladder.svg" alt="Optimisation ladder: no-op and 4 KiB fsync end-to-end latency for each configuration" width="100%"></p>

| No-op, end-to-end (median, n = 5–10) | |
|---|--:|
| Serverless exec · `python:3.13-slim` · v0.1 guest | 349 ms |
| Serverless exec · `python:3.13-slim` · lazy guest | 264 ms |
| **Serverless exec · default image · lazy guest** | **157 ms** |
| Warm sandbox · `sandbox.exec()` per task | 434 ms |
| **Warm sandbox · persistent worker** | **77 ms** |

**What made it faster**

1. **Use the default exec image.** It is served from Archil's pool of ready microVMs: **33 ms** queue vs **143 ms** for any custom `baseImage`, which boots on demand. It already ships Python 3.10.
2. **Keep the guest's cold start cheap.** Every exec is a fresh VM, so Python's stdlib is read from a cold cache: importing `sqlite3`, `hashlib`, `pathlib` and `json` up front cost ~135 ms. Importing per task and running `python3 -S` cut Archil-reported execute time from 107 ms to 38 ms.
3. **For many calls, keep one process warm.** A long-lived worker inside a sandbox, fed over a single connection, answers a no-op in **77 ms** (about one network round-trip) and a durable 4 KiB write in **86 ms**, after a one-time ~2 s boot. Calling `sandbox.exec()` per task is the slowest option (434 ms) because each call opens a new process connection.

<p align="center"><img src="docs/assets/vs-local.svg" alt="Ratio of Archil to local median guest work time per task" width="100%"></p>

Guest-measured work (the timed operation only — identical Python everywhere), median, n = 10 per cell:

| Task | Local SSD | Exec, default | Sandbox worker | Exec ÷ local | Sandbox ÷ local |
|---|--:|--:|--:|--:|--:|
| `stat` — one `stat()` | 4.8 µs | 3.7 µs | 19 µs | 0.79× | 4.1× |
| `small-files` — 64 × 16 B | 6.37 ms | 11.0 ms | 3.32 ms | 1.7× | **0.52×** |
| `fsync-4k` — 4 KiB + fsync | 4.77 ms | 6.76 ms | 7.25 ms | **1.4×** | 1.5× |
| `seq-write` — 4 MiB + fsync | 7.17 ms | 40.6 ms | 21.0 ms | 5.7× | 2.9× |
| `seq-read` — warm re-read | 455 µs | 2.27 ms | 1.69 ms | 5.0× | 3.7× |
| `sqlite-commit` — 25 × FULL | 27.9 ms | 320 ms | 298 ms | **11×** | 11× |
| `hash-64m` — CPU control | 41.8 ms | 30.7 ms | 30.5 ms | 0.73× | 0.73× |

Approximate 95% intervals for every ratio are on the [dashboard](https://thilakshekharshriyan.github.io/diskscope/#slowdown). The local baseline is this Mac's SSD with durable flushes (`F_FULLFSYNC`); its CPU speed varies with load, so `hash-64m` is context, not a result.

- **A single durable fsync is close to local** (1.4×). Plain macOS `fsync` doesn't flush the drive cache and returns in ~0.15 ms — compare against that and Archil looks 40× slower, which is wrong.
- **Commit-heavy work compounds:** SQLite with `synchronous=FULL` is ~11× slower (≈13 ms per commit vs ≈1.1 ms).
- **Every exec that writes pays ~110–120 ms at exit** for Archil's commit — the *start-up · commit* segment below.
- **Small-file metadata is fastest inside a sandbox** (0.52× local), whose exclusive mount can cache locally.
- **Fan-out has a cost at 4:** four concurrent `stat()` calls raised the median from 157 ms to 213 ms; two at once showed no change.

<p align="center"><img src="docs/assets/anatomy.svg" alt="Median queue, start-up and commit, work and network time per serverless call" width="100%"></p>

<details>
<summary><b>Every call, plotted</b></summary>
<p align="center"><img src="docs/assets/latency.svg" alt="Dot plot of every call's end-to-end latency per task and configuration" width="100%"></p>
<p align="center"><img src="docs/assets/timeline.svg" alt="Every remote call in submission order" width="100%"></p>
</details>

## Platform findings

Reported to Archil with minimal curl repros in [`ARCHIL-FINDINGS.md`](ARCHIL-FINDINGS.md):

1. **Cross-exec deletes fail with `EROFS`.** An exec cannot unlink or rmdir anything inside a directory created by an earlier exec — even a file it created itself. Inside one sandbox the same workload cleans up fine.
2. **SQLite databases break across execs.** A database created in one exec fails the next exec's commit (`disk I/O error`) and is then unreadable. `PRAGMA journal_mode=TRUNCATE` avoids it.
3. **Exit commit** costs ~120 ms per writing exec; **`sandbox.exec()`** costs ~430 ms per call; the docs omit the custom-image queue cost and the default image's contents.

## Methodology

Borrowed from [criterion.rs](https://bheisler.github.io/criterion.rs/book/analysis.html), [hyperfine](https://github.com/sharkdp/hyperfine), [fio](https://fio.readthedocs.io/), [ClickBench](https://github.com/ClickHouse/ClickBench) and Hoefler & Belli's [*Scientific Benchmarking of Parallel Computing Systems*](https://htor.inf.ethz.ch/publications/img/hoefler-scientific-benchmarking.pdf).

- **Same code everywhere.** One standard-library Python guest ([`src/guest_worker.py`](src/guest_worker.py)) runs locally, in serverless exec, and in a sandbox. Cross-environment ratios use only its own `perf_counter_ns` interval.
- **Fair durability.** Local `fsync` uses `F_FULLFSYNC`; SQLite uses `fullfsync`.
- **Honest statistics.** Medians with order-statistic intervals (n = 10 → (x₂, x₉), 97.9% coverage); ratios with a seeded bootstrap, labelled approximate; no outliers removed; no p99 from small samples.
- **Configurations are never pooled.** Every run records its variant, shuffle seed, image and parallelism; warm-up and sandbox boot are recorded but excluded.
- **Limits.** One region, one client, one disk, n = 5–10 per cell. Fan-out is closed-loop. `seq-read` is warm-cache. A sandbox's plain mount holds the disk exclusively while it runs.

## Run it yourself

Rust stable and Python 3.9+; for remote runs an Archil API key and disk (`aws-us-east-1`, `aws-us-west-2` or `aws-eu-west-1`). Put `ARCHIL_API_KEY`, `ARCHIL_DISK_ID` and `ARCHIL_REGION` in `.env.local` (gitignored).

```bash
cargo test && python3 -m unittest discover -s tests

cargo run --release -- local  --runs 10 --output results/local.json
set -a; source .env.local; set +a
cargo run --release -- archil --runs 10 --fanout 1,2,4 --output results/archil.json         # default image
cargo run --release -- archil --runs 5 --image python:3.13-slim --output results/slim.json     # custom image
(cd bench/sandbox && npm ci && node run.mjs --runs 10 --output ../../results/sandbox)        # warm sandbox

cargo run --release -- report --input results/*.json --output docs/index.html
node scripts/readme-charts.mjs results/*.json
```

**Guardrails:** never creates or deletes disks; allowlisted region hosts; validated disk IDs and image names; scratch files only under `/mnt/archil/.diskscope/<run>/`; at most 200 calls per run, estimate printed first; stops on HTTP 401/403/404/429; the sandbox benchmark always stops and deletes its sandbox; results keep only the last four characters of the disk ID.

**Flamegraph:** `cargo install inferno --locked && ./scripts/flamegraph.sh` on macOS without Xcode, or `cargo install flamegraph --locked` on Linux. It profiles the harness's own synthetic workload, not Archil.

## Layout

```
src/                 Rust CLI (local · archil · report · profile-workload) and the Python guest
bench/sandbox/       warm-sandbox benchmark (official `disk` SDK, pinned)
dashboard/           chart kernel, view models and page, rendered by `diskscope report`
scripts/             README charts, flamegraph
data/                published raw runs (v0.2); data/archive/v0.1 holds the first runs
docs/                GitHub Pages: dashboard, README charts, flamegraph
```

MIT licensed. Independent research — not affiliated with Archil.

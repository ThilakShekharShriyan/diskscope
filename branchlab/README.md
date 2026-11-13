# BranchLab (DiskScope v0.2 add-on)

**Safe, reproducible cross-process / cross-microVM state correctness experiments.**

This is an **additive subdirectory**, not a replacement for your existing DiskScope repo. It emits the existing DiskScope `schema_version: 1` JSON format, so the original `diskscope report` generator can import it.

## What it tests

Six cases; every step is its own command execution (a fresh Archil microVM, or a fresh local process):

1. **handoff** — write a file with `fsync(file)` + `fsync(dir)`; verify every byte from a new execution.
2. **sqlite** — 16 `synchronous=FULL` commits; reopen read-only elsewhere, check `integrity_check`, row count and sum.
3. **readfan** — one writer, then several concurrent fresh readers of the same file.
4. **writers** — concurrent writers to separate, pre-created directories, then a reconcile read. No shared-file races.
5. **semantics** — one execution creates entries; a later one tries ten operations on them (overwrite, append,
   create, `mkdir`, `chmod`, rename, write-temp-then-rename, unlink ×2, SQLite create), once plain and once after
   `archil checkout` of the folder. Records each outcome (`ok` / errno).
6. **visibility** (opt-in) — one long-lived reader primes its caches (negative `open`/`stat`, listings, attributes,
   an open fd), then polls each path in its own directory; a separate execution creates and overwrites files after
   `--writer-delay-secs`. Records when each path first sees the change, up to `--cap-secs` (≤ 280 s, under Archil's
   5-minute exec limit). A trial is invalid unless the reader primed before the writer started.

Default cases: `handoff,sqlite,readfan,writers,semantics`; add `--cases visibility` explicitly — each trial holds a
reader exec open for up to `--cap-secs`.

Each trial ends with one `trial/cleanup` that removes all of that trial's case directories, even after a failure, and results are saved after every trial.

Every step records client wall time, Archil `queueMs` / `executeMs` / `totalMs`, the guest's own timing, and PASS/FAIL.

## Safety

- Uses an existing **dedicated empty benchmark disk**; never creates or deletes disks.
- Uses `/mnt/archil/.diskscope/branchlab-<unique-run-id>` for all remote files.
- Guest deletion is restricted to a verified `branchlab-...` scratch folder directly under `.diskscope`; it will never delete the disk, the `.diskscope` parent, or arbitrary paths.
- No disk checkpoints, branches, host termination, `--force` ownership takeovers, arbitrary shell commands, or shared-file writer races.
- Defaults: 2 repetitions, 4 KiB payload, 2 concurrent workers, 38 API calls in a full run (19 per trial, with one cleanup per trial). Hard cap: 200 estimated calls.
- Cleanup is attempted even if a test fails. **A network failure can leave scratch data behind**; remove it with a `conditional` mount via `POST /api/exec`.
- Results may contain runtime error text; inspect the generated dashboard and JSON before publishing. Don't commit API keys, `.env` files, or secrets.
- A successful cross-VM read after `fsync` is not proof of resilience to an AZ outage or permanent host loss; no such fault is injected.

## Run it

From the repository root:

```bash
python3 -m unittest discover -s branchlab/tests -v
cargo test --manifest-path branchlab/Cargo.toml
cargo run --release --manifest-path branchlab/Cargo.toml -- local --runs 2 --size-kib 4 --fanout 2 --output results/state-local.json

set -a; source .env.local; set +a        # ARCHIL_API_KEY, ARCHIL_DISK_ID, ARCHIL_REGION (all required)
cargo run --release --manifest-path branchlab/Cargo.toml -- archil --runs 1 --size-kib 4 --fanout 2 --output results/state-archil.json

python3 branchlab/report.py results/state-local.json results/state-archil.json --output docs/branchlab.html
```

The report is a separate page (`docs/branchlab.html`); don't feed these JSON files to `diskscope report`, whose views don't know the `local-state` / `archil-state` backends. Published runs live in `data/branchlab/`.

### Archil-specific choices

- **Default image**, not `python:3.13-slim`: warm-pool queue of ~30 ms versus an on-demand boot that is sometimes 1.3–3.2 s.
- **Cleanup through `POST /api/exec` with a `conditional` mount.** A default exec can't unlink entries in directories an earlier exec created (`EROFS`; ARCHIL-FINDINGS.md #1), so a plain `rmtree` would leave the scratch directory behind.
- **SQLite with `journal_mode=TRUNCATE`.** `DELETE` (the default) and `WAL` fail across execs for the same reason (ARCHIL-FINDINGS.md #2).

## Interpretation

- `handoff/read-fresh-vm` success tests **fresh-execution visibility**, not the stale-cache TTL of a long-lived mounting client.
- `sqlite/reopen-fresh-vm` tests **single-writer, later-reader** SQLite persistence; it is not a multi-client transaction/concurrency benchmark.
- `writers/disjoint-path` tests simultaneous writes to separate files and may reveal runtime-level mount ownership limitations; failed attempts are evidence, not proof of data corruption.
- `remote_queue_ms` may include queueing, image setup, VM allocation, and mounts. It is **not** raw Firecracker boot time.
- The starter's Python fixture is intentionally standard-library only to avoid introducing Rust cross-compilation issues into the storage experiment. The scheduling and orchestration implementation is Rust; next upgrade is a precompiled Rust Linux guest worker.
- `client_wall_ms` includes API communication; the guest's `work_ms` isolates action timing more narrowly.

## Follow-on milestones

- **v0.3**: A long-lived mounted-client cache-coherence test, measuring negative dentry cache expiration and explicit invalidation, with clearly controlled mount modes.
- **v0.4**: Dedicated-disk checkpoint and branch test, requiring an explicit spend/cleanup plan because individual branches/checkpoints cannot yet be deleted.
- **v0.5**: SwarmScheduler: use measured operations to compare FIFO, size-aware scheduling, and multi-tenant fairness.
- **v1.0**: SwarmDB: fan-out of partitioned query scans and cost-aware aggregate execution.

## References

- [Archil exec API](https://docs.archil.com/api-reference/serverless-execution/exec-disk)
- [Archil: what it does and where it falls short](https://archil.com/guides/storage-for-agents/what-archil-does)
- [Archil: when is a write safe?](https://archil.com/guides/evaluating-storage/when-is-a-write-safe)

MIT; independently developed, not affiliated with Archil.

## Optional standalone correctness dashboard

For a dashboard focused on PASS/FAIL, cross-execution recovery latency, and per-request fanout:

```bash
python3 branchlab/report.py \
  results/state-local.json results/state-archil-smoke.json \
  --output docs/branchlab.html
open docs/branchlab.html
```

This file uses embedded raw observations and no external JavaScript libraries or network requests. Link it from your existing `docs/index.html` manually if you'd like it on the same GitHub Pages site.

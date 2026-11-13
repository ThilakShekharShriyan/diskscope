# BranchLab (DiskScope v0.2 add-on)

**Safe, reproducible cross-process / cross-microVM state correctness experiments.**

This is an **additive subdirectory**, not a replacement for your existing DiskScope repo. It emits the existing DiskScope `schema_version: 1` JSON format, so the original `diskscope report` generator can import it.

## What it actually tests

Four scenarios, each with independent command executions:

1. **Handoff:** create a file with `fsync(file)` + `fsync(directory)`; in a *new process/microVM*, verify its deterministic bytes and SHA-256-equivalent exact content check.
2. **SQLite recovery:** execute 16 `synchronous=FULL` commits, close the DB; reopen read-only in another execution, validate `PRAGMA integrity_check`, row count and sum.
3. **Fan-out reads:** create a file, launch several **new** readers concurrently, require each reader to verify the same expected bytes.
4. **Independent-path writers:** pre-create one directory per worker, have multiple independent executions write separate files concurrently; read them back and verify exact content. **No same-file concurrent writes.**

Every stage records client wall time, plus Archil `queueMs` / `executeMs` / `totalMs` when returned, and a PASS/FAIL for correctness. Per-trial stdout diagnostics are NOT published by default.

## Safety

- Uses an existing **dedicated empty benchmark disk**; never creates or deletes disks.
- Uses `/mnt/archil/.diskscope/branchlab-<unique-run-id>` for all remote files.
- Guest deletion is restricted to a verified `branchlab-...` scratch folder directly under `.diskscope`; it will never delete the disk, the `.diskscope` parent, or arbitrary paths.
- No disk checkpoints, branches, host termination, `--force` ownership takeovers, arbitrary shell commands, or shared-file writer races.
- Defaults: 2 repetitions, 4 KiB payload, 2 concurrent workers, 36 API calls in a full run. Hard cap: 120 estimated calls.
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

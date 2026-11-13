# BranchLab raw data

- `*.json` — the published runs rendered on `docs/branchlab.html` (8-trial Archil suite, local baseline with
  durable flushes, visibility trials).
- `size-1mib/` — the same suite with 1 MiB files (3 trials). Kept apart so 4 KiB and 1 MiB are never pooled.
- `archive/slow-client-network/` — a second 8-trial Archil run (152/152 passed) recorded while this client's
  network to us-east-1 was slow (TCP connect 80–200 ms). Its correctness results and Archil-reported
  `executeMs` are valid; its end-to-end times are not comparable with the main run.

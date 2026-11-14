## general
- Drill every top metric line before answering. Classify each by its per-minute shape: sustained step, single-minute blip, or jagged. Do not rank by score alone.
- A `_diskio` jump to only ~10^3–10^5 that lasts one minute is an injection artifact. It names the root-cause service but not the fault type, so decide the fault from the other metrics.
- If no metric crosses the threshold, or the only metric is a small `_diskio` blip, prefer delay. Do not guess cpu.

## cpu
- `_cpu` steps to about +16–19 cores above baseline and stays high to the end of the window. `_mem` barely moves (≤x1.5) and `_diskio` is not sustained.
- A small `_diskio` jump in the thousands alongside that `_cpu` rise still means cpu, not disk.

## mem
- `_mem` makes an abrupt step (x3–x20) within one minute of onset, with `_cpu` near +19.
- The per-minute `_mem` values swing widely (factor ≥1.5 between minutes, sometimes reaching a cap).
- Any `_diskio` is absent or a single-minute blip.

## disk
- `_diskio` sits at about 10^10 per minute in every minute after onset. This holds even when `_mem` has the bigger score, because page cache raises `_mem`.
- In a disk fault, `_mem` forms a fairly flat plateau rather than wild swings.
- If `_mem` jumps but `_diskio` is not in the digest, compare plateau flatness. Also check for rising latency in callers.

## delay
- `_latency-90`/`_latency-50` step up 10–20x into a smooth, flat plateau.
- There are no new ERROR logs, no `_socket` or `_mem` growth, and no `_cpu` change.

## loss
- Latency rises but is jagged minute to minute, with isolated spikes (e.g. 19 among 3–5).
- New ERROR templates appear in callers, such as "Unavailable", "no healthy upstream", or "request error".
- `_workload` drops, and `_socket` or `_mem` climb gradually minutes after onset, sometimes collapsing later.
- A slow `_mem` ramp starting after caller errors means loss, not mem. A mem fault shows an instant step plus a `_cpu` jump.

## socket
- `_socket` steps x2–4 at onset and stays flat-high to the end of the window.
- Any concurrent `_cpu` spike falls back to baseline within about 4–5 minutes. That fallback distinguishes socket from cpu, where `_cpu` stays high.
- `_mem` rises only x1.2–1.7.
- `_socket` growth that starts minutes after onset, together with errors and jagged latency, indicates loss instead.

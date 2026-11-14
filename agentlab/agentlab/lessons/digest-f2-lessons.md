## general
- Always drill each top metric line and read the per-minute shape: step-and-hold, wide swings, transient, or erratic. The ratio and score alone are misleading, and the highest-ranked line is not always the fault's own metric.

## cpu
- `_cpu` rises to about 19 and stays there to the end. `_mem` rises only slightly (≤x1.5), and there is no `_diskio` or `_socket` step. If `_cpu` falls back to baseline within ~5 min while `_socket` stays up, the fault is socket, not cpu.

## mem
- `_mem` jumps x5 or more, alongside a sustained `_cpu` rise to ~19.
- In the drill, memory swings widely minute to minute (e.g. 130M↔440M) or keeps hitting a cap.
- Any `_diskio` on the same service is only a one-minute blip.
- If `_mem` rises only x2–3 with no `_cpu` change, and callers log errors, the fault is loss.

## disk
- `_diskio` goes from 0 to about 1e10 per minute and holds there for the rest of the window. This means disk, even when `_mem` ranks higher (page cache inflates memory).
- With no own `_diskio` line, still suspect disk when `_mem` steps to a steady plateau and an unrelated service's `_diskio` rises about x3 at the same moment.

## delay
- `_latency-50`/`_latency-90` steps x10–20 to a flat, stable plateau, with no errors and no `_cpu`/`_mem` change. A small concurrent `_diskio` step does not change the answer.
- If only the entry service's latency and sockets rise, the fault is delay in one of its downstream callees. Do not blame the entry service.

## loss
- Latency jumps but stays erratic (e.g. 2.7, 5.8, 19.5, 4.7). The root-cause service's `_workload` roughly halves and keeps falling unevenly.
- Callers log ERROR lines such as "Unavailable", "no healthy upstream", "request error" or "failed to get…".
- `_socket` and `_mem` rises arrive minutes after onset.
- Blame the service whose workload drops or whose calls fail, not the one whose sockets grew.

## socket
- `_socket` steps up at onset and stays flat to the end (e.g. 9→22, 4→10), often with no other change.
- Any concurrent `_cpu` spike decays to baseline within ~5 min, and `_mem` rises only modestly.
- There are no caller errors or erratic latency, which separates socket from loss.

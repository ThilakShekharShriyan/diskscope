## general
- Drill every metric line for the root-cause service, not just the top-ranked one. The fault type is the metric with a sustained, step-shaped change; other metrics moving at the same time are side effects.
- A diskio line marked "from ~0" in the thousands is not disk stress by itself. If it is a single-minute spike, or repeats every ~5 minutes, ignore it.

## cpu
- `_cpu` steps up by about +15–19 absolute (e.g. 0.4→19, or 32→48) and stays flat to the end of the window. Any memory rise is small (≤x1.5).
- If a small "from ~0" diskio line comes with a sustained cpu step, the answer is cpu, not disk.

## mem
- `_mem` jumps x3–x14 and is the earliest change point. CPU rises to about 19 a few seconds later. Memory may swing wildly from minute to minute.
- Before answering mem, drill `_diskio`. A huge sustained diskio means disk, not mem.

## disk
- `_diskio` per minute rises to about 1e9–1e10 and stays there (x100+). This comes with a large mem jump (page cache) and CPU near 19. Answer disk even if mem has the higher score.
- Disk is also likely if another service's `_diskio` rises about x3 at the same second as the root-cause service's mem jump.

## delay
- `_latency-90` and `_latency-50` jump x15–20 to a flat plateau, with no error logs and no socket or mem growth.
- If only callers or the entry service show latency, a flat stepped plateau means delay downstream. A drop in one backend's `_workload` can be a side effect; do not blame the entry service.

## loss
- Latency is jagged: minute values swing irregularly (2→5→19→3) instead of holding a plateau.
- The root-cause service's `_workload` falls erratically, often by half.
- "request error" or "failed to …" ERROR logs appear later. Socket counts and memory creep up minutes after onset.
- Rising entry-service sockets under these conditions are a symptom of loss downstream, not socket exhaustion.

## socket
- `_socket` steps up at the fault onset and holds flat. Memory rises modestly at the same second.
- CPU may spike and then return to baseline within a few minutes. That decaying CPU marks socket, not cpu.
- A one-minute diskio spike at onset is an artifact, not disk.

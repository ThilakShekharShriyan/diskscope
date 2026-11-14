## general
- Drill every metric line (M*) for the suspect service before answering. The top-scored line is often a side effect, so judge by the per-minute shape: sustained step, transient spike, plateau or erratic.
- Ignore a diskio line that spikes every ~5 minutes and is zero in between. That is a routine periodic snapshot, not the fault.

## cpu
- Choose cpu when the `_cpu` line steps up (for example 0.4 → ~19) and stays high to the end of the window. Any `_mem` rise should be small (≤x1.5), with no large `_diskio` or `_socket` change.
- A small `_diskio` rise from 0 to a few thousand alongside a sustained `_cpu` rise is still cpu, not disk.

## mem
- Choose mem when `_mem` jumps many-fold at onset, often swinging near a cap of about 256 MiB, and `_cpu` rises to ~19 at the same moment.
- Do not choose mem if `_diskio` reaches about 1e9–1e10 per minute; that is disk.

## disk
- Choose disk when `_diskio` goes from ~0 to about 1e10 per minute and stays there, even if `_mem` (up to x20) or `_cpu` outranks it in the digest. Always drill `_diskio`.
- Suspect disk over mem when another service's `_diskio` rises about 3x at the same moment as the suspect's `_mem` and `_cpu` jump.

## delay
- Choose delay when `latency-90`/`latency-50` steps to a flat, stable plateau (for example 0.2 → 4.3 every minute) with no ERROR logs and no `_socket` explosion.
- If the digest shows no metric change points and only a small drop in call volume, still answer delay. A drop in a caller's `_workload` points toward its callee, not at the caller itself.

## loss
- Choose loss when latency rises but is erratic minute to minute (for example 2, 5, 3, 19, 4).
- Supporting evidence for loss:
  - ERROR logs appear later, such as "rpc error: code = Unavailable", "no healthy upstream" or "request error".
  - `_socket` and `_mem` climb late and then fall back.

## socket
- Choose socket when the service's own `_socket` count steps up at onset and holds a flat plateau to the end of the window.
- A simultaneous `_cpu` burst that returns to baseline within about 4 minutes means socket, not cpu.
- A single-minute `_diskio` spike at onset is an artifact, not disk.

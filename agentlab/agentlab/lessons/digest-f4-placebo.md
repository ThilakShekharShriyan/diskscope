## general
- Call get_digest first. Rank the candidates by the earliest change point, not by the largest one. Callers inherit latency and errors later and more weakly than the root-cause service.
- Before concluding, drill the top 3–5 lines (M*, L*, E*). Confirm which service the evidence belongs to and when it starts. Prefer a service that shows its own resource metric change over one that only shows symptoms.

## cpu
- A metric change point on the root-cause service ending in cpu_usage / cpu_seconds_total / cpu_throttled is a step up that holds through the window. This is the cpu signature.
- Its latency metrics (…_duration, p99) rise moderately at the same time. Few new error log templates appear. Memory and disk suffixes stay flat, which rules out mem and disk.

## mem
- A metric change point on memory_usage / memory_working_set / rss is a ramp or step up on the root-cause service. Drill it to confirm a sustained climb.
- Rare events or log templates may mention OOM, killed, restart, or GC pressure. CPU may rise slightly from GC, but memory leads in time. If CPU leads instead, choose cpu.

## disk
- Metric change points ending in fs_reads / fs_writes / io_time / blkio bytes, or in disk await, spike on the root-cause service.
- Log templates about slow writes, flush, or timeouts on persistence may increase. CPU and memory stay near baseline, which separates disk from cpu and mem.

## delay
- Latency suffixes (…_duration, latency p50/p99) on the root-cause service and its callers show a clean step up, roughly constant in size.
- Error counts and error log templates stay mostly unchanged. Resource metrics are flat. Latency without errors points to delay, not loss.

## loss
- Error log templates such as retransmit, timeout, connection reset, or unavailable rise in rate. Error-rate metrics climb on the root-cause service's callers.
- Latency is spiky and irregular rather than a flat step, because retries produce heavy tails. Request throughput may drop. Errors combined with jittery latency point to loss.

## socket
- Look for change points on socket / connection / file-descriptor counts (sockets_open, tcp_connections, fd_used) climbing toward a plateau on the root-cause service.
- Look for log templates or rare events about "too many open files", "cannot assign address", connection refused, or pool exhausted.

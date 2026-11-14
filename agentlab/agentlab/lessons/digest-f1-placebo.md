## general
- Call get_digest first. Rank candidate services by the earliest metric change point (M lines), not by the largest one. Callers inherit latency and errors later.
- Drill the top M, L and E lines before deciding. A fault's own resource metric (cpu, memory, fs/io, network) on a service outweighs error-log spikes on its callers.

## cpu
- Look for a change point on a container cpu_usage or cpu_throttled suffix that steps up to a plateau on one service.
- Latency rises on that service and its callers, with few new error templates.
- Memory, fs and network metrics stay flat. That separates cpu from mem, disk and network faults.

## mem
- Look for a change point on memory_usage, working_set or rss rising as a ramp or step on one service.
- There may be rare events such as OOM, restart or container-killed.
- CPU may rise slightly from GC, but if memory is the earliest and largest shift, choose mem over cpu.

## disk
- Look for a change point on fs_reads, fs_writes, io_time or blkio bytes suffixes on one service.
- Log templates may show slow-query, timeout-on-write or flush warnings.
- If disk I/O moves first while network metrics stay normal, choose disk over cpu and delay.

## delay
- Look for latency or duration change points (p50/p90/p99 suffixes, span durations) that rise as a clean step on the root-cause service and its callers.
- Error rate stays mostly flat, and CPU, memory and disk stay flat.
- Drilled spans show uniformly longer calls into the root-cause service, without failures. That separates delay from loss.

## loss
- Look for change points on network retransmit, dropped-packet or receive/transmit error suffixes.
- Latency becomes jittery and spiky rather than a clean step.
- New log templates appear such as connection reset, timeout or retry and "unavailable" errors from callers. Errors rise together with latency, which separates loss from delay.

## socket
- Look for rare events or log templates like "too many open files", "cannot assign requested address", "connection refused" or pool-exhausted.
- Also look for change points on socket, file-descriptor or tcp connection-count suffixes that climb toward a ceiling.
- Callers see fast failures (refused or reset) rather than slow responses.
- Network bytes may drop. Retransmits do not rise, which separates socket from loss.

## general
- Call get_digest first. Rank candidates by the earliest change point, not the largest one. The root-cause service usually changes first, and its callers change later.

## cpu
- The digest shows a step-up in a container metric ending in cpu_usage / cpu_user_seconds on one service, often with a cpu_throttled rise. Memory, fs and network-error metrics stay flat.
- Latency rises moderately on that service and its callers, and there are few or no new error log templates. This separates cpu from loss and socket.

## mem
- A metric ending in memory_usage / memory_working_set / rss climbs as a ramp or plateau on one service. Drill it to confirm a monotonic rise rather than a spike.
- Rare events may include OOM, restart or container-start lines. CPU change is absent or secondary. If memory moves without fs I/O counters moving, choose mem over disk.

## disk
- Metrics ending in fs_reads / fs_writes / fs_io_time / blkio bytes jump on one service. CPU may rise slightly as iowait, but memory stays flat.
- Log templates for slow writes, flush or timeout on that service may increase. Network metrics are unaffected, which separates disk from delay and loss.

## delay
- Latency or duration metrics (p90/p99, *_latency) rise as a clean step for calls into one service. Error-rate templates barely change, and resource metrics are flat.
- Drill caller logs. You should see slower responses but successful status codes. Retransmit or packet counters do not change. Uniform added latency with no errors means delay.

## loss
- Metrics ending in network_receive/transmit_packets_dropped or errors, or TCP retransmits, rise on one service.
- Error log templates increase in callers: connection reset, timeout, 5xx, "upstream". The latency distribution becomes long-tailed and spiky rather than a uniform step.
- Errors with flat CPU, memory and fs metrics, plus intermittent successes, mean loss rather than delay.

## socket
- Log templates such as "too many open files", "cannot assign requested address", "connection refused" or "no available socket" appear or surge. These are often listed as rare events (E lines) on the root-cause service or its callers.
- Metrics ending in sockets / file_descriptors / tcp_established / time_wait climb toward a ceiling. Packet-drop counters stay normal, which separates socket from loss.

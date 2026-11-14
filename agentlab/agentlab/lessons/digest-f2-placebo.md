## general
- Call get_digest first, then drill the top 3–5 metric change points (M*) and any log template (L*) or rare event (E*) that names the same service. The root-cause service usually shows the earliest change point and changes on its own container metrics. Its callers mostly show latency or error changes only.

## cpu
- A step-up in a container_cpu_usage / cpu_seconds style metric, or in throttling, on the root-cause service is the core signal. Its own latency rises with it, while network, memory and disk metrics stay flat.
- Logs rarely change much. Few error templates appear, unlike loss or socket faults.

## mem
- A memory_usage / working_set / rss metric climbs as a ramp or plateau on the root-cause service.
- Watch for possible OOM, restart or "killed" rare events (E*). A restart that resets the memory metric to baseline means mem, not cpu.

## disk
- Change points appear in fs_reads / fs_writes / io_time / blkio bytes on the root-cause service.
- Logs may show slow-write or timeout templates around storage or flush operations.
- To tell it from cpu: CPU and network metrics move little compared with the I/O suffixes.

## delay
- Latency (duration / p99 / response_time) steps up uniformly on the root-cause service and every caller above it.
- Throughput and error counts stay roughly flat. Resource metrics do not change.
- Network byte and packet counts are normal, with no retransmit or drop increase.
- Drilled spans show time spent in calls to the service, not inside it.

## loss
- Look for change points in network packet drop, retransmit or receive_errors metrics, or for erratic, spiky latency instead of a clean step.
- Logs show new or rising templates for retries, timeouts, "connection reset", or 5xx from callers.
- To tell it from delay: error and retry rates climb and throughput dips.

## socket
- Log templates appear such as "too many open files", "cannot assign requested address", "connection refused", or connection-pool exhaustion.
- Socket, file-descriptor or TCP-connection-count metrics on the root-cause service spike or saturate.
- Requests fail fast (errors up, latency often low or bimodal). Packet-drop metrics stay clean, which separates socket from loss.

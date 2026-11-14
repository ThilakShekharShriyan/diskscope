## general
- Call get_digest first, then drill the top 3–5 lines. The root-cause service is usually the deepest callee whose own resource metric moves first. Its callers show only latency or error spillover.
- Rank by onset time, not magnitude. The earliest change point on a single service beats larger downstream symptoms.

## cpu
- Look for a step up in a cpu_usage / cpu_seconds metric on one container, drilled as a sustained plateau.
- Latency rises on that service and its callers, but error logs stay rare.
- Memory and disk metrics stay flat, which rules out mem and disk.

## mem
- Look for a monotonic ramp or step in memory_usage / working_set / rss on one container.
- Rare events may show OOM kills, restarts or GC-pressure log templates. Any of these confirms mem over cpu.
- CPU may rise slightly from GC, but the memory change point comes first and is larger.

## disk
- Look for change points in fs_reads / fs_writes / io_time / blkio metrics on one container.
- Log templates may show slow-write warnings, timeouts or flush failures.
- CPU iowait may rise while user CPU stays flat. Memory stays flat.

## delay
- Look for a uniform latency shift in the latency / duration / p99 metrics of the root-cause service and every caller.
- Error rates and "error" log templates barely change.
- Container resource metrics stay flat. Latency rises cleanly, with no retries or resets.

## loss
- Look for latency becoming spiky and bimodal rather than shifted, often showing as a heavy p99 tail.
- Expect a rise in retransmit / timeout / "connection reset" / retry log templates and intermittent 5xx errors.
- An error-rate increase alongside flat resources distinguishes loss from delay.

## socket
- Look for rare events or log templates such as "too many open files", "cannot assign requested address", "connection refused" or connection-pool exhaustion.
- Look for a climb in a sockets / open_fds / tcp_connections metric on the root-cause service.
- Errors are near-total and abrupt rather than partial and intermittent, which distinguishes socket from loss. CPU and memory stay flat.

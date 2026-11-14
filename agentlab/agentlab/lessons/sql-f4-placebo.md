## general
- Find the fault window first. Query metrics for series whose value jumps relative to an earlier baseline, e.g. `SELECT series, MIN(value), AVG(value), MAX(value) FROM metrics GROUP BY series`, then compare the averages before and after the jump.

## cpu
- The root-cause service's `_cpu` series rises sharply into a sustained plateau, and its `_mem` and `_diskio` stay normal.
- Its `_latency-50` and `_latency-90` rise moderately and together, and its spans get uniformly slower with few error statuses.

## mem
- The root-cause service's `_mem` series climbs in a ramp or step and stays high, while `_cpu` changes little.
- Logs may show OOM, "out of memory", allocation failures, or a restart with a gap followed by new startup messages.
- Latency rises less than under cpu stress. Tell it apart from a leak-free fault by the mem shape alone.

## disk
- The root-cause service's `_diskio` series spikes far above its baseline. This is the main discriminator.
- Its spans that touch storage or persistence get long `dur_ms` values, and `_latency-90` inflates more than `_latency-50`.
- Logs may mention slow writes, fsync or timeouts. `_cpu` can rise slightly from iowait, but `_diskio` dominates.

## delay
- Resource metrics stay flat. Spans calling into the root-cause service gain a near-constant added `dur_ms` (for example +100 ms or more on every call), so both `_latency-50` and `_latency-90` shift up together.
- Error status stays mostly OK, with few error logs except timeouts in callers.
- Compute child span durations against parent span durations to find where the extra time begins.

## loss
- Resource metrics stay flat. The `_error` series and error or unset span statuses increase.
- Logs show connection reset, retransmit, "unavailable" or retries.
- Latency becomes bimodal and tail-heavy: `_latency-90` jumps (retry backoff) while `_latency-50` barely moves. This separates loss from delay.
- Error spans cluster on calls to or from the root-cause service.

## socket
- The root-cause service's `_socket` series rises abnormally or saturates.
- Logs show "too many open files", "cannot assign requested address", or connection refused or failed to connect.
- Callers then error quickly with short failing spans rather than slow ones. This is unlike the long durations seen under delay or loss.
- `_cpu`, `_mem` and `_diskio` stay mostly normal.

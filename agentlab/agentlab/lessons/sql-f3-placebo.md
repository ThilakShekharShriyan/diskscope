## general
- Find the fault window first: query metrics for each series' value over time and look for a step change of at least 3x baseline that starts at one shared timestamp. Treat the series that moves first as the leading candidate.

## cpu
- The cpu series rises to a sustained plateau while its mem and diskio stay flat.
- Its latency-50 and latency-90 both rise moderately. Errors stay low.
- In spans, the slowness is spread across all of its operations, including internal ones with no child calls. That is self-time, which rules out network faults.

## mem
- The mem series climbs steeply or ramps to a new plateau while cpu stays nearly flat.
- Logs may show out-of-memory, GC, restart or "killed" messages.
- Latency effects are mild or bursty. Elevated mem with flat cpu and flat diskio points to mem.

## disk
- The diskio series spikes far above baseline for the root-cause service.
- Its cpu may rise slightly because of iowait.
- Spans that touch storage or files get slower. Slow storage operations without a socket rise distinguish disk from socket.

## delay
- Its resource metrics stay flat: no change in cpu, mem or diskio.
- Its latency-50 and latency-90 shift up by a roughly constant amount, and error stays near zero.
- In spans, the caller's client span exceeds the callee's server span by a fixed gap. Alternatively, all of the service's spans grow by the same additive delay.

## loss
- Its resource metrics stay flat.
- The error series rises, and logs show timeout, "connection reset", retry or unavailable messages.
- Spans have error status, and durations become bimodal: most are normal, but some are very long because of retransmits.
- latency-90 jumps far more than latency-50. That gap distinguishes loss from delay.

## socket
- The socket series rises sharply or saturates for the root-cause service.
- Logs show "too many open files", "cannot assign address", "connection refused" or pool-exhausted messages.
- Errors appear on calls to or from that service while cpu and diskio stay mostly flat.
- A socket-count anomaly is the key discriminator against loss, where socket counts stay normal.

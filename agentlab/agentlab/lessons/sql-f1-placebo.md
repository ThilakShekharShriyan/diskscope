## general
- Find the incident onset first. Run `SELECT series, MIN(ts_ms), MAX(value) ... FROM metrics GROUP BY series`, then compare each series' mean after onset with its mean before.

## cpu
- The root-cause service's `_cpu` series jumps to a sustained plateau, often near the container limit. Its `_mem`, `_diskio` and `_socket` stay flat.
- Its spans get uniformly slower across all operation names, with modest `latency-90` growth. Errors stay rare.
- The other fault types do not produce a CPU plateau confined to one service.

## mem
- The root-cause service's `_mem` climbs steadily or steps up and stays high, while `_cpu` rises only slightly.
- Logs may show out-of-memory, GC or "killed/restart" messages from that component. You may also see gaps in its metrics or spans that look like restarts.
- Distinguish it from cpu by the mem rise being the dominant relative change.

## disk
- The root-cause service's `_diskio` spikes far above its baseline, while `_cpu` and `_mem` stay near normal.
- Spans that touch storage (names with read, write, query, db or file) slow sharply. Spans that don't touch storage are less affected.
- Logs may show slow query or timeout warnings.

## delay
- No resource metric moves on the root-cause service. Its `latency-50` and `latency-90` (and those of its callers) shift up by a roughly constant amount.
- Span `dur_ms` for calls into the service gains a fixed offset, often tens to hundreds of ms. `status` stays OK and the `_error` series is flat.

## loss
- Resources stay flat, but the `_error` series rises and tail latency becomes spiky. `latency-90` jumps far more than `latency-50`, reflecting retransmits.
- Spans show intermittent error status, and timeouts cluster near retry or timeout values such as 1 s or 3 s.
- Logs from callers show connection reset, timeout, unavailable or retry messages.

## socket
- The root-cause service's `_socket` series climbs sharply or saturates, while `_cpu` and `_mem` stay roughly flat.
- Logs show "too many open files", "cannot assign address", connection refused or pool-exhausted messages.
- Callers' spans fail fast with error status and short `dur_ms`. This fast-failure pattern differs from loss, where failures come with long timeouts.

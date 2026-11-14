## general
- Find the root-cause service first: pull per-minute averages of every `<svc>_*` series from metrics and look for the earliest step change. Then compare the before/after window on all of its own metrics (cpu, mem, diskio, socket, latency-50/90) before picking a fault type.
- A single elevated resource metric does not decide the type. Check whether that resource shift explains the latency, or whether it merely accompanies it.

## cpu
- The root-cause `_cpu` rises severalfold and stays high, and latency rises with it.
- Before answering cpu, check `_socket`. If the socket count has also stepped up (for example from about 4 to 10–22) and stays there, answer socket, not cpu.

## mem
- `_mem` climbs sharply (roughly 5–15x) while `_socket` stays flat and spans to it show no retransmit pattern.
- Rule out restarts: a gradual mem growth, followed by a drop after startup logs ("Starting gRPC server", "Profiler disabled"), is not mem stress. If caller spans are slow and multimodal, prefer loss.

## disk
- `_diskio` jumps from near zero to a huge sustained level (orders of magnitude above its baseline) at the onset.
- Brief diskio bursts alongside slow, multimodal caller spans point to loss, not disk.

## delay
- Every call to or from the root-cause service gains a roughly constant extra latency (for example a +70–100 ms latency-50 shift). Span durations in the spans table shift uniformly rather than spreading out.
- `_socket`, `_cpu` and `_mem` stay at baseline.

## loss
- Bucket the client-span `dur_ms` of the root-cause service's callers. Loss gives a multimodal histogram: most calls stay fast, with clusters near 200, 400, 600, 800 ms (retransmit timeouts) and long tails of seconds.
- Supporting signals: errors and timeouts in the logs ("failed to get …", "context deadline exceeded" on trace export), while the service's own resource metrics change little.

## socket
- `_socket` of the root-cause service shows a persistent step increase over its flat baseline (2–5x) at the onset, and the increase lasts after onset.
- CPU and latency often rise too. The socket step is the deciding evidence, so do not fall back to cpu or delay when it is present.
- Always query `_socket` at per-second or per-30-second resolution around the onset.

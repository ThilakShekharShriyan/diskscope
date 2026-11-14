## general
- Find the onset first. Query metrics for each <service>_latency-90 and <service>_error series, and take the earliest step change. The root-cause service is usually the deepest one in the span tree whose own dur_ms rises. Its callers inherit the latency through child spans.

## cpu
- Look for <service>_cpu rising sharply (often near saturation) from the onset, while mem, diskio and socket stay flat.
- Its spans show longer self-time (dur_ms minus child dur_ms) on all operations. Errors stay rare.
- latency-50 and latency-90 both rise moderately and noisily. This tells it apart from delay, which gives a clean constant offset.

## mem
- Look for <service>_mem climbing in a ramp or plateau well above baseline, while cpu changes only a little.
- Logs may show OOM, allocation or restart messages. A restart shows as a gap, then the metrics reset.
- Latency effects are mild unless swapping occurs. Do not blame mem if the mem series is unchanged.

## disk
- Look for <service>_diskio spiking from the onset. cpu may rise slightly from iowait.
- Spans that touch storage, files or databases slow down the most. Logs may mention write, flush or timeout.
- Tell it apart from cpu by checking which series leads: if diskio jumps first, it is disk.

## delay
- No resource series changes on the root-cause service.
- Spans from its callers to it gain a near-constant extra dur_ms (e.g. +100–500 ms). The latency-50 shift is about as large as the latency-90 shift.

## loss
- No resource series changes on the root-cause service.
- Latency is bimodal: latency-90 jumps (from retransmits of about 200 ms to 1 s or more) while latency-50 barely moves.
- The <service>_error series rises, and logs show connection reset, unavailable, timeout or retry messages. Spans to the root-cause service show status errors from its callers.

## socket
- Look for <service>_socket climbing steadily to a high plateau.
- Logs contain "too many open files", "cannot assign requested address", "connection refused" or "EMFILE".
- Its spans fail fast with error status, so dur_ms is short, unlike loss.
- cpu and mem stay mostly flat, and the error series rises abruptly.

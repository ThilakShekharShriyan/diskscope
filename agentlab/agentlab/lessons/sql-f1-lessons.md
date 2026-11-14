## general
- Find the onset minute first: compare per-minute averages of every `<service>_<metric>` series in `metrics` before and after it. Then match each candidate fault to the series that changed at the same time on the root-cause service.
- Check the `_socket` series before deciding on cpu or delay. In past runs, socket faults were most often misread as cpu or delay.

## cpu
- Choose cpu only when `_cpu` jumps several-fold, stays near a plateau, and `_socket`, `_mem` and `_diskio` stay flat.
- If `_socket` also rose at the onset, the fault is socket, not cpu. A CPU rise alone does not prove CPU stress.

## mem
- Choose mem when `_mem` shows an abrupt step, roughly 3–5x within a minute, and holds there.
- Slow memory creep is not mem stress.
- A memory drop after "Starting…/listening on port" restart logs in `logs` is not mem stress either. Restarts plus errors on the callers point to loss.

## disk
- Choose disk when `_diskio` goes from about 0 to a sustained high plateau for the whole fault period, often with `_cpu` and `_mem` bumps.
- Isolated `_diskio` bursts alongside caller timeouts and long-tail spans are not enough. Check loss first.

## delay
- Delay shifts the latency distribution uniformly. `_latency-50` and span `dur_ms` of calls to the root-cause service rise by a roughly constant amount (about 70–100 ms).
- Under delay, errors are few and the `_socket`, `_cpu` and `_mem` series are flat.
- Before answering delay, confirm that `_socket` did not step up and that `dur_ms` is not heavy-tailed.

## loss
- Bucket span `dur_ms` for calls to and from the root-cause service. Loss gives a heavy tail: many spans above 1 s and above 3 s, while the median stays small.
- Loss also produces caller-side ERROR logs such as "failed to …", "Unavailable", "context deadline exceeded" or "traces export" timeouts. It can also trigger restarts.
- The root-cause service's own resource metrics stay mostly flat under loss. Do not let side effects in `_mem` or `_diskio` override this pattern.

## socket
- The decisive sign is a step change in the root-cause service's `_socket` series at the onset, for example from 4 to 10–17, sustained or fluctuating. No other fault produces this.
- Latency may rise steadily and `_cpu` may climb as well. Still answer socket whenever `_socket` jumped at the onset.

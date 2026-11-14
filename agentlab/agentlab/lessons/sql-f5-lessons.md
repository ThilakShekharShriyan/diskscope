## general
- First run per-series min/avg/max over metrics, then per-minute averages of the suspect service's cpu, mem, diskio, socket, latency-50/90 and workload. Find the step onset and decide which resource moved first and by the most.
- Check the socket series before settling on cpu or delay. A socket step is the most frequently missed signal.

## cpu
- Use cpu only if the root-cause service's _cpu rises many-fold (5–60x) and plateaus while its _socket stays flat at baseline.
- If _socket also steps up and stays elevated, prefer socket. A CPU spike can be a side effect of socket exhaustion.

## mem
- Use mem when _mem shows a sustained multi-fold jump (5–15x) at onset.
- Slow mem creep, or mem growth ending in a restart (mem drops sharply, "Starting gRPC server" logs reappear), is not by itself mem stress. If latency-90 also rose and spans became heavy-tailed at the same time, weigh loss.

## disk
- Use disk when _diskio jumps from near zero to an order of magnitude far above every other service (GB/s scale) and stays there.
- A modest _diskio rise alongside trace-export "context deadline exceeded" errors is collateral. Check span durations for loss first.

## delay
- Use delay when the root-cause service's span durations shift uniformly by a constant offset (e.g., +70–100 ms on every call).
- Callers' latency-50 rises along with p90.
- _cpu, _mem and _socket stay at baseline, and there are few errors.
- Before naming delay, confirm _socket did not step up.

## loss
- In spans, bucket the root-cause service's dur_ms (e.g., cast(dur_ms/50)*50). Loss gives a multimodal, heavy-tailed histogram: most calls stay fast, with clusters near ~200, 400, 600, 800 ms+ (retransmit timeouts).
- Latency-90 balloons while latency-50 barely moves.
- Expect sporadic caller errors or timeouts ("Unavailable", "deadline exceeded"). Brief "no healthy upstream" with restarts can still be loss.
- Distinguish from delay, which shifts the whole distribution.

## socket
- Use socket when the root-cause service's _socket steps from its baseline to 2–5x (e.g., 4→17, 9→22) at onset and stays high.
- Latency and CPU rising alongside it are secondary.
- Do not call this cpu or delay just because those also moved. The persistent socket step is the deciding evidence.

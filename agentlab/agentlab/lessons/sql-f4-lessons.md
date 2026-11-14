## general
- Start with per-series min/avg/max from `metrics`. Then bucket the suspect's `_cpu`, `_mem`, `_diskio`, `_socket` and `_latency-90` per minute, and compare before vs. after the onset.
- Always read all four resource series before naming a fault. A CPU spike can be a side effect of another fault.
- Use `logs` for errors and restarts, and `spans` duration histograms (`cast(dur_ms/50 as int)`) for the latency shape. Use CAST for integer minute buckets.

## cpu
- Answer cpu only if `_cpu` jumps many-fold and stays high while `_socket`, `_mem` and `_diskio` stay flat.
- Expect latency to rise smoothly with little or no error logging.
- If `_socket` also stepped up, do not answer cpu.

## mem
- Answer mem when `_mem` shows an abrupt multi-fold step (about 5–15x) at the onset that persists.
- A slow drift ending in a sudden drop to baseline, with startup logs ("Starting…", "listening on port"), is a container restart. That pattern is not mem; check for loss.

## disk
- Answer disk when `_diskio` of the root-cause service jumps from near zero to orders of magnitude above other services.
- In disk faults, CPU and socket count barely move.

## delay
- Answer delay when the latency of the root-cause service and its callers shifts up by a near-constant amount (about +100 ms).
- In delay faults, resource series stay flat and there are essentially no error logs or failed spans.
- Span durations shift as a single mode.

## loss
- Answer loss when caller spans show a multimodal duration histogram (clusters near 200, 400, 600, 800 ms, i.e. retransmission timeouts) alongside many fast normal spans.
- Loss also produces caller error logs ("failed to …", "rpc error … Unavailable", status_code 14) and possibly restarts.
- Prefer loss over delay when errors or timeouts appear or durations are multimodal.
- Prefer loss over mem when the memory change is gradual or ends in a restart.

## socket
- Answer socket when `_socket` of the root-cause service shows a persistent step increase at the onset (for example 4→17, 9→22, 4→10) from a value that was constant before.
- Socket faults often come with a large `_cpu` spike. The socket step outranks the CPU spike: answer socket, not cpu.
- Always query `_socket` per minute before concluding cpu.

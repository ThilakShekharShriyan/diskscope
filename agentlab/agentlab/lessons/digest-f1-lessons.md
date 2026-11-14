## general
- Always drill each top metric line before deciding. The digest score ranks magnitude, not cause, and a huge score can come from a one-minute spike.
- A `_diskio` line reading "0 -> a few thousand (from ~0)" whose drill shows a single nonzero minute is an injection artifact. Ignore it and judge from the remaining metrics.

## cpu
- The root-cause service's `_cpu` steps up and stays on a flat plateau to the end of the window.
- There is no sustained `_socket` step at the same time.
- A modest rise (about x1.5) still counts when nothing else persists.

## mem
- Drill `_mem` and look for a step at onset followed by wide minute-to-minute swings: more than ±40% around the level, sometimes pinned at a round cap.
- `_cpu` usually rises to about 19 alongside.
- Any diskio is only a single-minute spike.

## disk
- Drill `_diskio`. A sustained jump to billions per minute, held every minute to the end, means disk.
- In disk faults `_mem` often rises x3 to x12 from page cache. It forms a near-flat plateau (within about ±15%), unlike the wide swings of mem stress.
- A large `_mem` step that is flat after onset should not be called mem, even without a diskio line.

## delay
- Signs: callers' `_latency-90` steps to a constant plateau, the entry service's `_socket` rises modestly, and some `_workload` drops about 20%.
- There are no ERROR logs and no stress-style `_cpu` or `_mem` change on the root-cause service.

## loss
- Signs: the root-cause service's `_workload` falls by half or more with jagged per-minute values, and callers' `_latency-90` is high and erratic.
- New caller ERROR templates appear, such as "Unavailable / no healthy upstream", "failed to get …", or "request error".
- `_mem`, `_socket`, `_cpu` or latency changes on the root-cause service arrive minutes after onset and ramp or jump irregularly. Do not read these late changes as mem or socket stress.

## socket
- The root-cause service's `_socket` steps up at onset and stays flat to the window end, e.g. 4→17 or 3→9.
- `_cpu` or `_mem` may rise at the same second. If `_cpu` returns to baseline within minutes, the answer is socket, not cpu.
- A sustained socket step beside an onset-only diskio blip is socket, not disk.

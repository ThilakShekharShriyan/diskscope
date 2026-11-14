## general
- Find the fault window: query metrics for each series' max/baseline ratio (e.g. GROUP BY series, compare AVG before vs. after the first error/latency spike); the root-cause service shows its own resource or latency shift first, and its callers only mirror it in latency-90 and error.

## cpu
- The root-cause service's _cpu series jumps to a sustained plateau well above baseline while _mem, _diskio and _socket stay flat.
- Its own spans get uniformly longer, including internal or leaf spans with no children, and its _latency-50 rises along with _latency-90. Errors stay rare.

## mem
- The root-cause service's _mem series climbs steeply or steps up and holds, while _cpu is moderate.
- Logs may show out-of-memory, GC or "killed/restart" messages, or gaps followed by startup messages. Latency rise is milder and spikier than with cpu.

## disk
- The root-cause service's _diskio series spikes far above baseline. This is the key discriminator; cpu may rise slightly from I/O wait.
- Slow spans cluster on operations that read or write storage. Log write timestamps may bunch up.

## delay
- No resource series changes. The root-cause service's spans, and every caller span wrapping them, gain a roughly constant added dur_ms, such as +100–200 ms.
- _latency-50 shifts up as a whole, not just the tail. Status stays mostly OK.
- Telltale sign: the child span's start_ms lags the parent's call, or the parent's duration exceeds the child's by a fixed gap.

## loss
- Resources stay flat. Latency is bimodal: most spans are normal, while some take about 1 s or more (retransmit timeouts).
- _latency-90 jumps but _latency-50 barely moves, and _error rises.
- Logs show timeouts, "connection reset", "unavailable" or retry messages, and span status is ERROR on calls into the root-cause service. Callers show the errors, but the common downstream target is the root cause.

## socket
- The root-cause service's _socket series rises sharply or saturates, with cpu and mem roughly normal.
- Logs mention "too many open files", "cannot assign address", "connection refused" or pool exhaustion.
- Calls to or from the root-cause service fail quickly (short dur_ms with ERROR status) rather than slowly. This fast failure separates socket from loss and delay.

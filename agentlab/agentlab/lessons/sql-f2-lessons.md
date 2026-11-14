## general
- Start with per-series min/avg/max over metrics, then per-minute averages of the suspect's cpu, mem, diskio, socket, latency-50/90 and workload. Before naming a fault, compare every resource series of the root-cause service before and after onset.
- Several faults trigger side effects (CPU bursts, small mem drift, diskio blips). Pick the series whose change is a sustained step at onset, not the loudest transient.

## cpu
- Choose cpu only if the root-cause service's _cpu rises several-fold for the whole fault period while its _socket stays flat and _mem/_diskio show no step. Latency-90 rises with it.
- A cpu spike that coincides with a _socket step is socket, not cpu.

## mem
- Look for _mem that jumps many-fold (about 10x or more) and stays high. Mild cpu or latency rise may come along.
- A small mem wiggle next to error logs or span tail growth is loss, not mem.

## disk
- Look for _diskio that goes from near zero to orders of magnitude higher and stays there for minutes.
- Isolated diskio bursts lasting a few seconds amid span timeouts or export errors point to loss.

## delay
- Spans show a uniform, steady added latency (e.g. a constant +tens of ms per call) on the root-cause service's calls. Its callers' latency-50 shifts with it.
- Its own cpu, mem, diskio and socket stay flat, and there are no error or "failed to" log bursts.

## loss
- Bucket span dur_ms (e.g. 50 ms buckets). Loss gives a multimodal tail clustered near retransmit multiples (~200, 400, 600, 800 ms+) while most calls stay fast.
- Loss also brings ERROR logs from callers ("failed to …", Unavailable, deadline exceeded) and process restarts.
- Distinguish from delay: under delay all calls shift by about the same amount; under loss the slowdown sits in a heavy tail.

## socket
- Always query the root-cause service's _socket at 1 s or 30 s resolution. Socket exhaustion shows a step in open sockets (e.g. 4→10, 9→22) that persists after onset.
- It is often accompanied by a big temporary cpu jump. If cpu and socket both step, answer socket.
- Distinguish socket from delay: a steady latency rise plus a socket-count step is socket. Delay leaves _socket unchanged.

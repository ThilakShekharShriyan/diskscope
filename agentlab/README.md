# AgentLab (Phase 3): observability for agents

The question: for the same incidents, how do an agent's accuracy, tokens, dollars and latency change with the *interface* it gets to the telemetry? Every published RCA benchmark (AIOpsLab, OpenRCA, ITBench, ORCA-bench) uses one interface, so none of them can separate the interface from the model.

Research write-up and plan: the shared doc *Observability for Agents, Not Eyeballs*.

## Arms

| Arm | The agent gets | Status |
|---|---|---|
| `visual` | Grafana panels as PNGs (vision) | planned; needs Docker (Prometheus, Loki, Grafana) |
| `mcp` | the real open-source `mcp-grafana` server, rendering off | planned; same stack |
| `files` | `shell` in a fresh Archil microVM over JSONL and CSV files | built |
| `sql` | one read-only SQLite `query` tool | built (DuckDB and Parquet are not in the exec image) |
| `digest` | a ranked digest (`get_digest`) plus `drill(id)` for raw evidence | built |

Every arm gets the same output cap (8,000 characters per tool result), the same turn cap (20) and input-token cap, and must finish with `submit_answer(component, cause, evidence, summary)`. Grading is deterministic, with no LLM judge.

## How it is kept fair

- **The digest is generic and frozen.** `agentlab/digest.py` was committed (27b346c) before any incident was written. It uses only change points, log-template rates, rare events and attribute shifts. `tests/test_digest_freeze.py` fails if it gains fault-specific words.
- **The incidents come from measured Archil behaviour**: custom-image queueing, shared-mode `EROFS`, SQLite in another exec's directory, the 30 s negative-lookup cache, `EEXIST` on concurrent creates, slow commits, and fan-out contention. They include decoys and two no-fault incidents. Four held-out incidents (`ho-*`), of fault types not in that list, test whether the digest generalises.
- **Real timings.** Per-call timings are resampled from DiskScope's published runs (`baselines.json`).
- **Retrieval vs reasoning.** Each run records whether the incident's evidence ever appeared in a tool result, so "never found it" is separated from "found it and misread it".
- **The agent is observed too.** Each run writes an OTel GenAI-shaped trace (`invoke_agent`, `chat`, `execute_tool`).

## Run it

```sh
cd agentlab
python3 -m unittest discover -s tests
python3 -m agentlab prepare --root /tmp/al                      # all 24 incidents, ~13 s, ~330 MB
python3 -m agentlab run --root /tmp/al --incidents erofs-a      # scripted mock model, no network

# On Archil (each tool call is one microVM); source credentials first
set -a; source ../.env.local; set +a
python3 -m agentlab prepare-archil --prefix agentlab/<build> --incidents erofs-a --verify-root /tmp/alv
python3 -m agentlab run --backend archil --prefix agentlab/<build> --incidents erofs-a

# A live model needs --live plus Anthropic credentials, and stops at --max-usd
python3 -m agentlab run --backend archil --prefix agentlab/<build> --model claude-opus-5-5 --live --max-usd 5
```

## Controls on RCAEval (`agentlab/rcaeval_controls.py`, `agentlab/digest_decoy.py`)

- **Healthy windows, mixed suite** (`--incidents rcaevalmix`): 30 real faults (`rcaevalm:<case>`) plus 6 seeded healthy windows (`rcaevalq:<case>`, 17%). A healthy window is the first 11 min of a case, ending 60 s before the injection. Every task offers `no_fault`, and real and healthy tasks share the same prompt, schema, README and 11-min window length: the real windows are cropped to 11 min with the injection at a seeded 210-450 s, which also removes RE2's midpoint leak. Accuracy on `+mix` is therefore not comparable to the 24-min suite; compare arms within it. `--incidents rcaevalq` gives all 30 healthy windows.
- **Digest decoy / placebo** (arms `digest-decoy`, `digest-placebo`): the frozen digest's output with one line inserted as M1 about an innocent service's metric. In the decoy the change is fabricated; in the placebo the numbers are honest. `drill(M1)` returns the series' real (flat) per-minute values in both. The decoy service per task is in `agentlab/rcaeval_decoys.json` and in each run's `detail.decoy`.
- The report adds the false-positive rate on healthy windows, the false-negative rate on real `+mix` tasks, and decoy capture (decoy service named first, or in the top 5, with the plain digest as the base rate).

```sh
python -m agentlab.rcaeval_controls --data ../data-ext/rcaeval --root ../data-ext/al-rcaeval-controls
python3 -m agentlab run --root ../data-ext/al-rcaeval-controls --incidents rcaevalmix --arms sql,digest,digest-decoy,digest-placebo
```

A live model can only use `shell` through Archil, never on this machine. The local backend's shell is enabled only for the scripted mock model.

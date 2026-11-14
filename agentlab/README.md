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

## RCAEval suites (real fault injections)

| Ids | Dataset | App | Subset |
|---|---|---|---|
| `rcaeval:<case>`, `rcaeval+t:<case>` | RE2-OB | Online Boutique, 12 services | `rcaeval_subset.json` |
| `rcaevaltt:<case>`, `rcaevaltt+t:<case>` | RE2-TT | Train Ticket, 68 services and databases | `rcaeval_tt_subset.json` |

The `+t` variant also tells the agent the injection time; it reads the same layout. Each subset is 30 cases, one repetition per (service, fault), chosen by seed and committed before any scoring. `--incidents rcaevaltt-pilot` is a fixed six-case pilot (one per fault). The service enum comes from the metric-column prefixes, so it describes the app, not the labels.

```sh
# Download the 30 TT cases (metrics, logs, traces) and build the layouts: ~6 min with 4 processes, ~2.2 GB peak per process
python -m agentlab.rcaeval_baselines --dataset re2-tt --data ../data-ext/rcaeval-tt-full \
    --root ../data-ext/al-rcaeval-tt --out tt-baselines.json --jobs 4 --download
AGENTLAB_RCAEVAL_TT_DATA=../data-ext/rcaeval-tt-full python -m agentlab run --root ../data-ext/al-rcaeval-tt \
    --incidents rcaevaltt-pilot --arms sql,digest --model claude-opus-5-5 --live --max-usd 3
```

`AGENTLAB_RCAEVAL_TT_DATA` is needed only by `rcaevaltt+t`, which reads the injection time.

A live model can only use `shell` through Archil, never on this machine. The local backend's shell is enabled only for the scripted mock model.

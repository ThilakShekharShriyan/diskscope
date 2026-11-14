# AgentLab (Phase 3): observability for agents

The question: for the same incidents, how do an agent's accuracy, tokens, dollars and latency change with the *interface* it gets to the telemetry? Every published RCA benchmark (AIOpsLab, OpenRCA, ITBench, ORCA-bench) uses one interface, so none of them can separate the interface from the model.

Research write-up and plan: the shared doc *Observability for Agents, Not Eyeballs*.

## Arms

| Arm | The agent gets | Status |
|---|---|---|
| `visual` | Grafana panels as PNGs (vision): `get_dashboard`, `render_panel(panel, service, from_min, to_min)` | built; needs the Docker stack |
| `mcp` | the real open-source `mcp-grafana` 2.1.1 (61 tools, ~23.8k tokens), rendering and write tools off | built; same stack |
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

## Grafana arms (`visual`, `mcp`)

`agentlab/grafana/docker-compose.yml` pins Prometheus v3.15.1, Loki 3.7.9, Grafana 13.2.5 and
grafana-image-renderer v5.12.9 (ports on 127.0.0.1 only). `grafana load` backfills each incident's
`grafana/<opaque>/metrics.om` with `promtool tsdb create-blocks-from openmetrics` and pushes `loki.json`.
Many incidents share one stack: every series and log stream carries `incident="<opaque id>"` and every
query selects by it; case names never enter the stack. Series become `app_<stat>{service, incident}`;
the one dashboard (`Service overview`) has a panel per stat found in the data plus log-volume and
error-log panels, nothing chosen per fault. Traces are not in the stack (no Tempo), so these arms see
metrics and logs only.

- `visual`: PNGs go back as image content blocks (Claude `tool_result` images; OpenAI `input_image`).
  The image budget is fixed sizes (dashboard 1400 px wide, panel 1000x500), the analogue of the 8,000-char cap.
- `mcp`: `grafana/mcp-grafana:2.1.1` over stdio (`--disable-rendering --disable-write`), a read-only
  Viewer service account, its real `tools/list` passed through unchanged (no strict mode). Text results
  are capped at 8,000 chars like every other arm. `grafana tools --out F` logs the list and its size.

```sh
python3 -m agentlab grafana up
python3 -m agentlab grafana load --root ../data-ext/al-rcaeval            # all prepared incidents (~2 s each)
python3 -m agentlab run --backend grafana --root ../data-ext/al-rcaeval --arms visual,mcp --incidents rcaeval
python3 -m agentlab grafana down                                          # removes the volumes too
```

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

A live model can only use `shell` through Archil, never on this machine. The local backend's shell is enabled only for the scripted mock model.

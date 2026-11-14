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
| `sql+lessons`, `digest+lessons` | the base arm plus a lessons file appended to the system prompt, distilled by one fixed model from graded training-fold runs (leave-one-service-out over RE2-OB) | built (`agentlab/lessons.py`) |
| `sql+placebo`, `digest+placebo` | control: same length, written by the same model from only the fault list and the tool schema | built |
| `rcatools` | RCAEval's own agent tools (`describe_case`, `list_metrics`, `get_metric`, `rank_anomalies`, `search_logs`, `trace_summary`, `call_graph`), RCAEval suite only | built (local backend) |

### The `rcatools` arm (RCAEval 1.12.0's agent interface)

RCAEval ships an LLM-agent setup in `RCAEval/agent/`: `export.py` writes a blind task view (cases renamed `re2-ob-0001`…, telemetry CSVs, `task.json`; labels in a separate answers file), `toolkit.py` serves 8 tools over one case (the 7 above plus `submit(ranks, reasoning)`), `react.py` is their reference agent (LiteLLM, prompt v2 in `prompt.py`, 30 steps, temperature 0, forced submit on the last step), `mcp_server.py` serves the same tools over MCP, and `predictions.py` scores AC@1/3/5, Avg@5, Chance@5 and Lift@5 at service and indicator level, plus Inspected@root.

- **The tools are theirs.** `agentlab/rcatools.py` keeps a verbatim copy of their tool names, schemas and descriptions (a test compares it with the installed package), and RCAEval's `CaseToolkit` runs every call. Results are their JSON. **Cap:** theirs (`MAX_CHARS` = 8,000 characters of JSON; `cap()` halves the longest list). Our 8,000-character cut applies only if a result still exceeds it; `calls[*].cap` records which cap applied.
- **The views are ours.** `prepare-rcatools` builds an export-shaped view under `<root>/rcatools/<opaque>/` from the Hugging Face Parquet: `metrics.csv`, `logs.csv` and `traces.csv`, then `tracets_err`, `tracets_lat`, `logts` and `log_templates.json` via `RCAEval.convert.derive`. `task.json` sets `case_id` to the opaque id and has a ±10-minute standard window. Error text has its paths replaced. Drain3 is needed only for `logts`; without it, `rank_anomalies(logs)` reports that the series is missing.
- **It shows the injection time.** `describe_case` returns `inject_time`, and every tool splits before and after it, so the report labels this arm `rcatools+time`. Compare it with the `+time` arms.
- **Same task as the other arms.** It uses our prompt (`rcaeval.SYSTEM`), caps and `submit_answer(services, fault)`. Their `submit` and prompt v2 are not used, so only the tools differ.
- **Inspected@root** (report column) is RCAEval's rule for rcatools: a `service` or `name` argument belonging to the root-cause service before submit. For sql and files it is a proxy, the name appearing as a token in the query or command (marked `*`). For digest and prior it is `—`.

```sh
PYTHONPATH=<dir with drain3> .venv/bin/python -m agentlab prepare-rcatools --data ../data-ext/rcaeval --root ../data-ext/al-rcatools
.venv/bin/python -m agentlab run --root ../data-ext/al-rcatools --arms rcatools --incidents rcaeval [--model ... --live]
```

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

RCAEval layouts are too big to generate from code, so they are uploaded. An exec takes at most 100 KiB of command (and no stdin), but it has outbound HTTPS, so each microVM fetches its case's Parquet from Hugging Face and re-runs this repo's converter. Files whose sha256 matches the local copy are kept. `telemetry.db` differs in a few hundred SQLite pages, so only those pages are sent. A last pass re-hashes every file from fresh microVMs. Reruns skip files that are already there.

```sh
python3 -m agentlab upload-archil --root ../data-ext/al-rcaeval --prefix agentlab/rc1 --parallel 10
python3 -m agentlab.parity --root ../data-ext/al-rcaeval --prefix agentlab/rc1 --n 3   # local vs Archil tool outputs

## Controls on RCAEval (`agentlab/rcaeval_controls.py`, `agentlab/digest_decoy.py`)

- **Healthy windows, mixed suite** (`--incidents rcaevalmix`): 30 real faults (`rcaevalm:<case>`) plus 6 seeded healthy windows (`rcaevalq:<case>`, 17%). A healthy window is the first 11 min of a case, ending 60 s before the injection. Every task offers `no_fault`, and real and healthy tasks share the same prompt, schema, README and 11-min window length: the real windows are cropped to 11 min with the injection at a seeded 210-450 s, which also removes RE2's midpoint leak. Accuracy on `+mix` is therefore not comparable to the 24-min suite; compare arms within it. `--incidents rcaevalq` gives all 30 healthy windows.
- **Digest decoy / placebo** (arms `digest-decoy`, `digest-placebo`): the frozen digest's output with one line inserted as M1 about an innocent service's metric. In the decoy the change is fabricated; in the placebo the numbers are honest. `drill(M1)` returns the series' real (flat) per-minute values in both. The decoy service per task is in `agentlab/rcaeval_decoys.json` and in each run's `detail.decoy`.
- The report adds the false-positive rate on healthy windows, the false-negative rate on real `+mix` tasks, and decoy capture (decoy service named first, or in the top 5, with the plain digest as the base rate).

```sh
python -m agentlab.rcaeval_controls --data ../data-ext/rcaeval --root ../data-ext/al-rcaeval-controls
python3 -m agentlab run --root ../data-ext/al-rcaeval-controls --incidents rcaevalmix --arms sql,digest,digest-decoy,digest-placebo
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

## Lessons arms (RCAEval only)

```sh
# 1. distil per (base arm, fold) lessons + placebo from graded runs; --dry-run prints the prompts and projected cost
python3 -m agentlab lessons build --runs ../results/step1/*-notime.json --out-dir lessons/ --max-usd 2 [--dry-run]
python3 -m agentlab lessons check --dir lessons/      # sha256 vs manifest + service-name/case-id grep
# 2. commit lessons/ (live runs refuse an uncommitted lessons dir), then run
python3 -m agentlab run ... --arms sql+lessons,sql+placebo,digest+lessons,digest+placebo --incidents rcaeval
```

Each test case gets the file built without its own service's runs; `detail.lessons_sha` records which.

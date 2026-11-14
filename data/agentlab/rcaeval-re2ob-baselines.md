# RCAEval RE2-OB: no-LLM baselines (2026-11-14)

File: `rcaeval-re2ob-baselines-2026-11-14.json` (30 cases, fixed subset in `agentlab/agentlab/rcaeval_subset.json`).

| Ranker | Gets injection time | Sources | AC@1 (95% Wilson) | AC@3 | Avg@5 |
|---|---|---|---|---|---|
| Frozen AgentLab digest, read as a ranking | no | metrics, logs, traces | 0.70 (0.52–0.83) | 0.90 | 0.85 |
| RCAEval metric BARO (their code, their main.py protocol) | yes | metrics | 0.13 (0.05–0.30) | 0.87 | 0.75 |
| Chance (12 services) | – | – | 0.08 | 0.25 | – |

**BARO reproduction check:** the same `baro_rank` code on all 90 RE2-TT cases gives AC@1 0.67, AC@3 0.82,
Avg@5 0.80, matching the paper's Table 6 exactly on every fault row. So the OB result is BARO's real behaviour:
`redis_diskio` has an IQR of 0 before injection, so RobustScaler returns raw deltas (scores of 25,000+)
and redis tops most rankings. The paper publishes no RE2-OB table.

**Caveats:**
- n = 30. Per-fault rows are 5 cases each (digest is weak on `delay`, Avg@5 0.36), an observation, not a finding.
- The two rankers are not on equal terms (see the columns above).
- The digest's log section on this data is mostly UUID variants that the frozen templater does not collapse,
  so its top-1 comes from metric change points. Not fixed: changing the digest after seeing RCAEval output would be tuning.
- No LLM has run on RCAEval yet; this is the digest's text read mechanically.

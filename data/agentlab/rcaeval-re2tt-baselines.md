# RCAEval RE2-TT (Train Ticket): no-LLM baselines (2026-11-14)

File: `rcaeval-re2tt-baselines-2026-11-14.json`. The 30 cases are the fixed subset in `agentlab/agentlab/rcaeval_tt_subset.json`: one repetition per (service, fault), chosen by seed and committed before any scoring. The agent's service enum has 68 components.

| Ranker | Gets injection time | Sources | AC@1 (95% Wilson) | AC@3 | Avg@5 |
|---|---|---|---|---|---|
| Frozen AgentLab digest, read as a ranking | no | metrics, logs, traces | 0.53 (0.36–0.70) | 0.67 | 0.65 |
| RCAEval BARO (`dataset="re2-tt"`) | yes | metrics | 0.70 (0.52–0.83) | 0.87 | 0.84 |
| RCAEval N-Sigma | yes | metrics | 0.80 (0.63–0.90) | 0.90 | 0.88 |
| BARO, injection time given 120 s late | late | metrics | 0.70 | 0.80 | 0.78 |
| N-Sigma, injection time given 120 s late | late | metrics | 0.00 | 0.03 | 0.02 |
| Chance among the 5 root-cause services | – | – | 0.20 | 0.60 | 0.60 |

**Paper check:** BARO on all 90 RE2-TT cases (metrics-only copies, `--all-cases`; `rcaeval-re2tt-all90-classic-2026-11-14.json`) gives AC@1 0.667, AC@3 0.82 and Avg@5 0.80. That matches Table 6 (0.67). N-Sigma on the same 90 cases scores 0.76.

**Digest by fault (Avg@5):** cpu 1.00, disk 1.00, socket 1.00, mem 0.40, delay 0.44, loss 0.04.

**Data and cost:**
- 30 full cases from Hugging Face take 629 MB.
- The layouts in `data-ext/al-rcaeval-tt` take 23 GB: files 9.4 GB, sql 8.6 GB, grafana 4.5 GB, digest 17 MB.
- Each case has 123k–1.32M spans. One case (`ts-auth-service_cpu_1`) has no logs.
- Converting, preparing and running the frozen digest takes at most 74 s and 2.1 GB peak RSS per case. All 30 cases take 5.5 min with 4 processes.

**Caveats:**
- n = 30, and each per-fault row covers only 5 cases.
- The classical rankers get the injection time and the digest does not.
- N-Sigma collapses when its time is 120 s late: 0.01 AC@1 on all 90 cases. This is observed, not investigated. BARO is robust to the same shift.

## Live pilot (2026-11-14)

Six cases from `rcaeval_tt_pilot.json`, one per fault, chosen by seed. Each ran with the sql and digest arms, 1 trial, `--parallel 8`. Files: `pilot-rcaevaltt-<model>-2026-11-14.json`.

| Model | Arm | AC@1 | Fault right | Both right | $ |
|---|---|---|---|---|---|
| claude-opus-5-5 | sql | 6/6 | 4/6 | 4/6 | 0.79 |
| claude-opus-5-5 | digest | 4/6 | 3/6 | 3/6 | 0.24 |
| gpt-5.6-sol | sql | 6/6 | 5/6 | 5/6 | 0.98 |
| gpt-5.6-sol | digest | 4/6 | 3/6 | 3/6 | 0.32 |

The pilot cost $2.33 in total.
- Runs had no infra errors, no model mismatches and no SQL errors.
- SQL calls on the large spans table took a median of 135–284 ms and at most 3.4 s.
- Both digest runs missed the same two cases: order mem and train loss.
- Both models called the socket case `cpu`.
- n = 6, so these numbers are an observation, not a finding.

**Estimate for 30 cases × 3 trials × sql+digest, no-time variant only** (`python -m agentlab estimate`):
- claude-opus-5-5: $13.4 at the median, up to $31.6.
- gpt-5.6-sol: $16.4 at the median, up to $35.9.
- Both models: about $30 at the median, $68 at most. Adding the `+t` variant doubles that.

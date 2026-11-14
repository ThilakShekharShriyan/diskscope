| Model | Interface | Cases × trials | Service AC@1 | AC@3 | Fault type | Both right | pass^k | Input tok (med) | $/run (med) | $/correct | Calls (med) | Tool errors | Inspected@root |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | digest+mix | 30 × 3.0 | 84% (71%–97%) | 89% | 56% (39%–72%) | 52% (36%–69%) | 47% | 6961 | 0.016 | 0.036 | 3 | 0% | — |
| claude-opus-5-5 | sql+mix | 30 × 3.0 | 100% (100%–100%) | 100% | 88% (79%–96%) | 88% (79%–96%) | 73% | 10926 | 0.038 | 0.057 | 2 | 0% | 100%* |
| gpt-5.6-sol | digest+mix | 30 × 3.0 | 83% (70%–97%) | 83% | 44% (28%–62%) | 41% (24%–59%) | 37% | 3929 | 0.017 | 0.043 | 4 | 0% | — |
| gpt-5.6-sol | sql+mix | 30 × 3.0 | 100% (100%–100%) | 100% | 80% (67%–91%) | 80% (67%–91%) | 67% | 30914 | 0.099 | 0.132 | 12 | 2% | 100%* |

Inspected@root (RCAEval's evidence metric): a tool call named the root-cause service before submit. Exact for rcatools (a service/name argument, RCAEval's rule); * = proxy for sql/files (the name appears as a token in the query/command); — = not measurable (digest, prior). rcatools always shows the injection time, so it is labelled +time.

Paired differences (per case, mean of trials; 95% paired bootstrap):

- [models] digest+mix: claude-opus-5-5 − gpt-5.6-sol: correct +11pt (+3pt, +21pt); usd +0.001 (-0.001, +0.003); McNemar p=0.25, Holm 0.25 (3 vs 0 cases)
- [models] sql+mix: claude-opus-5-5 − gpt-5.6-sol: correct +8pt (+2pt, +14pt); usd -0.056 (-0.061, -0.050); McNemar p=0.50, Holm 0.50 (2 vs 0 cases)
Healthy-window controls (mixed suite; every task offers no_fault). FP = a fault named on a healthy window:

| Model | Interface | Healthy cases × runs | False-positive rate | FP Wilson (cases) | Unsubmitted | False-negative rate (real) | Both right (real) |
|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | digest+mix | 6 × 18 | 28% (0%–61%) | 10%–70% | 0 | 8% (0%–18%) | 52% (36%–69%) |
| claude-opus-5-5 | sql+mix | 6 × 18 | 0% (0%–0%) | 0%–39% | 0 | 0% (0%–0%) | 88% (79%–96%) |
| gpt-5.6-sol | digest+mix | 6 × 18 | 33% (0%–67%) | 10%–70% | 0 | 3% (0%–10%) | 41% (24%–59%) |
| gpt-5.6-sol | sql+mix | 6 × 18 | 72% (44%–94%) | 30%–90% | 0 | 0% (0%–0%) | 80% (67%–91%) |

No-LLM reference: answer no_fault iff the plain digest's METRICS section is empty: false positives 2/6 healthy windows, false negatives 1/30 real windows.

Decoy capture: how often the innocent service named by the inserted digest line is the agent's first answer (base rate = same service on arms without the line):

| Model | Interface | Cases × runs | Decoy named first | Decoy in top 5 | Service AC@1 |
|---|---|---|---|---|---|
| claude-opus-5-5 | digest+mix | 30 × 90 | 0% (0%–0%) | 3% (0%–10%) | 84% (71%–97%) |
| claude-opus-5-5 | sql+mix | 30 × 90 | 0% (0%–0%) | 12% (3%–23%) | 100% (100%–100%) |
| gpt-5.6-sol | digest+mix | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 83% (70%–97%) |
| gpt-5.6-sol | sql+mix | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 100% (100%–100%) |

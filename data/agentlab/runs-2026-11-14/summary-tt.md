| Model | Interface | Cases × trials | Service AC@1 | AC@3 | Fault type | Both right | pass^k | Input tok (med) | $/run (med) | $/correct | Calls (med) | Tool errors | Inspected@root |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | digest | 30 × 3.0 | 76% (60%–90%) | 77% | 67% (50%–83%) | 58% (40%–76%) | 57% | 11140 | 0.021 | 0.055 | 3 | 0% | — |
| claude-opus-5-5 | digest+time | 30 × 3.0 | 74% (59%–90%) | 76% | 63% (47%–79%) | 57% (40%–73%) | 50% | 11206 | 0.025 | 0.058 | 3 | 0% | — |
| claude-opus-5-5 | sql | 30 × 3.0 | 97% (92%–100%) | 99% | 60% (43%–77%) | 59% (42%–76%) | 50% | 29525 | 0.071 | 0.134 | 5 | 1% | 83%* |
| claude-opus-5-5 | sql+time | 30 × 3.0 | 97% (91%–100%) | 100% | 64% (47%–80%) | 61% (44%–78%) | 53% | 9217 | 0.037 | 0.075 | 2 | 0% | 42%* |
| gpt-5.6-sol | digest | 30 × 3.0 | 77% (60%–90%) | 77% | 58% (41%–74%) | 52% (36%–69%) | 47% | 10748 | 0.033 | 0.077 | 5 | 0% | — |
| gpt-5.6-sol | digest+time | 30 × 3.0 | 73% (57%–87%) | 74% | 58% (42%–73%) | 50% (33%–67%) | 43% | 9094 | 0.027 | 0.081 | 5 | 0% | — |
| gpt-5.6-sol | sql | 30 × 3.0 | 93% (87%–99%) | 96% | 67% (51%–81%) | 62% (47%–77%) | 50% | 61052 | 0.141 | 0.241 | 16 | 1% | 99%* |
| gpt-5.6-sol | sql+time | 30 × 3.0 | 94% (87%–100%) | 96% | 72% (57%–87%) | 67% (51%–82%) | 60% | 42727 | 0.110 | 0.175 | 10 | 1% | 100%* |

Inspected@root (RCAEval's evidence metric): a tool call named the root-cause service before submit. Exact for rcatools (a service/name argument, RCAEval's rule); * = proxy for sql/files (the name appears as a token in the query/command); — = not measurable (digest, prior). rcatools always shows the injection time, so it is labelled +time.

No-LLM reference on the same cases: digest as ranking, no LLM, no time AC@1 53%, Avg@5 65%; N-Sigma (given time) AC@1 80%, Avg@5 88%; BARO (given time) AC@1 70%, Avg@5 84%; chance (5 candidates) AC@1 20%, Avg@5 60%

Paired differences (per case, mean of trials; 95% paired bootstrap):

- [interface] claude-opus-5-5: digest − sql: correct -1pt (-20pt, +18pt); fault_ok +7pt (-9pt, +22pt); usd -0.047 (-0.059, -0.037); McNemar p=1.00, Holm 1.00 (4 vs 5 cases)
- [interface+time] claude-opus-5-5: digest+time − sql+time: correct -4pt (-23pt, +14pt); fault_ok -1pt (-19pt, +17pt); usd -0.013 (-0.019, -0.008); McNemar p=0.55, Holm 0.55 (4 vs 7 cases)
- [time] claude-opus-5-5: digest − digest+time: correct +1pt (-4pt, +8pt); fault_ok +3pt (-6pt, +13pt); usd -0.001 (-0.003, +0.001); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- [time] claude-opus-5-5: sql − sql+time: correct -2pt (-11pt, +6pt); fault_ok -4pt (-12pt, +2pt); usd +0.033 (+0.025, +0.041); McNemar p=1.00, Holm 1.00 (1 vs 2 cases)
- [interface] gpt-5.6-sol: digest − sql: correct -10pt (-29pt, +9pt); fault_ok -9pt (-27pt, +9pt); usd -0.109 (-0.125, -0.095); McNemar p=0.51, Holm 0.51 (3 vs 6 cases)
- [interface+time] gpt-5.6-sol: digest+time − sql+time: correct -17pt (-36pt, +2pt); fault_ok -14pt (-32pt, +3pt); usd -0.076 (-0.084, -0.068); McNemar p=0.23, Holm 0.23 (3 vs 8 cases)
- [time] gpt-5.6-sol: digest − digest+time: correct +2pt (-4pt, +10pt); fault_ok +0pt (-9pt, +9pt); usd -0.000 (-0.006, +0.005); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- [time] gpt-5.6-sol: sql − sql+time: correct -4pt (-14pt, +4pt); fault_ok -6pt (-16pt, +4pt); usd +0.033 (+0.024, +0.043); McNemar p=1.00, Holm 1.00 (1 vs 2 cases)
- [models] digest: claude-opus-5-5 − gpt-5.6-sol: correct +6pt (+1pt, +11pt); usd -0.009 (-0.014, -0.003); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- [models] digest+time: claude-opus-5-5 − gpt-5.6-sol: correct +7pt (-2pt, +16pt); usd -0.008 (-0.013, -0.003); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- [models] sql: claude-opus-5-5 − gpt-5.6-sol: correct -3pt (-18pt, +9pt); usd -0.071 (-0.085, -0.058); McNemar p=1.00, Holm 1.00 (2 vs 3 cases)
- [models] sql+time: claude-opus-5-5 − gpt-5.6-sol: correct -6pt (-13pt, +1pt); usd -0.071 (-0.080, -0.063); McNemar p=1.00, Holm 1.00 (0 vs 1 cases)

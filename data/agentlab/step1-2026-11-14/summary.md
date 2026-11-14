| Model | Interface | Cases × trials | Service AC@1 | AC@3 | Fault type | Both right | pass^k | Input tok (med) | $/run (med) | $/correct | Calls (med) | Tool errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | digest | 30 × 3.0 | 87% (73%–97%) | 96% | 60% (43%–77%) | 52% (34%–70%) | 50% | 6948 | 0.016 | 0.037 | 3 | 0% |
| claude-opus-5-5 | digest+time | 30 × 3.0 | 87% (73%–97%) | 97% | 60% (44%–76%) | 51% (34%–69%) | 47% | 7016 | 0.017 | 0.036 | 3 | 0% |
| claude-opus-5-5 | prior | 30 × 3.0 | 14% (6%–26%) | 39% | 17% (3%–30%) | 2% (0%–7%) | 0% | 1004 | 0.005 | 0.243 | 0 | 0% |
| claude-opus-5-5 | sql | 30 × 3.0 | 100% (100%–100%) | 100% | 90% (80%–98%) | 90% (80%–98%) | 80% | 12874 | 0.047 | 0.061 | 2 | 0% |
| claude-opus-5-5 | sql+time | 30 × 3.0 | 100% (100%–100%) | 100% | 73% (60%–86%) | 73% (60%–86%) | 60% | 7390 | 0.030 | 0.047 | 2 | 0% |
| gpt-5.6-sol | digest | 30 × 3.0 | 83% (70%–97%) | 83% | 59% (41%–77%) | 50% (33%–67%) | 50% | 4060 | 0.020 | 0.046 | 4 | 0% |
| gpt-5.6-sol | digest+time | 30 × 3.0 | 84% (71%–97%) | 87% | 58% (41%–73%) | 48% (31%–64%) | 40% | 3967 | 0.020 | 0.051 | 4 | 0% |
| gpt-5.6-sol | prior | 30 × 3.0 | 17% (8%–27%) | 38% | 13% (3%–26%) | 2% (0%–6%) | 0% | 405 | 0.006 | 0.280 | 0 | 0% |
| gpt-5.6-sol | sql | 30 × 3.0 | 100% (100%–100%) | 100% | 80% (67%–92%) | 80% (67%–92%) | 70% | 39528 | 0.108 | 0.146 | 12 | 1% |
| gpt-5.6-sol | sql+time | 30 × 3.0 | 100% (100%–100%) | 100% | 81% (68%–92%) | 81% (68%–92%) | 70% | 27952 | 0.085 | 0.118 | 8 | 0% |

No-LLM reference on the same cases: digest as ranking, no LLM, no time AC@1 70%, Avg@5 85%; N-Sigma (given time) AC@1 77%, Avg@5 89%; BARO (given time) AC@1 13%, Avg@5 75%; chance (5 candidates) AC@1 20%, Avg@5 60%

Paired differences (per case, mean of trials; 95% paired bootstrap):

- claude-opus-5-5: digest − digest+time: correct +1pt (-4pt, +8pt); fault_ok +0pt (-6pt, +7pt); usd +0.001 (-0.000, +0.002); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- claude-opus-5-5: digest − prior: correct +50pt (+31pt, +69pt); fault_ok +43pt (+22pt, +66pt); usd +0.014 (+0.011, +0.017); McNemar p=0.00, Holm 0.00 (16 vs 1 cases)
- claude-opus-5-5: digest − sql: correct -38pt (-58pt, -17pt); fault_ok -30pt (-49pt, -10pt); usd -0.035 (-0.042, -0.029); McNemar p=0.00, Holm 0.01 (1 vs 13 cases)
- claude-opus-5-5: digest − sql+time: correct -21pt (-39pt, -3pt); fault_ok -13pt (-29pt, +2pt); usd -0.015 (-0.019, -0.012); McNemar p=0.23, Holm 0.45 (3 vs 8 cases)
- claude-opus-5-5: digest+time − prior: correct +49pt (+30pt, +68pt); fault_ok +43pt (+23pt, +63pt); usd +0.013 (+0.010, +0.016); McNemar p=0.00, Holm 0.00 (15 vs 1 cases)
- claude-opus-5-5: digest+time − sql: correct -39pt (-58pt, -20pt); fault_ok -30pt (-48pt, -12pt); usd -0.036 (-0.043, -0.029); McNemar p=0.00, Holm 0.01 (1 vs 14 cases)
- claude-opus-5-5: digest+time − sql+time: correct -22pt (-39pt, -6pt); fault_ok -13pt (-28pt, +1pt); usd -0.016 (-0.020, -0.012); McNemar p=0.15, Holm 0.44 (3 vs 9 cases)
- claude-opus-5-5: prior − sql: correct -88pt (-96pt, -78pt); fault_ok -73pt (-87pt, -58pt); usd -0.049 (-0.058, -0.041); McNemar p=0.00, Holm 0.00 (0 vs 27 cases)
- claude-opus-5-5: prior − sql+time: correct -71pt (-83pt, -57pt); fault_ok -57pt (-73pt, -39pt); usd -0.029 (-0.033, -0.025); McNemar p=0.00, Holm 0.00 (0 vs 20 cases)
- claude-opus-5-5: sql − sql+time: correct +17pt (+4pt, +30pt); fault_ok +17pt (+4pt, +30pt); usd +0.020 (+0.015, +0.027); McNemar p=0.02, Holm 0.06 (7 vs 0 cases)
- gpt-5.6-sol: digest − digest+time: correct +2pt (-6pt, +10pt); fault_ok +1pt (-8pt, +10pt); usd -0.001 (-0.003, +0.001); McNemar p=1.00, Holm 1.00 (2 vs 1 cases)
- gpt-5.6-sol: digest − prior: correct +48pt (+29pt, +67pt); fault_ok +46pt (+28pt, +63pt); usd +0.017 (+0.014, +0.020); McNemar p=0.00, Holm 0.00 (15 vs 0 cases)
- gpt-5.6-sol: digest − sql: correct -30pt (-50pt, -9pt); fault_ok -21pt (-40pt, -3pt); usd -0.094 (-0.103, -0.085); McNemar p=0.01, Holm 0.05 (2 vs 12 cases)
- gpt-5.6-sol: digest − sql+time: correct -31pt (-51pt, -11pt); fault_ok -22pt (-41pt, -3pt); usd -0.073 (-0.082, -0.063); McNemar p=0.01, Holm 0.05 (2 vs 12 cases)
- gpt-5.6-sol: digest+time − prior: correct +46pt (+28pt, +63pt); fault_ok +44pt (+28pt, +60pt); usd +0.018 (+0.014, +0.022); McNemar p=0.00, Holm 0.00 (14 vs 0 cases)
- gpt-5.6-sol: digest+time − sql: correct -32pt (-51pt, -12pt); fault_ok -22pt (-41pt, -4pt); usd -0.093 (-0.102, -0.084); McNemar p=0.01, Holm 0.04 (2 vs 13 cases)
- gpt-5.6-sol: digest+time − sql+time: correct -33pt (-52pt, -13pt); fault_ok -23pt (-42pt, -6pt); usd -0.072 (-0.081, -0.062); McNemar p=0.01, Holm 0.04 (2 vs 13 cases)
- gpt-5.6-sol: prior − sql: correct -78pt (-90pt, -64pt); fault_ok -67pt (-80pt, -52pt); usd -0.111 (-0.121, -0.101); McNemar p=0.00, Holm 0.00 (0 vs 25 cases)
- gpt-5.6-sol: prior − sql+time: correct -79pt (-90pt, -67pt); fault_ok -68pt (-81pt, -53pt); usd -0.089 (-0.099, -0.080); McNemar p=0.00, Holm 0.00 (0 vs 25 cases)
- gpt-5.6-sol: sql − sql+time: correct -1pt (-9pt, +7pt); fault_ok -1pt (-9pt, +7pt); usd +0.021 (+0.014, +0.028); McNemar p=1.00, Holm 1.00 (1 vs 1 cases)
- digest: claude-opus-5-5 − gpt-5.6-sol: correct +2pt (-11pt, +16pt); usd -0.004 (-0.006, -0.002); McNemar p=1.00, Holm 1.00 (3 vs 2 cases)
- digest+time: claude-opus-5-5 − gpt-5.6-sol: correct +3pt (-2pt, +10pt); usd -0.006 (-0.008, -0.003); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- prior: claude-opus-5-5 − gpt-5.6-sol: correct +0pt (-3pt, +3pt); usd -0.001 (-0.001, -0.001); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- sql: claude-opus-5-5 − gpt-5.6-sol: correct +10pt (+3pt, +18pt); usd -0.062 (-0.068, -0.056); McNemar p=0.25, Holm 0.25 (3 vs 0 cases)
- sql+time: claude-opus-5-5 − gpt-5.6-sol: correct -8pt (-23pt, +7pt); usd -0.062 (-0.070, -0.054); McNemar p=0.22, Holm 0.22 (1 vs 5 cases)

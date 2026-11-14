| Model | Interface | Cases × trials | Service AC@1 | AC@3 | Fault type | Both right | pass^k | Input tok (med) | $/run (med) | $/correct | Calls (med) | Tool errors | Inspected@root |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | digest | 30 × 3.0 | 87% (73%–97%) | 96% | 60% (43%–77%) | 52% (34%–70%) | 50% | 6948 | 0.016 | 0.037 | 3 | 0% | — |
| claude-opus-5-5 | digest+lessons | 30 × 3.0 | 90% (80%–100%) | 97% | 87% (74%–97%) | 79% (64%–92%) | 77% | 9651 | 0.021 | 0.028 | 3 | 0% | — |
| claude-opus-5-5 | digest+placebo | 30 × 3.0 | 87% (73%–97%) | 97% | 63% (47%–80%) | 53% (37%–70%) | 50% | 9366 | 0.018 | 0.039 | 3 | 0% | — |
| claude-opus-5-5 | digest+time | 30 × 3.0 | 87% (73%–97%) | 97% | 60% (44%–76%) | 51% (34%–69%) | 47% | 7016 | 0.017 | 0.036 | 3 | 0% | — |
| claude-opus-5-5 | digest-decoy | 30 × 3.0 | 88% (74%–98%) | 97% | 67% (51%–82%) | 59% (42%–74%) | 53% | 7230 | 0.022 | 0.040 | 4 | 0% | — |
| claude-opus-5-5 | digest-placebo | 30 × 3.0 | 88% (76%–98%) | 96% | 72% (57%–87%) | 66% (49%–81%) | 60% | 7082 | 0.017 | 0.030 | 3 | 0% | — |
| claude-opus-5-5 | files | 30 × 3.0 | 100% (100%–100%) | 100% | 82% (71%–92%) | 82% (71%–92%) | 70% | 17469 | 0.058 | 0.079 | 4 | 1% | 83%* |
| claude-opus-5-5 | mcp | 30 × 3.0 | 100% (100%–100%) | 100% | 62% (47%–77%) | 62% (47%–77%) | 47% | 163307 | 0.169 | 0.278 | 9 | 12% | — |
| claude-opus-5-5 | prior | 30 × 3.0 | 14% (6%–26%) | 39% | 17% (3%–30%) | 2% (0%–7%) | 0% | 1004 | 0.005 | 0.243 | 0 | 0% | — |
| claude-opus-5-5 | rcatools+time | 30 × 3.0 | 100% (100%–100%) | 100% | 58% (40%–76%) | 58% (40%–76%) | 53% | 14588 | 0.037 | 0.069 | 6 | 0% | 97% |
| claude-opus-5-5 | sql | 30 × 3.0 | 100% (100%–100%) | 100% | 90% (80%–98%) | 90% (80%–98%) | 80% | 12874 | 0.047 | 0.061 | 2 | 0% | 100%* |
| claude-opus-5-5 | sql+lessons | 30 × 3.0 | 100% (100%–100%) | 100% | 93% (84%–100%) | 93% (84%–100%) | 90% | 17236 | 0.053 | 0.062 | 4 | 5% | — |
| claude-opus-5-5 | sql+placebo | 30 × 3.0 | 100% (100%–100%) | 100% | 74% (60%–88%) | 74% (60%–88%) | 67% | 21912 | 0.060 | 0.091 | 4 | 10% | — |
| claude-opus-5-5 | sql+time | 30 × 3.0 | 100% (100%–100%) | 100% | 73% (60%–86%) | 73% (60%–86%) | 60% | 7390 | 0.030 | 0.047 | 2 | 0% | 61%* |
| claude-opus-5-5 | visual | 30 × 3.0 | 99% (97%–100%) | 100% | 74% (59%–89%) | 73% (58%–87%) | 67% | 12886 | 0.044 | 0.063 | 3 | 0% | — |
| gpt-5.6-sol | digest | 30 × 3.0 | 83% (70%–97%) | 83% | 59% (41%–77%) | 50% (33%–67%) | 50% | 4060 | 0.020 | 0.046 | 4 | 0% | — |
| gpt-5.6-sol | digest+lessons | 30 × 3.0 | 84% (72%–94%) | 84% | 83% (70%–94%) | 72% (57%–86%) | 67% | 5706 | 0.022 | 0.033 | 4 | 0% | — |
| gpt-5.6-sol | digest+placebo | 30 × 3.0 | 79% (63%–92%) | 80% | 46% (29%–62%) | 38% (21%–56%) | 30% | 5644 | 0.022 | 0.063 | 5 | 0% | — |
| gpt-5.6-sol | digest+time | 30 × 3.0 | 84% (71%–97%) | 87% | 58% (41%–73%) | 48% (31%–64%) | 40% | 3967 | 0.020 | 0.051 | 4 | 0% | — |
| gpt-5.6-sol | digest-decoy | 30 × 3.0 | 81% (67%–93%) | 81% | 50% (33%–67%) | 44% (28%–61%) | 40% | 4144 | 0.025 | 0.062 | 5 | 0% | — |
| gpt-5.6-sol | digest-placebo | 30 × 3.0 | 84% (70%–97%) | 84% | 52% (36%–70%) | 43% (27%–61%) | 40% | 4066 | 0.020 | 0.054 | 5 | 0% | — |
| gpt-5.6-sol | files | 30 × 3.0 | 100% (100%–100%) | 100% | 84% (73%–94%) | 84% (73%–94%) | 73% | 37456 | 0.114 | 0.143 | 12 | 4% | 100%* |
| gpt-5.6-sol | mcp | 30 × 3.0 | 98% (94%–100%) | 99% | 68% (54%–81%) | 67% (53%–79%) | 43% | 123062 | 0.182 | 0.295 | 24 | 0% | — |
| gpt-5.6-sol | prior | 30 × 3.0 | 17% (8%–27%) | 38% | 13% (3%–26%) | 2% (0%–6%) | 0% | 405 | 0.006 | 0.280 | 0 | 0% | — |
| gpt-5.6-sol | rcatools+time | 30 × 3.0 | 100% (100%–100%) | 100% | 74% (59%–90%) | 74% (59%–90%) | 73% | 33864 | 0.094 | 0.134 | 22 | 1% | 100% |
| gpt-5.6-sol | sql | 30 × 3.0 | 100% (100%–100%) | 100% | 80% (67%–92%) | 80% (67%–92%) | 70% | 39528 | 0.108 | 0.146 | 12 | 1% | 100%* |
| gpt-5.6-sol | sql+lessons | 30 × 3.0 | 99% (97%–100%) | 99% | 79% (64%–91%) | 79% (64%–91%) | 73% | 43437 | 0.106 | 0.141 | 10 | 1% | — |
| gpt-5.6-sol | sql+placebo | 30 × 3.0 | 100% (100%–100%) | 100% | 73% (59%–87%) | 73% (59%–87%) | 63% | 48506 | 0.112 | 0.158 | 11 | 1% | — |
| gpt-5.6-sol | sql+time | 30 × 3.0 | 100% (100%–100%) | 100% | 81% (68%–92%) | 81% (68%–92%) | 70% | 27952 | 0.085 | 0.118 | 8 | 0% | 100%* |
| gpt-5.6-sol | visual | 30 × 3.0 | 99% (97%–100%) | 99% | 73% (59%–88%) | 72% (58%–86%) | 60% | 12310 | 0.052 | 0.085 | 7 | 0% | — |

Inspected@root (RCAEval's evidence metric): a tool call named the root-cause service before submit. Exact for rcatools (a service/name argument, RCAEval's rule); * = proxy for sql/files (the name appears as a token in the query/command); — = not measurable (digest, prior). rcatools always shows the injection time, so it is labelled +time.

No-LLM reference on the same cases: digest as ranking, no LLM, no time AC@1 70%, Avg@5 85%; N-Sigma (given time) AC@1 77%, Avg@5 89%; BARO (given time) AC@1 13%, Avg@5 75%; chance (5 candidates) AC@1 20%, Avg@5 60%

Paired differences (per case, mean of trials; 95% paired bootstrap):

- [interface] claude-opus-5-5: digest − files: correct -30pt (-52pt, -7pt); fault_ok -22pt (-43pt, +0pt); usd -0.046 (-0.054, -0.039); McNemar p=0.05, Holm 0.39 (4 vs 13 cases)
- [interface] claude-opus-5-5: digest − mcp: correct -10pt (-31pt, +11pt); fault_ok -2pt (-21pt, +18pt); usd -0.154 (-0.164, -0.144); McNemar p=0.39, Holm 1.00 (4 vs 8 cases)
- [interface] claude-opus-5-5: digest − prior: correct +50pt (+31pt, +69pt); fault_ok +43pt (+22pt, +66pt); usd +0.014 (+0.011, +0.017); McNemar p=0.00, Holm 0.00 (16 vs 1 cases)
- [interface] claude-opus-5-5: digest − sql: correct -38pt (-58pt, -17pt); fault_ok -30pt (-49pt, -10pt); usd -0.035 (-0.042, -0.029); McNemar p=0.00, Holm 0.02 (1 vs 13 cases)
- [interface] claude-opus-5-5: digest − visual: correct -21pt (-41pt, +0pt); fault_ok -14pt (-33pt, +6pt); usd -0.027 (-0.031, -0.023); McNemar p=0.09, Holm 0.46 (3 vs 10 cases)
- [interface] claude-opus-5-5: files − mcp: correct +20pt (+10pt, +31pt); fault_ok +20pt (+10pt, +31pt); usd -0.108 (-0.119, -0.098); McNemar p=0.06, Holm 0.44 (5 vs 0 cases)
- [interface] claude-opus-5-5: files − prior: correct +80pt (+68pt, +91pt); fault_ok +66pt (+50pt, +80pt); usd +0.060 (+0.052, +0.068); McNemar p=0.00, Holm 0.00 (24 vs 0 cases)
- [interface] claude-opus-5-5: files − sql: correct -8pt (-16pt, -1pt); fault_ok -8pt (-16pt, -1pt); usd +0.010 (+0.004, +0.018); McNemar p=0.38, Holm 1.00 (1 vs 4 cases)
- [interface] claude-opus-5-5: files − visual: correct +9pt (-4pt, +23pt); fault_ok +8pt (-6pt, +21pt); usd +0.019 (+0.012, +0.027); McNemar p=0.69, Holm 1.00 (4 vs 2 cases)
- [interface] claude-opus-5-5: mcp − prior: correct +60pt (+44pt, +76pt); fault_ok +46pt (+30pt, +61pt); usd +0.168 (+0.157, +0.179); McNemar p=0.00, Holm 0.00 (19 vs 0 cases)
- [interface] claude-opus-5-5: mcp − sql: correct -28pt (-40pt, -17pt); fault_ok -28pt (-40pt, -17pt); usd +0.119 (+0.108, +0.129); McNemar p=0.01, Holm 0.07 (0 vs 8 cases)
- [interface] claude-opus-5-5: mcp − visual: correct -11pt (-21pt, -2pt); fault_ok -12pt (-22pt, -3pt); usd +0.127 (+0.117, +0.137); McNemar p=0.25, Holm 1.00 (0 vs 3 cases)
- [interface] claude-opus-5-5: prior − sql: correct -88pt (-96pt, -78pt); fault_ok -73pt (-87pt, -58pt); usd -0.049 (-0.058, -0.041); McNemar p=0.00, Holm 0.00 (0 vs 27 cases)
- [interface] claude-opus-5-5: prior − visual: correct -71pt (-86pt, -56pt); fault_ok -58pt (-74pt, -41pt); usd -0.041 (-0.046, -0.036); McNemar p=0.00, Holm 0.00 (0 vs 22 cases)
- [interface] claude-opus-5-5: sql − visual: correct +17pt (+6pt, +29pt); fault_ok +16pt (+4pt, +28pt); usd +0.008 (+0.003, +0.014); McNemar p=0.06, Holm 0.44 (5 vs 0 cases)
- [interface+time] claude-opus-5-5: digest+time − rcatools+time: correct -7pt (-29pt, +18pt); fault_ok +2pt (-18pt, +23pt); usd -0.021 (-0.026, -0.017); McNemar p=0.79, Holm 0.79 (6 vs 8 cases)
- [interface+time] claude-opus-5-5: digest+time − sql+time: correct -22pt (-39pt, -6pt); fault_ok -13pt (-28pt, +1pt); usd -0.016 (-0.020, -0.012); McNemar p=0.15, Holm 0.44 (3 vs 9 cases)
- [interface+time] claude-opus-5-5: rcatools+time − sql+time: correct -16pt (-34pt, +2pt); fault_ok -16pt (-34pt, +2pt); usd +0.006 (+0.000, +0.011); McNemar p=0.34, Holm 0.69 (3 vs 7 cases)
- [lessons] claude-opus-5-5: digest+lessons − digest+placebo: correct +26pt (+12pt, +40pt); fault_ok +23pt (+8pt, +39pt); usd +0.001 (-0.001, +0.004); McNemar p=0.00, Holm 0.02 (9 vs 0 cases)
- [lessons] claude-opus-5-5: digest+lessons − digest: correct +27pt (+12pt, +42pt); fault_ok +27pt (+12pt, +42pt); usd +0.003 (+0.000, +0.005); McNemar p=0.01, Holm 0.02 (8 vs 0 cases)
- [time] claude-opus-5-5: digest − digest+time: correct +1pt (-4pt, +8pt); fault_ok +0pt (-6pt, +7pt); usd +0.001 (-0.000, +0.002); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- [lessons] claude-opus-5-5: sql+lessons − sql+placebo: correct +19pt (+2pt, +37pt); fault_ok +19pt (+2pt, +37pt); usd -0.010 (-0.019, -0.001); McNemar p=0.18, Holm 0.36 (7 vs 2 cases)
- [lessons] claude-opus-5-5: sql+lessons − sql: correct +3pt (-8pt, +14pt); fault_ok +3pt (-8pt, +14pt); usd +0.003 (-0.004, +0.010); McNemar p=1.00, Holm 1.00 (2 vs 2 cases)
- [time] claude-opus-5-5: sql − sql+time: correct +17pt (+4pt, +30pt); fault_ok +17pt (+4pt, +30pt); usd +0.020 (+0.015, +0.027); McNemar p=0.02, Holm 0.03 (7 vs 0 cases)
- [controls] claude-opus-5-5: digest − digest-decoy: correct -7pt (-19pt, +6pt); fault_ok -7pt (-20pt, +6pt); usd -0.004 (-0.006, -0.002); McNemar p=1.00, Holm 1.00 (2 vs 3 cases)
- [controls] claude-opus-5-5: digest-decoy − digest-placebo: correct -7pt (-17pt, +1pt); fault_ok -6pt (-17pt, +4pt); usd +0.004 (+0.002, +0.005); McNemar p=0.38, Holm 0.75 (1 vs 4 cases)
- [controls] claude-opus-5-5: digest − digest-placebo: correct -13pt (-26pt, -3pt); fault_ok -12pt (-24pt, -1pt); usd -0.001 (-0.002, +0.001); McNemar p=0.12, Holm 0.38 (0 vs 4 cases)
- [interface] gpt-5.6-sol: digest − files: correct -34pt (-53pt, -14pt); fault_ok -26pt (-44pt, -8pt); usd -0.097 (-0.104, -0.091); McNemar p=0.00, Holm 0.02 (1 vs 13 cases)
- [interface] gpt-5.6-sol: digest − mcp: correct -17pt (-38pt, +4pt); fault_ok -9pt (-30pt, +11pt); usd -0.174 (-0.191, -0.158); McNemar p=0.12, Holm 0.71 (4 vs 11 cases)
- [interface] gpt-5.6-sol: digest − prior: correct +48pt (+29pt, +67pt); fault_ok +46pt (+28pt, +63pt); usd +0.017 (+0.014, +0.020); McNemar p=0.00, Holm 0.00 (15 vs 0 cases)
- [interface] gpt-5.6-sol: digest − sql: correct -30pt (-50pt, -9pt); fault_ok -21pt (-40pt, -3pt); usd -0.094 (-0.103, -0.085); McNemar p=0.01, Holm 0.12 (2 vs 12 cases)
- [interface] gpt-5.6-sol: digest − visual: correct -22pt (-40pt, -3pt); fault_ok -14pt (-32pt, +3pt); usd -0.038 (-0.044, -0.033); McNemar p=0.04, Holm 0.31 (2 vs 10 cases)
- [interface] gpt-5.6-sol: files − mcp: correct +18pt (+8pt, +29pt); fault_ok +17pt (+7pt, +28pt); usd -0.076 (-0.092, -0.062); McNemar p=0.06, Holm 0.44 (5 vs 0 cases)
- [interface] gpt-5.6-sol: files − prior: correct +82pt (+70pt, +92pt); fault_ok +71pt (+57pt, +84pt); usd +0.114 (+0.107, +0.123); McNemar p=0.00, Holm 0.00 (27 vs 0 cases)
- [interface] gpt-5.6-sol: files − sql: correct +4pt (-1pt, +11pt); fault_ok +4pt (-1pt, +11pt); usd +0.004 (-0.003, +0.010); McNemar p=0.50, Holm 1.00 (2 vs 0 cases)
- [interface] gpt-5.6-sol: files − visual: correct +12pt (+3pt, +22pt); fault_ok +11pt (+2pt, +22pt); usd +0.059 (+0.052, +0.066); McNemar p=0.12, Holm 0.71 (4 vs 0 cases)
- [interface] gpt-5.6-sol: mcp − prior: correct +64pt (+51pt, +78pt); fault_ok +54pt (+38pt, +71pt); usd +0.191 (+0.174, +0.209); McNemar p=0.00, Holm 0.00 (22 vs 0 cases)
- [interface] gpt-5.6-sol: mcp − sql: correct -13pt (-23pt, -4pt); fault_ok -12pt (-21pt, -3pt); usd +0.080 (+0.066, +0.095); McNemar p=0.38, Holm 1.00 (1 vs 4 cases)
- [interface] gpt-5.6-sol: mcp − visual: correct -6pt (-16pt, +4pt); fault_ok -6pt (-16pt, +6pt); usd +0.136 (+0.122, +0.151); McNemar p=1.00, Holm 1.00 (2 vs 3 cases)
- [interface] gpt-5.6-sol: prior − sql: correct -78pt (-90pt, -64pt); fault_ok -67pt (-80pt, -52pt); usd -0.111 (-0.121, -0.101); McNemar p=0.00, Holm 0.00 (0 vs 25 cases)
- [interface] gpt-5.6-sol: prior − visual: correct -70pt (-84pt, -56pt); fault_ok -60pt (-76pt, -43pt); usd -0.055 (-0.062, -0.049); McNemar p=0.00, Holm 0.00 (0 vs 23 cases)
- [interface] gpt-5.6-sol: sql − visual: correct +8pt (+1pt, +16pt); fault_ok +7pt (+0pt, +14pt); usd +0.056 (+0.047, +0.065); McNemar p=0.62, Holm 1.00 (3 vs 1 cases)
- [interface+time] gpt-5.6-sol: digest+time − rcatools+time: correct -27pt (-48pt, -6pt); fault_ok -17pt (-37pt, +2pt); usd -0.075 (-0.082, -0.069); McNemar p=0.06, Holm 0.11 (3 vs 11 cases)
- [interface+time] gpt-5.6-sol: digest+time − sql+time: correct -33pt (-52pt, -13pt); fault_ok -23pt (-42pt, -6pt); usd -0.072 (-0.081, -0.062); McNemar p=0.01, Holm 0.02 (2 vs 13 cases)
- [interface+time] gpt-5.6-sol: rcatools+time − sql+time: correct -7pt (-19pt, +3pt); fault_ok -7pt (-19pt, +3pt); usd +0.004 (-0.004, +0.011); McNemar p=0.25, Holm 0.25 (0 vs 3 cases)
- [lessons] gpt-5.6-sol: digest+lessons − digest+placebo: correct +34pt (+18pt, +52pt); fault_ok +38pt (+19pt, +56pt); usd -0.000 (-0.003, +0.003); McNemar p=0.01, Holm 0.05 (10 vs 1 cases)
- [lessons] gpt-5.6-sol: digest+lessons − digest: correct +22pt (+6pt, +40pt); fault_ok +24pt (+8pt, +42pt); usd +0.001 (-0.002, +0.003); McNemar p=0.11, Holm 0.33 (8 vs 2 cases)
- [time] gpt-5.6-sol: digest − digest+time: correct +2pt (-6pt, +10pt); fault_ok +1pt (-8pt, +10pt); usd -0.001 (-0.003, +0.001); McNemar p=1.00, Holm 1.00 (2 vs 1 cases)
- [lessons] gpt-5.6-sol: sql+lessons − sql+placebo: correct +6pt (-18pt, +29pt); fault_ok +6pt (-18pt, +29pt); usd -0.005 (-0.016, +0.007); McNemar p=0.79, Holm 1.00 (8 vs 6 cases)
- [lessons] gpt-5.6-sol: sql+lessons − sql: correct -1pt (-22pt, +20pt); fault_ok -1pt (-22pt, +20pt); usd -0.006 (-0.018, +0.005); McNemar p=1.00, Holm 1.00 (5 vs 6 cases)
- [time] gpt-5.6-sol: sql − sql+time: correct -1pt (-9pt, +7pt); fault_ok -1pt (-9pt, +7pt); usd +0.021 (+0.014, +0.028); McNemar p=1.00, Holm 1.00 (1 vs 1 cases)
- [controls] gpt-5.6-sol: digest − digest-decoy: correct +6pt (-3pt, +17pt); fault_ok +9pt (+0pt, +20pt); usd -0.004 (-0.006, -0.002); McNemar p=0.50, Holm 1.00 (2 vs 0 cases)
- [controls] gpt-5.6-sol: digest-decoy − digest-placebo: correct +1pt (-10pt, +11pt); fault_ok -2pt (-14pt, +9pt); usd +0.004 (+0.001, +0.007); McNemar p=1.00, Holm 1.00 (2 vs 2 cases)
- [controls] gpt-5.6-sol: digest − digest-placebo: correct +7pt (-3pt, +18pt); fault_ok +7pt (-4pt, +18pt); usd -0.000 (-0.002, +0.002); McNemar p=0.62, Holm 1.00 (3 vs 1 cases)
- [models] digest: claude-opus-5-5 − gpt-5.6-sol: correct +2pt (-11pt, +16pt); usd -0.004 (-0.006, -0.002); McNemar p=1.00, Holm 1.00 (3 vs 2 cases)
- [models] digest+lessons: claude-opus-5-5 − gpt-5.6-sol: correct +7pt (-1pt, +16pt); usd -0.002 (-0.005, +0.001); McNemar p=0.25, Holm 0.25 (3 vs 0 cases)
- [models] digest+placebo: claude-opus-5-5 − gpt-5.6-sol: correct +16pt (+4pt, +29pt); usd -0.003 (-0.005, -0.001); McNemar p=0.38, Holm 0.38 (4 vs 1 cases)
- [models] digest+time: claude-opus-5-5 − gpt-5.6-sol: correct +3pt (-2pt, +10pt); usd -0.006 (-0.008, -0.003); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- [models] digest-decoy: claude-opus-5-5 − gpt-5.6-sol: correct +14pt (+3pt, +27pt); usd -0.004 (-0.006, -0.002); McNemar p=0.12, Holm 0.12 (4 vs 0 cases)
- [models] digest-placebo: claude-opus-5-5 − gpt-5.6-sol: correct +22pt (+10pt, +37pt); usd -0.003 (-0.006, -0.001); McNemar p=0.02, Holm 0.02 (7 vs 0 cases)
- [models] files: claude-opus-5-5 − gpt-5.6-sol: correct -2pt (-8pt, +3pt); usd -0.055 (-0.061, -0.049); McNemar p=0.50, Holm 0.50 (0 vs 2 cases)
- [models] mcp: claude-opus-5-5 − gpt-5.6-sol: correct -4pt (-20pt, +10pt); usd -0.024 (-0.042, -0.007); McNemar p=0.75, Holm 0.75 (4 vs 6 cases)
- [models] prior: claude-opus-5-5 − gpt-5.6-sol: correct +0pt (-3pt, +3pt); usd -0.001 (-0.001, -0.001); McNemar p=1.00, Holm 1.00 (1 vs 0 cases)
- [models] rcatools+time: claude-opus-5-5 − gpt-5.6-sol: correct -17pt (-33pt, -0pt); usd -0.059 (-0.067, -0.052); McNemar p=0.12, Holm 0.12 (1 vs 6 cases)
- [models] sql: claude-opus-5-5 − gpt-5.6-sol: correct +10pt (+3pt, +18pt); usd -0.062 (-0.068, -0.056); McNemar p=0.25, Holm 0.25 (3 vs 0 cases)
- [models] sql+lessons: claude-opus-5-5 − gpt-5.6-sol: correct +14pt (+3pt, +28pt); usd -0.053 (-0.060, -0.045); McNemar p=0.12, Holm 0.12 (4 vs 0 cases)
- [models] sql+placebo: claude-opus-5-5 − gpt-5.6-sol: correct +1pt (-8pt, +10pt); usd -0.048 (-0.057, -0.040); McNemar p=1.00, Holm 1.00 (2 vs 1 cases)
- [models] sql+time: claude-opus-5-5 − gpt-5.6-sol: correct -8pt (-23pt, +7pt); usd -0.062 (-0.070, -0.054); McNemar p=0.22, Holm 0.22 (1 vs 5 cases)
- [models] visual: claude-opus-5-5 − gpt-5.6-sol: correct +1pt (-8pt, +10pt); usd -0.015 (-0.021, -0.009); McNemar p=1.00, Holm 1.00 (2 vs 2 cases)
Decoy capture: how often the innocent service named by the inserted digest line is the agent's first answer (base rate = same service on arms without the line):

| Model | Interface | Cases × runs | Decoy named first | Decoy in top 5 | Service AC@1 |
|---|---|---|---|---|---|
| claude-opus-5-5 | digest | 30 × 90 | 1% (0%–3%) | 13% (3%–27%) | 87% (73%–97%) |
| claude-opus-5-5 | digest+lessons | 30 × 90 | 0% (0%–0%) | 12% (2%–24%) | 90% (80%–100%) |
| claude-opus-5-5 | digest+placebo | 30 × 90 | 1% (0%–3%) | 14% (3%–28%) | 87% (73%–97%) |
| claude-opus-5-5 | digest-decoy | 30 × 90 | 7% (0%–17%) | 94% (86%–100%) | 88% (74%–98%) |
| claude-opus-5-5 | digest-placebo | 30 × 90 | 7% (0%–17%) | 44% (28%–61%) | 88% (76%–98%) |
| claude-opus-5-5 | files | 30 × 90 | 0% (0%–0%) | 28% (13%–43%) | 100% (100%–100%) |
| claude-opus-5-5 | mcp | 30 × 90 | 0% (0%–0%) | 24% (10%–40%) | 100% (100%–100%) |
| claude-opus-5-5 | prior | 30 × 90 | 1% (0%–3%) | 10% (0%–20%) | 14% (6%–26%) |
| claude-opus-5-5 | rcatools+time | 30 × 90 | 0% (0%–0%) | 34% (19%–51%) | 100% (100%–100%) |
| claude-opus-5-5 | sql | 30 × 90 | 0% (0%–0%) | 29% (14%–44%) | 100% (100%–100%) |
| claude-opus-5-5 | sql+lessons | 30 × 90 | 0% (0%–0%) | 24% (10%–40%) | 100% (100%–100%) |
| claude-opus-5-5 | sql+placebo | 30 × 90 | 0% (0%–0%) | 22% (10%–37%) | 100% (100%–100%) |
| claude-opus-5-5 | visual | 30 × 90 | 0% (0%–0%) | 26% (12%–40%) | 99% (97%–100%) |
| gpt-5.6-sol | digest | 30 × 90 | 0% (0%–0%) | 3% (0%–10%) | 83% (70%–97%) |
| gpt-5.6-sol | digest+lessons | 30 × 90 | 0% (0%–0%) | 7% (0%–16%) | 84% (72%–94%) |
| gpt-5.6-sol | digest+placebo | 30 × 90 | 0% (0%–0%) | 3% (0%–10%) | 79% (63%–92%) |
| gpt-5.6-sol | digest-decoy | 30 × 90 | 7% (0%–16%) | 9% (0%–20%) | 81% (67%–93%) |
| gpt-5.6-sol | digest-placebo | 30 × 90 | 3% (0%–9%) | 6% (0%–14%) | 84% (70%–97%) |
| gpt-5.6-sol | files | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 100% (100%–100%) |
| gpt-5.6-sol | mcp | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 98% (94%–100%) |
| gpt-5.6-sol | prior | 30 × 90 | 3% (0%–8%) | 23% (13%–33%) | 17% (8%–27%) |
| gpt-5.6-sol | rcatools+time | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 100% (100%–100%) |
| gpt-5.6-sol | sql | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 100% (100%–100%) |
| gpt-5.6-sol | sql+lessons | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 99% (97%–100%) |
| gpt-5.6-sol | sql+placebo | 30 × 90 | 0% (0%–0%) | 0% (0%–0%) | 100% (100%–100%) |
| gpt-5.6-sol | visual | 30 × 90 | 0% (0%–0%) | 1% (0%–3%) | 99% (97%–100%) |
- claude-opus-5-5: digest-decoy − digest-placebo: decoy_first +0pt (+0, +0); ac1 +0pt (+0, +0)
- claude-opus-5-5: digest-decoy − digest: decoy_first +6pt (+0, +14); ac1 +1pt (+0, +3)
- claude-opus-5-5: digest-placebo − digest: decoy_first +6pt (+0, +14); ac1 +1pt (+0, +3)
- gpt-5.6-sol: digest-decoy − digest-placebo: decoy_first +3pt (-2, +11); ac1 -3pt (-9, +0)
- gpt-5.6-sol: digest-decoy − digest: decoy_first +7pt (+0, +16); ac1 -2pt (-8, +2)
- gpt-5.6-sol: digest-placebo − digest: decoy_first +3pt (+0, +9); ac1 +1pt (+0, +3)

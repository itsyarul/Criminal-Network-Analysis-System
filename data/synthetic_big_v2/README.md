# Synthetic Big v2 — Fresh Network D/E/F/Y/M (~2x)
Generated 2026-09-06T06:24:54.468937
Network: 78 (D/E/F/Y) + Noise: 35 = 113
Photos: 78 mapped (UUID orphans first)
CDRs: 1536
Transactions: 345 (flagged: 56)
FIRs: 73 (noise: 24)
Social: 132 (noise: 50)
Surveillance: 74 (noise: 28)
Intel: 61 (noise: 26)
Criminal History: 96 (noise: 18)
Aliases: 65
Ground truth: ground_truth_network_big.json with 90 edges
IDs: D1-D24, E1-E24, F1-F24, Y1-Y6, M1-M35 (fresh, no collision with A/B/C/X/N)
Phones: 70100xxxx, Accounts: AC0010xxxx (fresh block)
To test: python -m backend.loader --data-dir data/synthetic_big_v2 --out-dir output_big ; python -m backend.pipeline --data-dir data/synthetic_big_v2
Or copy: cp data/synthetic_big_v2/*.csv data/ ; cp data/synthetic_big_v2/people_directory.json data/

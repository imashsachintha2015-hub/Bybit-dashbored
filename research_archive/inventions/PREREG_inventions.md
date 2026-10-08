# Invention lab: pre-registration (written 2026-10-08 before any invention was run)
Common frame (same as the Kalman trend): OKX 1H d6 + d5 + d4 joined -> 4H, all coins, both price orientations; signal at a bar close,
market entry at the next open; protective stop 3 ATR(14) from the signal close; exit at the next open after the first close where the
invention's own trend measure is below 0 (or after 120 bars); 14 bps + 0.25 bp funding per bar; one open trade per coin and side.
Each invention runs with and without the BTC filter (trade only in the direction of BTC's 1D trend); DEV chooses on/off by t.

| name | idea | entry (long side; shorts are mirrored) | exit measure |
|---|---|---|---|
| KALMAN | benchmark (trendlines/tl_setups.py KALMAN1) | Kalman z crosses above 1 | z < 0 |
| NEWTON | constant-acceleration Kalman (level, velocity, acceleration; accel noise 1e-6 x R) | velocity z crosses above 1 while acceleration > 0 | velocity z < 0 |
| IMM | two Kalman trends (slope noise 3e-5 and 3e-4 x R) mixed by Bayesian model probabilities (stay prob 0.98) | mixed slope z crosses above 1 | mixed z < 0 |
| SWARM | market breadth: share of all coins whose Kalman z > 0 at that bar | Kalman z crosses above 1 and breadth >= 0.6 | Kalman z < 0 |
| HURST | persistence gate: variance ratio of 6-bar vs 1-bar log returns over 120 bars | Kalman z crosses above 1 and VR >= 1.1 | Kalman z < 0 |
| ENTROPY | order gate: permutation entropy (order 4) of the last 120 closes | Kalman z crosses above 1 and entropy below the 30th percentile of its previous 500 values | Kalman z < 0 |
| VOLCLOCK | Kalman in activity time: time step = volume / its 180-bar mean (clipped 0.2-5), noise scaled by it | volume-clock z crosses above 1 | volume-clock z < 0 |
| ELASTIC | rubber band: price below the Kalman level by >= 1 residual sd while Kalman z > 1 | first such bar in an up-trend | Kalman z < 0 |
| PATH | least resistance: at a Kalman entry, volume (last 180 bars) in (price, price + 3 ATR] < volume in [price - 3 ATR, price) | Kalman z crosses above 1 and the barrier above is lighter | Kalman z < 0 |

Segments: DEV = 19 design coins 2021-06 .. 2023-12 (selection only). VAL = design coins 2024. FINAL = design coins 2025-01 .. 2026-09.
UNSEEN = other coins 2021-06 .. 2026-09. OLD = all coins 2020-01 .. 2021-05 (never used for any invention).
An invention "beats the benchmark" if its average net R is higher than KALMAN with the BTC filter in at least 4 of the 4 holdout
segments VAL / FINAL / UNSEEN / OLD (3 of 4 = "partly"). Standalone pass: pooled holdouts average > 0 with t >= 2.
$10 portfolios: 0.5% risk, max 8 open, one per coin, for OLD (2020-21) and for 2024-2026 (all coins).

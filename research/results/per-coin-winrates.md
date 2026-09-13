# Per-coin win rates and the strategies behind them

Deployed geometry: stops 2.5x, invalidation 2.0x, swarm off, 20bps patient
maker entry with a 20-minute timeout, 0.5% risk per trade, costs charged at
5.5bps taker / 1.0bps maker / 2bps slippage.

Two tiers of evidence here, and they are not equally trustworthy. Read the
sample size before the win rate on every row.

## Tier 1 — 418 days, 236 trades (trustworthy)

| coin | n | win rate | expectancy | total R | baseline WR | baseline expR |
|---|---|---|---|---|---|---|
| **SOL** | 71 | **60.6%** | +0.239R | +16.98R | 42.9% | -0.021R |
| **BTC** | 51 | **66.7%** | +0.215R | +10.96R | 42% | -0.049R |
| **ETH** | 59 | **52.5%** | +0.097R | +5.75R | 36.2% | -0.184R |
| **DOGE** | 55 | **45.5%** | -0.151R | -8.31R | 30.9% | -0.559R |

## Strategies, aggregated across those 4 coins

| setup | n | win rate | total R | expectancy |
|---|---|---|---|---|
| **RANGE_FADE** | 157 | 61.1% | **+29.82R** | +0.190R |
| **BREAKOUT_RETEST** | 74 | 48.6% | **-0.80R** | -0.011R |
| **SWEEP_RECLAIM** | 5 | 20.0% | **-3.64R** | -0.728R |

## Per coin x strategy


**SOL** — 71 trades, 60.6% WR, +16.98R

| setup | n | WR | total R | expectancy |
|---|---|---|---|---|
| RANGE_FADE | 48 | 66.7% | +16.99R | +0.354R |
| SWEEP_RECLAIM | 2 | 50.0% | +0.03R | +0.015R |
| BREAKOUT_RETEST | 21 | 47.6% | -0.04R | -0.002R |

By regime: TREND n=21 WR=48% · RANGE n=50 WR=66%

**BTC** — 51 trades, 66.7% WR, +10.96R

| setup | n | WR | total R | expectancy |
|---|---|---|---|---|
| RANGE_FADE | 34 | 70.6% | +10.31R | +0.303R |
| BREAKOUT_RETEST | 16 | 62.5% | +1.01R | +0.063R |
| SWEEP_RECLAIM | 1 | 0.0% | -0.36R | -0.359R |

By regime: RANGE n=35 WR=69% · TREND n=16 WR=62%

**ETH** — 59 trades, 52.5% WR, +5.75R

| setup | n | WR | total R | expectancy |
|---|---|---|---|---|
| RANGE_FADE | 38 | 60.5% | +8.00R | +0.211R |
| BREAKOUT_RETEST | 20 | 40.0% | -0.13R | -0.006R |
| SWEEP_RECLAIM | 1 | 0.0% | -2.13R | -2.130R |

By regime: RANGE n=39 WR=59% · TREND n=20 WR=40%

**DOGE** — 55 trades, 45.5% WR, -8.31R

| setup | n | WR | total R | expectancy |
|---|---|---|---|---|
| SWEEP_RECLAIM | 1 | 0.0% | -1.18R | -1.180R |
| BREAKOUT_RETEST | 17 | 47.1% | -1.64R | -0.097R |
| RANGE_FADE | 37 | 45.9% | -5.49R | -0.148R |

By regime: TREND n=17 WR=47% · RANGE n=38 WR=45%

## Tier 2 — 42 days only, 5-19 trades per coin (NOT trustworthy)

These eight were added purely to reach a ~100-trade sample. At five to
nineteen trades each, a standard error on the win rate is roughly 12-22
points, so every row below is consistent with a coin flip. They are listed
for completeness, not for acting on.

| coin | n | win rate | expectancy | total R | strategies |
|---|---|---|---|---|---|
| ATOM | 19 | 73.7% | +0.509R | +9.67R | RANGE_FADE n=14 71%; BREAKOUT_RETEST n=5 80% |
| BCH | 5 | 100% | +1.063R | +5.31R | RANGE_FADE n=4 100%; BREAKOUT_RETEST n=1 100% |
| LTC | 7 | 42.9% | +0.107R | +0.75R | RANGE_FADE n=6 50%; BREAKOUT_RETEST n=1 0% |
| ADA | 13 | 46.2% | +0.005R | +0.06R | BREAKOUT_RETEST n=7 43%; RANGE_FADE n=6 50% |
| XRP | 5 | 40% | -0.355R | -1.78R | RANGE_FADE n=2 50%; BREAKOUT_RETEST n=3 33% |
| DOT | 10 | 30% | -0.220R | -2.20R | RANGE_FADE n=4 50%; BREAKOUT_RETEST n=6 17% |
| LINK | 10 | 40% | -0.362R | -3.62R | BREAKOUT_RETEST n=6 67%; RANGE_FADE n=4 0% |
| AVAX | 10 | 20% | -0.687R | -6.87R | BREAKOUT_RETEST n=2 50%; RANGE_FADE n=8 12% |

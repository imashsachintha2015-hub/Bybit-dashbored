# Trend lines without noise, trend reversals, and the setups built from them (4H)

**Data.** OKX 1H candles joined into one series per coin and aggregated to 4H: d5 (2021-06 to 2024-04) plus d4 (2024-04 to 2026-09).

**Coin and period split** (the same coin split as `scenario_research/scen_an.py`):

| Segment | Coins | Period | Used for |
|---|---|---|---|
| DEV | 19 design coins | 2021-06 to 2023-12 | design and selection |
| VAL | 19 design coins | 2024 | validation |
| FINAL | 19 design coins | 2025-01 to 2026-09 | one look |
| UNSEEN | the other coins | all periods | one look |

A final pre-registered test ran on 2020-01 to 2021-05 (d6), which was never used before (`PREREG_trend_2020.md`).

**Costs.** 14 bps per trade plus 0.25 bp funding per 4H bar. On 4H that is about 0.07 to 0.1 ATR, or 0.01 to 0.03 R with these stops.

## Engine (`tl_core.py`): everything causal, no repainting (`tl_core_out.txt`: 48 cut-off points × 28 features, 0 mismatches)

- **Noise filter.** A swing exists only after price has reversed 3 ATR(14) from it; the bar where that happens is its confirmation bar.
- **Swing trend lines.**
  - **Uptrend line:** drawn through the last two higher lows.
  - **Downtrend line:** drawn through the last two lower highs.
  - **Life of a line:** it exists from the bar after confirmation until a close 0.25 ATR beyond it.
  - **Touches:** later swings within 0.35 ATR of the line count as touches.
  - **Steeper redraws:** a higher low above the line redraws a steeper line, and the old slope is kept as "acceleration".
  - **Tests:** visits to the line's zone that do not close beyond it.
- **Optimized "no-violation" lines (windows of 48 and 96 bars).** The line passes through the most extreme bar relative to a regression fit. Its slope is the least-squares slope, clipped so that no bar in the window crosses the line.
- **Noise-free trend.** A local-linear-trend Kalman filter on log price; z = slope / standard error.

## Part 1: how price acts at a trend line (`tl_study_out.txt`, DEV only, 1,683 tests and 1,378 breaks)

**Tests.**
- Overall, a test holds for 6 bars only 43% of the time.
- Flat lines hold more often than steep ones: 51% vs 33%.
- The average move after a test is +0.26 ATR over 24 bars (continuation in the line's trend direction).
- Best groups:
  - a rejection candle on the line: holds 55%, +0.43 ATR;
  - BTC trending the same way: +0.44 ATR.

**Breaks.**
- An ordinary break of a 2-touch line has no follow-through: −0.07 ATR over 24 bars. 22% are false breaks, and 62% are retested.
- Breaks of strong lines do follow through, but they are rare:
  - 3+ touches: +0.72 ATR (n=76);
  - 2+ prior tests: +0.97 ATR (n=94).
- Breaks of accelerated (steepened) lines tend to reverse back: −0.24 ATR.

## Part 2: trend reversals (DEV; tops = ends of up-legs of at least 6 ATR, labelled with hindsight; detectors use none)

There were 1,028 tops. The following decline averaged 12.2 ATR (median 8.7).

| Detector | Signals during a real decline | Next 24 bars, in the signal's direction | Tops caught | ATR given up before the signal | Share of the decline captured |
|---|---|---|---|---|---|
| Trend-line break | 70% | −0.06 ATR | 72% | 4.8 | 53% |
| 3+ touch line break | 80% | +0.79 ATR | 6% | 4.5 | 50% |
| CHoCH (close below the last swing low) | 91% | +0.39 ATR | 75% | 6.0 | 43% |
| Optimized-line break | 77% | +0.14 ATR | 92% | 3.9 | 57% |
| Kalman flip | 69% | +0.10 ATR | 91% | 3.9 | 55% |
| EMA 20/50 cross | 78% | +0.18 ATR | 78% | 4.8 | 49% |

Every reliable reversal signal is late. It gives up 4 to 6 ATR of the move and pays for that by being right about direction.

## Part 3: setups (`tl_setups.py`, `tl_setups_out.txt`)

**Search.**
- 43 configs were tried.
- On DEV, 0 configs reached t ≥ 3 (about 0.06 expected by luck), and 2 reached t ≥ 2 (about 1.0 expected).
- No config passed the FINAL + UNSEEN bar.

**Trend-following family.** It stayed positive in every segment, though only by a small amount:

| Setup | DEV | VAL | FINAL | UNSEEN |
|---|---|---|---|---|
| Kalman trend | +0.13R | +0.30R | +0.01R | +0.06R |
| Optimized-line breakout | +0.05R | +0.08R | +0.06R | +0.06R |
| Strong-line break | +0.18R | +0.42R | +0.06R | +0.09R |
| CHoCH | +0.10R | +0.06R | +0.01R | +0.02R |

**Counter-trend ideas** failed out of sample: the false break ("spring") and the trend-line bounce.

## Pre-registered test on never-used data, 2020-01 to 2021-05 (`tl_prereg2020_out.txt`)

Pass rule: t ≥ 2.0, or strict t ≥ 2.33.

| Setup | Trades | Avg per trade | t | $10 at 1% risk | $10 at 2% risk | Verdict |
|---|---|---|---|---|---|---|
| Kalman trend | 943 | +0.462R | 2.5 | $39.74 (226 trades taken, max drawdown −12%) | $101.23 (max drawdown −23%) | PASS strict |
| CHoCH | 567 | +0.168R | 2.3 | $12.78 | — | PASS |
| Optimized-line breakout | 570 | +0.172R | 1.7 | — | — | fail |
| Strong-line break | 62 | +0.131R | 0.7 | — | — | fail |
| All four as one portfolio | 2,142 | +0.297R | 2.9 | $12.17 (232 trades taken) | — | PASS strict |

**Caveat, stated before the test:** 2020-21 was a strong bull market. In the Kalman trend:
- longs averaged +1.09R and shorts −0.15R;
- in 2025-26 it averaged −0.006R.

The edge is regime-dependent trend following: it pays in trending markets and is flat in choppy ones.

## Kalman trend rules (frozen)
- **Signal:** on 4H closes, run a local-linear-trend Kalman filter on log price.
  - Observation noise: EWMA (span 100) of squared 1-bar log returns.
  - Slope noise: 1e-4 times that.
  - Signal value z = slope / its standard error.
- **Entry:** go long at the next open when z crosses above +1. Go short (mirror) when z crosses below −1.
- **Stop:** 3 ATR from the signal close.
- **Exit:** at the next open after the first close where z has crossed back through 0, or after at most 120 bars.
- **Costs and sizing:** market orders. Size by risk (1% recommended), max 3 open positions, one per coin.

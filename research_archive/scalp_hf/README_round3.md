# Round 3: four new mechanisms (liquidation map, pairs arbitrage, trend days, smarter Kalman entries)

**Answer.** Nothing new passes.
- 0 of 76 pre-registered cells passed the DEV screen, plus 0 of 16 in the maker follow-up.
- The most interesting finding is real but too small to trade: **selling toward clusters of estimated long liquidations below the price.**
  - Taker execution: the edge before costs is +10 to +16 bps per trade, in all 8 DEV variants, but the fees are 14 bps.
  - Maker execution: it made money in DEV and VAL ($10 → $12.30 and $12.05), then lost in FINAL ($7.34), on unseen coins ($6.69) and in 2021-22.
- **Intraday trend days entered at 08:00 UTC** were the only thing positive in every period. But the edge is tiny (t < 1.1) and makes $10 into about $10.30–$10.70.
- **For the live Kalman system:** limit-order entries do not beat market entries. Skipping signals the limit order misses loses most of the edge, because the missed ones are the winners.

Rules: `PREREG_round3.md`, written and committed before any round-3 code touched data. Addendum 1 (the maker follow-up) was written after the DEV screen and carries a caveat. The data, periods, costs and $10 portfolio are the same as rounds 1-2.

## The four ideas
| Idea | What it is | Why it might work |
|---|---|---|
| **A. Liquidation map** (`r3_liq.py`) | Every 5-minute OI increase is placed as new longs / shorts (split by taker buying) at 10/25/50/100x leverage. Their liquidation prices go on a 0.1% price grid. Bins decay over time, shrink when OI falls, and are cleared when price trades through them. **MAGNET** trades toward the bigger cluster of fuel within 3%. **SWEEP** fades the bar after a large estimated liquidation cascade. | Exchanges must close leveraged positions at known prices; traders watch liquidation heatmaps. |
| **B. Pairs arbitrage** (`r3_pairs.py`) | All 45 pairs of coins on 1H bars. Hedge ratio from 30-day OLS. Trade the spread back to its mean when z goes beyond ±2 or ±2.5, with an optional half-life filter. | Coins share common drivers; spread deviations should close. |
| **C. Kalman entry** (`r3_kentry.py`) | The live 4H Kalman signals with a limit order 0.25 / 0.5 / 1 ATR better than the signal close, for 4h or 12h. If unfilled: skip, or enter at market. | A better price on the one system that works. |
| **D. Trend days** (`r3_trendday.py`) | At 08:00 / 12:00 / 16:00 UTC, if the day is up ≥ 0.5 or 0.8 daily ATR and trading near its high (or the mirror), ride it to the UTC close. | Strong one-sided days tend to continue. |

**Checks:**
- The liquidation map does not repaint: it is identical when the data is cut mid-way (`test_round3.py`).
- The map sizes are plausible: about $150m/day of estimated BTC liquidations in Q1 2023 against $2.3bn of open interest.
- The Kalman 1m baseline reproduces `hf_ladder.py` exactly: the same signals on all 20 coins, returns within 0–6 bps.

## Results: best DEV cell of each idea and side, frozen, run once ($10, 0.5% risk; trades = trades the portfolio took)
| Idea / side | Frozen cell | DEV | VAL | FINAL | UNSEEN coins | 2021-22 |
|---|---|---|---|---|---|---|
| MAGNET long | HL 1d, R ≥ 4, target 1R | $7.80 (576) | $7.14 (781) | $7.52 (470) | $2.93 (1751) | $5.57 (1336) |
| MAGNET short | HL 3d, R ≥ 4, target 1R | $9.43 (1291) | $9.91 (1567) | $6.74 (1251) | $5.29 (2731) | $5.17 (2611) |
| SWEEP (both sides) | all 16 cells −0.22 to −0.36R on DEV | — | — | — | — | — |
| PAIRS | Z 168h, k 2.5, half-life filter | $3.98 (1035) | $7.65 (666) | $6.78 (835) | $4.81 (871) | $2.59 (1256) |
| TREND DAY long | 08:00, k 0.5, stop at day low | $10.36 (111) | $10.23 (101) | $10.69 (117) | $10.30 (96) | $10.46 (97) |
| TREND DAY short | 08:00, k 0.5, stop at day high | $10.37 (86) | $10.02 (63) | $10.30 (144) | $10.54 (68) | $9.07 (68) |
| Maker MAGNET long (Addendum 1) | HL 1d, R ≥ 4, 1R | $9.22 (535) | $8.15 (743) | $7.81 (451) | $3.38 (1686) | $5.54 (1266) |
| Maker MAGNET short (Addendum 1) | HL 3d, R ≥ 4, 1R | $12.30 (1258) | $12.05 (1508) | $7.34 (1198) | $6.69 (2641) | $7.11 (2528) |

Notes on the table:
- The trend-day long rule is positive in all five periods: +0.07, +0.01, +0.11, +0.06 and +0.09R. But no period has t above 1.1, 4 of the 5 FINAL quarters were negative, and the profit is a few percent over 15 months. It is a weak lead, not a system.
- The trend-day short rule lost in 2021-22.

**Kalman entries:** 299 DEV signals; the baseline is market entry at the next 4H open.

| Kalman entry variant | DEV ΔR | Result |
|---|---|---|
| Limit 0.25 ATR, 4h, then market (best) | +0.025 (t 0.9) | VAL +0.003. FINAL: $12.94 → $13.57 (+0.037R, t 1.4). Unseen: $11.53 → $11.44 |
| Limit 0.5 / 1 ATR, skip if unfilled | −0.14 to −0.43 (fills 8–61%) | The missed signals are the winners |

So: keep market entries in the live system, or at most try a 0.25 ATR limit that falls back to market after one 4H bar.

## Why they fail: edge before costs (DEV, `r3_gross_dev.txt`)
- **MAGNET long:** about 0 (−0.03 to +0.04R).
- **MAGNET short:** +0.07 to +0.11R (t 2.0–2.6) against 0.09–0.19R of taker cost. The one real effect found in three rounds: price drifts down toward stacks of long liquidations more than chance.
  - With maker fills it nets +0.01 to +0.04R on DEV. Adverse selection takes the rest: a resting sell fills when price is rising, against the trade.
  - Out of sample it turns negative.
- **SWEEP:** about 0 (−0.03 to +0.02R) against 0.25–0.36R of cost. After an estimated cascade, price neither reverts nor continues.
- **PAIRS:** negative before costs (−0.07 to −0.12R). Spreads between coins do not mean-revert: 50–61% of trades win, but the losers (spreads that keep widening) are larger.
- **TREND DAY:** about 0 overall. At 16:00 UTC, shorts lose before costs (−0.08 to −0.14R): late-day down moves bounce back.

## Where this leaves things (rounds 1–3)
- Over 4,100 pre-registered variants have been tested across 1m, 5m, 15m, 1H and 4H-aligned rules:
  - price patterns, indicators, custom levels and machine learning;
  - liquidation maps, pairs arbitrage and trend days;
  - limit-order execution.
- Not one passes out of sample. The single persistent effect is liquidation-magnet shorts, worth about +10–16 bps before costs, which is less than any realistic retail fee.
- The 4H Kalman trend remains the only rule here that makes money in every period and on coins it never saw: $10 → $11.5–$17.1 in the table above.

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round3.md` (+ Addendum 1) |
| Code | `r3_common.py` (trade walk, screen / VAL / frozen runs), `r3_liq.py`, `r3_liq_maker.py`, `r3_pairs.py`, `r3_trendday.py`, `r3_kentry.py` |
| Tests | `test_round3.py` |
| Outputs | `r3_screen_out.txt` (every DEV cell), `r3_final_out.txt`, `r3_gross_dev.txt`, `r3_maker_screen_out.txt`, `r3_maker_final_out.txt`, `r3_frozen_*.json` |

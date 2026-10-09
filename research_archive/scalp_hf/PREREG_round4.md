# Round 4: why the setups lose, and whether fixing the causes turns them profitable (pre-registration)

**Request.** "Analyze why you lose them and make it turn into profit."

**Guardrail.** Any losing trade can be "explained" after the fact, and filtering out losers in hindsight always makes a backtest
profitable. So a fix counts only if:
- it is a mechanical rule using information known before entry;
- it is re-simulated on every trade (losing trades are never deleted);
- it is chosen on DEV and VAL of the design coins;
- it is then judged once on data no round has touched.

**The clean holdout (HOLDOUT2).** FINAL and the unseen coins have already been printed for these setups, so they are no longer clean.
The judge is 10 new coins, never loaded in any scalp round: AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI. They are tested on all of
2021-01..2026-09 (their OLD, DEV, VAL and FINAL periods). FINAL and UNSEEN of the old coins are still reported, labelled "seen before".

## Truth 1: what the losses are made of (DEV, design coins)
**All families of rounds 2-3:**
- Net R split into gross edge, fees and funding.
- How much of the loss would remain with zero fees.

**Candidates.** Only setups with a gross edge on DEV can become profitable; a setup with no edge before costs cannot be fixed by any
honest rule. The 10 candidates were chosen from the DEV tables already published (`r2_gross_dev.txt`, `r3_gross_dev.txt`):
1. MAGNET short (HL 3d, R >= 4): gross +0.09R.
2-5. FAKE_PD long / short, 5m / 15m: gross +0.05 to +0.08R.
6-7. BB_FADE 5m long / short: gross +0.05R with the trail.
8-9. CVD_DIV 15m long / short: gross +0.06R with the trail.
10. TRENDDAY long 08:00, k 0.5, stop at the day low: gross +0.10R.

**For each candidate:**
- MFE and MAE in R over the whole allowed holding time.
- Loser types: straight to the stop (MFE < 0.3R), gave back a winner (MFE >= 1R, then lost), timed out.
- Winners that nearly stopped out (MAE >= 0.8R).
- Cost in R against stop width.

## Truth 2: causes known before entry (13 candidate causes, declared now)
- stop width %
- 1H regime (UP / DOWN / RANGE / MIXED)
- volatility ratio (1H ATR% vs its 30-day median)
- hour (UTC)
- weekday
- coin 24h return, signed by side
- BTC 24h return, signed by side
- coin 4h return, signed by side
- last funding rate, signed by side
- relative volume (last 1h vs 24h average)
- distance to the daily VWAP in daily ATR, signed
- distance to the 1D EMA50 in daily ATR, signed (over-extension)
- 1D trend of the coin vs the trade side (with / against / flat)

**Method:**
- Net R by tercile of each cause; the tercile cut points are fixed on DEV. Hour, weekday and regime use their natural groups.
- A cause is **accepted** only if all three hold:
  - its worst group has DEV t <= -2;
  - the same group is negative on VAL;
  - removing it raises net R in both DEV and VAL.
- At most 2 causes per candidate.

## Truth 3: fixes (re-simulated, chosen on DEV and VAL)
**Menu:**
- Exit: target in {setup's own, 1, 1.5, 2, 3R, none}; breakeven stop after +1R in {off, on}; time limit x {0.5, 1, 2}.
- Stop width x {1, 1.5, 2} of the structural distance. Fewer fees per R; R targets scale with it, level targets stay where they are.
- Execution in {taker, maker entry}. Maker: limit at the signal close; fills on a trade-through of max(0.05 ATR(5m), 1 bp) within 3
  minutes, otherwise no trade.
- Filters: the accepted causes of Truth 2.

**Choice per candidate:** first the exit x width x execution combination (216). Then the filters, measured on top of it.
- The combination needs n >= 100 in DEV and in VAL, and net R > 0 in both.
- Among those, the highest net R on DEV and VAL pooled wins.
- Candidates with no such combination are reported as "not fixable".

## Truth 4: the judge (HOLDOUT2, run once)
**Pass, all required:**
- HOLDOUT2, 2021-01..2026-09 pooled: n >= 150, net R > 0, day-clustered t >= 2.0.
- Positive in at least 3 of its 4 periods.
- Still positive with +2 bps per side.
- $10 at 0.5% risk ends above $10 with max drawdown < 40%.

**Also reported:** design coins FINAL and UNSEEN ("seen before"), and the RANDOM-entry control with the same fixes. If the fixes make
random entries "profitable" too, they are fitting noise.

**Multiple testing:** 10 candidates go to HOLDOUT2. A candidate with no edge passes t >= 2.0 and 3 of 4 periods by chance with
probability about 0.02, so about 0.2 false passes in 10.

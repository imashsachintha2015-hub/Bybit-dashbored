# Loss diagnosis: the Four Noble Truths (Chathurarya Sathya) applied to our failed strategies
Dukkha = measure the loss exactly. Samudaya = find its cause, using only information known before entry, confirmed in a second period.
Nirodha = re-simulate with the cause removed (never by deleting losing trades). Magga = prove the fix on data the diagnosis never touched.
Costs: 14 bps market / 8 bps limit + 0.5 bp funding per 8h. $10 portfolio: max 3 open, one per coin.

## 1. Resonance rebuild (diag_res.py, fix_res.py; data and rules in ../resonance_test)
Dukkha (15m, DEV): CHoCH trigger gross +0.021R - fees 0.127R - funding 0.004R = -0.110R; EMA trigger gross -0.030R - fees 0.193R = -0.227R.
Of the stopped trades ~40% went straight to the stop, ~20% had been +1R first. Parity with res_ev.pkl: 172,425 events identical.
Samudaya: fees are 5-6x the gross edge (median stops 0.85-1.3%). One entry-time condition passed both checks: EMA trigger while BTC's
1D trend is flat (-0.61R vs the rest, t=-3.7 on DEV, still worse on VAL).
Nirodha (DEV, re-simulated): F1 limit entry helped 3/6 frozen configs (dropped); F2 skip BTC-flat 3/3 and F3 confirmation entry 4/6 (kept).
Magga: 15m with F2+F3, one look at FINAL + UNSEEN: all 6 fail (EMA from -0.2R to about 0 on unseen coins; best $10 -> $7.74 at 1% risk).
Fee cause, fixed one level up (RES_BASE=1H: 1H/2H/4H/12H/1D): fee 0.055R per trade instead of 0.14R; 40/135 configs net positive on
2024-26, but the DEV-frozen ones fail on FINAL (-0.08 .. +0.01R) and on 2021-24 (CHoCH -0.03 .. +0.01R, EMA -0.02 .. -0.12R).
Best on 2021-24: CHoCH, >=3 agree, ADX>=25, 2.25R, confirmation entry: +0.009R (n=3,258, t=0.2), $10 -> $13.44 at 1% (1,076 trades, DD -42%).
Verdict: the fee cause was real and removable; what is left has no edge.

## 2. AMD-FVG (amd_fillfix.py, fix_amd.py)
Samudaya found a measurement error in our only "winner": the fill rule checked "touch bar closed below the gap -> cancel" before "low
traded through the limit -> filled" on the same bar. A resting limit fills before the close exists, so 1,261 real fills were dropped over
2021-26 (avg -0.24R, 70% stopped). Corrected, 2021-26 all coins: NO_GATE +0.028R (t=0.8), PRIMARY +0.057R (t=1.3); 2021-24 alone -0.020R /
-0.004R, so the pre-registered 2021-24 "pass" came from the bug. Fixed in backend_lib/amd_fvg.py (forward lab) with parity 1,595/0.
Dukkha (corrected, NO_GATE): gross +0.065R - fees 0.031R - funding 0.006R = +0.028R; 59% stopped, 45% of those straight to the stop.
Samudaya: gaps run through on the first touch lose (-0.24R, 48% straight to stop); gaps that hold earn +0.15R (PRIMARY +0.19R).
Magga A, wait for the touch bar's close and enter at the next open only if the gap held (market): NO_GATE +0.006R, PRIMARY +0.053R.
The information costs about what it saves (worse entry price + market fees).
Magga B, walk-forward cause filters learned only from earlier years (leak-asserted): no improvement (PRIMARY limit 2023-26 +0.034 -> +0.029R).
By year (PRIMARY, corrected): 2021 +0.06, 2022 +0.17, 2023 -0.13, 2024 -0.15, 2025 +0.31, 2026 +0.12. Regime-dependent, no learnable filter.

## 3. Every scenario configuration (cause_map.py)
934 configs with >= 60 trades in 2024-26 and 2021-24. Same class in both periods: 89%. No edge before fees in both: 825; wrong
direction in both: 7; profitable in both: 0; killed only by fees in both: 0. Nothing to fix: these patterns do not predict direction.
Limit-entry families in scen.py / scen2.py used the same look-ahead fill order, so their numbers are an upper bound.

## Lessons
1. Split every result into gross edge and costs first: gross <= 0 means the idea is wrong; gross > 0 but net < 0 means it is too expensive.
2. A fill model must never use a bar's close to decide whether an order resting inside that bar was filled.
3. Information has a price: waiting for a candle close to avoid bad trades costs about as much as the bad trades.
4. Causes learned from past years did not carry over to the next year.

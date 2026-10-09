# Automated pattern-discovery loop (target: +0.5R net)
Engine (disc.py): six parametrized families -- SPRING (sweep+reclaim of a compressed range), CLIMAX (capitulation bar + half-body reclaim),
ABSORB (huge volume, tiny range, then break), IMBAL (displacement + BOS + FVG 50% limit retest), AMD (compression -> sweep -> displacement -> FVG retest),
VACUUM (breakout into a low-volume node). Random parameter search x 3 timeframes; long/short via price inversion; costs 14bps market / 8bps limit.
Protocol: dev 50% / val 25% / final 25%; final touched once, for pre-declared survivors.
Round 1 (10,800 configs): FVG-limit cluster looked good; found a simulator bias (the fill bar's high could count toward the target). Fixed (sim_fill).
Round 2 (9,000 configs, fixed): 15 pass train (chance expects ~12); 2 pass val; on FINAL one holds (+0.48R, n=38), one fails.
Candidate: 1H AMD, counter-drift gate, wide stops (>=0.8%), TP 2R (see BASE in stress.py/oos.py).
Hardening: fee sweep OK to 30bps; neighbourhood is a plateau; concentration OK; harsher fill assumptions weaken val.
Out-of-sample (33 coins x 900 days, coins/periods never used in search): 884 trades, avg +0.19R, t=+2.4 (winner's-curse shrink from +0.58R in-sample);
2024 losing, 2025 strong; the drift-gate effect did NOT replicate out of sample. Portfolio ($10, max 3 open, 2% risk): unseen $10 -> ~$29 (484 trades, maxDD -47%); harsher fills -> ~$9.
Verdict: a weak regime-dependent lead, not a 0.5R edge; needs forward (paper) validation.

## Grade system attempt (gradedata.py, grade.py, grade2.py, gradeeq.py)
Per-trade entry-time features for the AMD candidate (1,107 trades, 33 coins x 900d). Learned tercile-bucket grades on the first 60% of trades, tested on the last 40% (and reverse).
Result: dropping grade D did not help out of sample (lift -0.03R both directions; permutation-null p ~0.7-0.8). Nine a-priori loss hypotheses: none significant (need |t|>2.8), several with the opposite sign.
Loss anatomy: 54% of trades lose exactly -1R (median 12h to stop), winners average +1.72R -- losses are inherent to the 2R payoff design, not a detectable feature.

## Correction 2026-10-08 (found by ../loss_diagnosis)
disc.py's limit fill checked "close below the gap -> cancel" before "traded through the limit -> filled" on the same bar, a look-ahead
that dropped real fills (mostly losers). The AMD candidate's +0.19R out-of-sample came largely from it. Corrected, OKX 1H 2021-26,
all coins: AMD_PRIMARY +0.057R, AMD_NO_GATE +0.028R per trade, not significant. backend_lib/amd_fvg.py is fixed.

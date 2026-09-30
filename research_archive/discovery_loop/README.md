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

# Scalp research (1m / 5m), same protocol as the discovery loop
Data (OKX): 5m x 16 coins x 120d (8 search coins / 8 unseen coins), 1m x 8 coins x 30d. Costs: 14 bps market, 8 bps limit (also 4 bps optimistic-maker run).
Families: SPRING, CLIMAX, ABSORB, IMBAL (FVG limit retest), AMD, VACUUM, RETEST (break-and-retest) + wide-stop variants; dev 50% / val 25% / final 25%.
- 5m base: 4,900 configs, median avg R -0.13..-0.38 per family; 0 pass train t>=3. Cost wall: min-stop 0.15% -> median -0.32R, 0.40% -> -0.16R.
- 5m wide-stop: 3,500 configs, 0 pass train t>=3; 2 consistent (train+val) of 8,400 (chance ~38), both negative on FINAL.
- 1m: 2,800 configs, medians -0.15..-0.60R, none pass.
- Gross vs net (5m wide, limit families): IMBAL gross +0.067R median (78% positive) -> -0.034R at 8 bps. AMD +0.02 -> -0.07, RETEST 0 -> -0.13.
- 4 bps maker-optimistic: 19 consistent configs (chance ~7); 4/19 positive on FINAL, mean -0.047R.
- Top candidate ($10, 2% risk, max 3 open): 8 bps -> final third $9.52 (156 trades), unseen coins $1.54 (669 trades); 4 bps -> $11.08 / $4.66.
Verdict: no scalp edge survives costs and out-of-sample.

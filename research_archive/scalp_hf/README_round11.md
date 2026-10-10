# Round 11: wait for the liquidity sweep to finish, then enter

**Answer.** No pass. Waiting for the sweep to finish helps a little (the taker-flow confirmation roughly halves the losses), but no cell makes money after the 14 bps fee in both selection periods, so no judge set was run.

Rules: `PREREG_round11.md`, committed before any code ran. Code: `r11_sweep.py` (causal tests passed: trades that finished before a data cut are identical on cut data; a synthetic sweep gives one long with the right stop and a 2R target).

**What was tested.** On 15-minute bars: a bar breaks the previous 24-hour (or 72-hour) high or low by 0.1 ATR (the sweep), price closes back inside within 3 bars (the reclaim), then a taker-flow confirmation, then a market entry at the next open with the stop beyond the sweep extreme and a 2R or 4R target (up to 48 hours). 32 cells: lookback x confirmation (none / reclaim-bar taker flow turns / absorption on the sweep bar / both) x target x BTC-trend gate. Design coins, selection 2021-01..2024-06 (A) and 2024-07..2025-06 (B).

## Selection (`r11_select_out.txt`)
Net R per trade (A / B), best gate per row:

| Confirmation | Trades (A+B) | Net R (A / B) | Gross bps (A / B) |
|---|---|---|---|
| none (every sweep + reclaim) | 19,000 to 49,000 | −0.17 to −0.21 / −0.26 to −0.34 | −3 to +9 / −9 to −3 |
| reclaim bar's taker flow turns with the trade | 850 to 6,100 | −0.02 to −0.18 / −0.16 to −0.28 | −6 to +28 / −5 to +11 |
| absorption on the sweep bar | 500 to 2,300 | −0.12 to −0.36 / −0.28 to −0.38 | −20 to +14 / −18 to 0 |
| both | 89 to 445 | −0.12 to +0.34 / −0.56 to −0.04 | −3 to +69 / −31 to +34 |

- **No cell qualified** (n ≥ 300 and net R > 0 in both A and B). All 32 are negative in B. The best A+B t is +0.66 (89 trades), the next best −0.4.
- **Confirmation by taker flow helps, absorption does not.** The reclaim-bar flow filter cuts the loss from about −0.2R to −0.1R, and in the BTC-trend, 4R cells it lifts the gross edge to +10 to +28 bps, still below the 14 bps fee in B. The absorption signature on the sweep bar is worse than no filter (gross −6 to −20 bps): heavy one-sided selling into a low is more often the start of a continuation than the end of a move.
- **The both-filters cells** show +35 to +70 bps gross in A on 47 to 107 trades, then fall apart in B. This is what a small sample with two filters looks like, not an edge.
- The target distance does not change the picture: 4R is a little better than 2R, but the gross edge never clears the fee on enough trades.

## What this says about sweeps
Eleven rounds and every sweep variant (entry on the sweep, limit at the level, estimated liquidation cascades, previous-day/week levels, and now sweep + reclaim + confirmation) end in the same place: the reversal after a sweep is real but smaller than the 14 bps round trip on trades this frequent. Fewer, higher-quality sweeps (the confirmed ones) get closer, but at 1 to 3 trades a week per coin the sample is too small to show an edge. The only sweep-based idea that survived anything is AMD-FVG (sweep, displacement, then a fair-value-gap retest), at about +0.03 to +0.06R, and it runs as a forward test.

Files: `PREREG_round11.md`, `r11_sweep.py`, `r11_select_out.txt`, `r11_frozen.json`.

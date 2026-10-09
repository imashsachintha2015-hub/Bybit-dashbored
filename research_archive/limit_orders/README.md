# Limit orders: can maker execution turn the fee-killed setups into profits?
Execution model (lx_core.py), Bybit USDT perpetuals non-VIP: maker 2 bps per side; taker 5.5 bps + 1.5 bps slippage = 7 bps per side.
Market in + market out = 14 bps (every earlier test). Conservative fills: a limit entry fills only when price trades through it by
0.05 ATR, checked BEFORE any cancel on that bar; the fill bar can only stop out; a limit take-profit needs a 0.02 ATR trade-through;
stops and time-outs are taker. Parity: market mode reproduces resonance_test/res.py exactly (7,200 trades, 0 mismatches).

## Resonance rebuild (lx_res.py): 54 configs x 4 modes (market | limit at close | close - 0.25 ATR | close - 0.5 ATR, maker TPs)
| base | mode | fill rate | fee per trade | avg net R over 54 configs | positive configs |
|---|---|---|---|---|---|
| 15m | market | 100% | 0.123R | -0.123 | 0/54 |
| 15m | limit at close | 95% | 0.065R | -0.093 | 0/54 |
| 15m | limit -0.25 ATR | 81% | 0.071R | -0.113 | 0/54 |
| 15m | limit -0.5 ATR | 66% | 0.079R | -0.133 | 0/54 |
| 1H (2024-26) | market | 100% | 0.050R | -0.033 | 17/54 |
| 1H (2024-26) | limit at close | 96% | 0.026R | -0.044 | 15/54 |
| 1H (2024-26) | limit -0.25 ATR | 81% | 0.029R | -0.053 | 15/54 |
| 1H (2024-26) | limit -0.5 ATR | 65% | 0.033R | -0.088 | 13/54 |
DEV selection: 0 configs with t >= 2 (luck ~4.9 each). All 24 frozen configs fail FINAL + UNSEEN; on 2021-24 (d5) every frozen config is
negative. Example, 1H CHoCH ladder on 2021-24: market +0.016R before fees / -0.029R after ($10 -> $6.27 at 1%, 1,628 trades);
limit at close -0.017R before fees / -0.041R after ($10 -> $5.06, 1,624 trades).

## Previous-week-low sweep + reclaim (lx_pwl.py): the scenario family with the best gross edge in both periods
Event detection identical to scen2.py (2021-24: 2,774/2,774 events; 2024-26 adds SEI, which scen2 skipped for < 15,000 bars;
gross R identical on all 12,050 shared trades). Average over its 12 configs:
| mode | fill | 2024-26 before / after fees | 2021-24 before / after fees |
|---|---|---|---|
| market | 100% | +0.115 / -0.016R | +0.093 / -0.014R |
| limit retest of last week's low | 69% | -0.004 / -0.138R | -0.088 / -0.200R |
| limit at close - 0.25 ATR | 82% | +0.020 / -0.089R | +0.014 / -0.073R |
Frozen DEV-best per mode on 2021-24: market -0.086R ($10 -> $5.97 at 1%, 430 trades), retest -0.356R ($2.96, 327), pullback -0.129R ($4.32, 392).

## AMD-FVG (corrected fill), maker take-profit instead of taker: NO_GATE +0.028 -> +0.029R, PRIMARY +0.057 -> +0.058R per trade
(2021-26; $10 at 1%: $7.91 / 2,285 trades and $13.03 / 1,856 trades). The trade-through rule gives back most of the fee saving.

## Why limit orders do not help here: adverse selection
A resting buy limit below the market fills when price comes back down to it, which happens most often when the move is failing; the
trades that run away immediately (the winners) never fill. On every setup the gross edge lost this way was larger than the fee saved,
and the deeper the limit, the bigger the loss (PWL retest: -0.18R of gross edge on 2021-24 to save about 0.1R of fees). The edge these
setups have is in the immediate move after the signal, which only a market order captures, and a market order costs more than the edge.

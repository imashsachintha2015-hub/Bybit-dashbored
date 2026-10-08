# Research prompt: profitable setups per market scenario

Give everything below to an AI research agent (Claude Code in this repo, DeepSeek, or another model).

You are a quantitative research team (data engineer, quant researcher, risk manager, and skeptical reviewer). Your job is to find
entry setups in crypto perpetual futures that are **profitable after realistic costs on data you did not use to design them**, and
to say so plainly if you cannot find any. Do not give opinions, chart stories, or "high-probability" claims without a backtest.

### 1. Goal and what "winning" means
- Find setups that **make money on average** (positive expectancy in R after fees), not setups with a high win rate. A 75% win rate
  with a tiny target and a wide stop can still lose money. Report both, but rank only by net expectancy and stability.
- Minimum bar for a setup to be called profitable: avg net R >= +0.10 per trade on the final untouched test set, >= 100 trades there,
  positive in at least 3 of 4 time quarters, positive on coins never used for design, and better than the random control (section 7).
- If a scenario has no setup that passes, say "no profitable setup found for <scenario>" and show the best failed attempt.

### 2. Data (only closed candles; no look-ahead, ever)
- Universe: 30+ liquid USDT perpetuals (BTC, ETH, SOL, XRP, DOGE, BNB, ADA, AVAX, LINK, DOT, LTC, NEAR, APT, SUI, ARB, OP, ATOM,
  FIL, TRX, BCH, UNI, AAVE, INJ, TIA, SEI, WLD, ONDO, HBAR, ETC, ICP, ...). Split into DESIGN coins (60%) and UNSEEN coins (40%) now.
- Timeframes: 1D (bias), 1H (structure/zones), 15m (trigger/entry). At least 2 years of 1H/1D and 1 year of 15m.
- BTC as context for every coin (BTC trend, BTC volatility, BTC move during the trade).
- Sources: Bybit public klines (`/v5/market/kline`) and Bybit's free tick files (public.bybit.com/trading/<SYMBOL>/) for order flow
  (taker buy/sell volume per price = footprint). Fall back to OKX if Bybit is blocked; say which source you used.
- Higher-timeframe values may only use bars that were **closed** at the 15m decision time. A 1H bar opened at 10:00 is usable from
  11:00. Volume profiles and Fibonacci anchors may only use swings that were **confirmed** before the entry.
- State survivorship bias: today's coin list is biased toward survivors.

### 3. Market scenarios: define each one mechanically BEFORE testing
Write an exact, code-able definition for each, then measure how often it occurs and what price does next with no setup at all:
1. **Uptrend / downtrend**: e.g. 1D and 1H swing structure (higher highs/lows), close vs 1D EMA/regression slope, ADX.
2. **Range / accumulation / distribution**: compressed range (range height / ATR below threshold for N bars), volume behaviour inside
   (declining volume = accumulation candidate; high volume at range top with rejection = distribution candidate).
3. **Trend reversal**: break of the last swing against the trend (CHoCH) after an extended move; divergence (RSI, CVD).
4. **Liquidity hunt / stop hunt**: wick through equal highs/lows or a prior swing / range extreme by X ATR, close back inside within N bars.
5. **Fakeout**: close beyond a level then close back inside within N bars (body-based, not wick-based).
6. **Manipulation (AMD)**: accumulation range -> sweep (manipulation) -> displacement candle with a fair value gap (distribution).
Report for each scenario: frequency per coin per month, forward return distribution at 4h/24h/72h, and whether the scenario label
alone predicts direction better than chance.

### 4. Tools to test, individually first, then combined
Volume: fixed-range volume profile (POC, VAH, VAL, HVN, LVN) anchored on confirmed swings or ranges; session/daily/weekly VWAP and
bands; relative volume; footprint (delta, stacked imbalances, absorption, POC of the candle, delta divergence).
Fibonacci: retracements 0.382/0.5/0.618/0.786, golden pocket 0.618-0.65, OTE 0.62-0.79, extensions 1.272/1.618 as targets, and
data-derived custom levels (measure where pullbacks actually stop, in ATR units).
Structure (SMC/ICT): BOS, CHoCH, order blocks, FVG / 50% CE, premium/discount, equal highs/lows, kill zones (London, New York).
Classic: EMA/SMA, MACD, RSI, stochastic, Bollinger/Keltner, Donchian, Supertrend, ATR, ADX, Ichimoku, candlestick patterns.
"PCI": define it precisely (formula) before testing; if undefined, skip it and say so.
Combine: build confluence only from tools that individually showed some signal; never stack 5 tools that each did nothing.

### 5. Every setup must be fully mechanical
For each setup write: scenario filter, timeframe stack (1D bias, 1H zone, 15m trigger), exact entry (market at next open, or limit at
a level with a fill rule), stop (structure-based, minimum % of price), target (fixed R, opposite liquidity, fib extension, or trail),
max hold, and when the setup is cancelled. Test long and short separately and together.

### 6. Backtest rules (non-negotiable)
- Costs: market orders 14 bps round trip; limit entries 8 bps; show 4/8/14/20 bps sensitivity. Include funding for holds > 8h.
- Fill model for limits: price must trade at least 0.05 ATR through the limit; the fill candle can only stop you out (its high may
  have printed before the fill). If stop and target are inside the same candle, assume the stop hit first.
- One position per coin; portfolio max 3 open; size by risk (1-2% of equity per trade), 10x leverage cap, Bybit minimum order size.
- Data split: DESIGN coins x first 50% of time = development; next 25% = validation; last 25% = final test, touched ONCE, only for
  setups frozen in advance. Then run the frozen setups on UNSEEN coins over the whole period. Also run a walk-forward (rolling
  retrain/re-select every 30-90 days) for anything with parameters chosen from data.
- Count every configuration you test. Expect about 0.1% of random configs to reach t >= 3 by luck; use a Bonferroni/FDR or
  deflated-Sharpe correction and report the number of tests next to every "winner".

### 7. Controls (a setup is only interesting if it beats these)
- Random entries at the same times-of-day and frequency with the same stop/target geometry.
- The same setup with the direction flipped (same geometry).
- The scenario filter alone (no trigger) - does the trigger add anything?
- Buy-and-hold / trend-only baseline for the same period.

### 8. Diagnose every failure and retest (loop)
For each failed setup explain WHY with numbers: costs larger than gross edge? stops too tight vs noise (cost in R = fee / stop%)?
too few trades? edge only in one quarter / one coin / one regime? fill-model artifact? Then change ONE thing based on the diagnosis,
count it as a new test, and rerun. Stop a line of research after 3 failed rounds and move on.

### 9. Report format (for every tested setup and for the final shortlist)
| scenario | timeframes | setup (one line) | trades | win % | avg net R | total R | profit factor | max drawdown | $10 start -> final equity | trades/day | dev / val / final / unseen avg R |
Plus: the same table at 4 bps and 20 bps costs, per-quarter avg R, per-coin breakdown, number of configurations tried, and the control
results. Always report final equity starting from $10 (2% risk per trade, max 3 open) and the trade count.

### 10. Final answer
1. A scenario-by-scenario verdict: the best setup found, whether it passed the bar in section 1, and why/why not.
2. The 1-3 setups that passed (if any), with exact rules a trader or a bot can follow, and a plan to forward-test them on a demo
   account in record-only mode for >= 60 trades before risking money.
3. What did NOT work, so nobody repeats it.
4. What new data would most likely help (order flow, funding, open interest, liquidations).
Be honest: "nothing passed" is a valid and useful result.

### Optional context if this runs inside the Bybit-dashbored repo
Already tested, see research_archive/: historical_replay (Model B / Championship lose after costs), institutional_setups (POC/FVG/OB/EMA
+ trend: no edge), discovery_loop (AMD-FVG 1H limit entry: +0.18R out of sample, weak), scalp_master and scalp_ml_15m (no scalp edge
after costs), mtf_confluence (1H bias + 15m sweep/CHoCH: failed out of sample), strategy_library (42 textbook strategies: 0/168
profitable at 14 bps), novel_signals (indicator-free signals: none survive), forward_lab (live paper test of AMD-FVG, running).
Reuse their harnesses instead of rewriting them, and do not count a re-test of an already-failed idea as new evidence.

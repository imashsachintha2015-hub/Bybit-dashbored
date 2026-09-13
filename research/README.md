# Research: where the edge actually is

Four studies, run on the same real data as the trading system. Each one is
runnable and prints its own numbers.

```bash
node research/predictability.js     # what is predictable, and what isn't
node research/cost-scaling.js       # the arithmetic of many small trades
node research/passive-mm.js         # market making, modelled honestly
node research/funding-harvest.js    # getting paid without predicting
```

---

## The question these answer

> *"What if we just call and profit a small amount and do it 100,000 times? Or
> just trading fees? Why can't we build an accurate prophet? Why can't we
> identify chaos first?"*

Three separate claims. One is arithmetically backwards, one is pointing at a
real mechanism, and one is the single best instinct in the question.

---

## 1. "Why can't we identify chaos first?"

**You can. Chaos is the predictable part. Direction isn't.**

Measured over 12,098 fifteen-minute bars per symbol:

| | lag 1 | lag 2 | lag 5 | lag 20 |
|---|---|---|---|---|
| autocorrelation of **returns** (direction) | −0.009 | −0.009 | +0.010 | +0.003 |
| autocorrelation of **\|returns\|** (size) | **0.300** | **0.245** | **0.199** | **0.125** |

Direction is essentially independent from one bar to the next. Move *size* is
strongly dependent and decays slowly — volatility clustering, one of the most
robust findings in empirical finance. Regressing future realised volatility on
recent realised volatility gives **R² = 0.19–0.23**: roughly a fifth of future
volatility is explained by recent volatility alone.

Compare that to the direction-prediction ceiling on the same data:

| predictor | accuracy | avg captured |
|---|---|---|
| momentum (last bar) | 47.7–48.2% | −0.13 bps |
| **mean reversion (last 5)** | **52.2–53.6%** | **+0.14 to +0.26 bps** |
| always long | 49.2–49.9% | −0.02 bps |

The mean-reversion edge is *real* — 52–54% at n=12,098 is several standard
errors from chance, and the variance ratio confirms it (0.78–0.87 at 32 bars,
where 1.0 would be a random walk). It is simply worth about a quarter of a basis
point per trade.

So the prophet you want exists, but it forecasts the wrong quantity. It can tell
you *how violent* the next few hours will be with meaningful accuracy. It cannot
tell you which way.

---

## 2. "Do it 100,000 times"

The arithmetic is exact:

```
net = N × (edge_per_trade − cost_per_trade)
```

**Both terms scale with N.** Trade count is a multiplier on the per-trade
number, never a fix for it. Using the best measured edge (0.264 bps):

| fee structure | cost/round trip | net/trade | after 100,000 trades |
|---|---|---|---|
| retail taker | 11.0 bps | −10.74 bps | **−107% of stake** |
| retail maker | 4.0 bps | −3.74 bps | −37% |
| VIP-3 maker | 2.0 bps | −1.74 bps | −17% |
| MM rebate tier | −1.0 bps | +1.26 bps | +13% |

Repetition cannot change the sign of the per-trade number. If it is negative,
high frequency is the fastest known way to pay the exchange your entire account.
Only the rebate tier — which is gated on volume you do not have — turns positive,
and that is the mechanism, not the frequency, doing the work.

---

## 3. "Or just trading fees?" — market making

This half of the question is pointing at something real: as a taker you *pay*
the spread, as a maker you are *paid* it. The sign of the cost term genuinely
flips. So it was simulated properly.

A first-pass simulation produced +8.5 bps per fill at wide quotes, which was an
artefact of three things it did not model: it never closed the inventory, it
assumed touching a price meant filling at it, and it allowed unbounded one-sided
exposure. `passive-mm.js` models all three — queue penalty, forced unwinds at
the taker fee, inventory caps — and the result inverts:

| BTC, VIP-3 maker | 5 bps quote | 10 bps | 20 bps |
|---|---|---|---|
| net (bps, cumulative) | −12,945 | −2,433 | −1,074 |

Negative at every quote width, every fee tier below the rebate tier, and every
queue assumption tested (0 to 5 bps through). The killer is **adverse
selection**: a resting bid does not fill at random, it fills when someone wants
to sell, and often they want to sell because price is about to be lower.

Real market makers compete on inventory management, latency and fee tier — not
prediction. The gate is volume, not cleverness.

---

## 4. Getting paid without predicting — funding harvest

The one structure measured here whose payoff does not depend on being right
about direction. Long spot + short perp is delta neutral; price does whatever it
likes and the legs cancel. What does not cancel is the funding payment.

Measured over 33 days, 87 settlements:

| | BTC | ETH | SOL |
|---|---|---|---|
| mean funding per 8h | 0.0056% | 0.0056% | 0.0009% |
| settlements positive | 94% | 95% | 55% |
| gross annualised | 5.4% | 5.4% | 0.8% |
| **net of perp-leg fees** | **4.2%** | **4.2%** | −0.4% |

A counterintuitive result worth noting: the "smart" selective version — hold
only while funding is positive — **underperforms simply staying on**, because
each re-entry pays a round trip. On SOL it turns +2.4% gross into −17% net
across 16 re-entries. Cleverness costs money here.

Not modelled, and all of it reduces the number: spot-leg fees, entry basis,
liquidation risk on the short leg if it is levered, and exchange
counterparty risk, which in crypto is not a rounding error.

So: real, structural, non-directional — and about 4% a year. A yield, not a
windfall.

---

## 5. The finding that actually matters

Every losing result in this repository was dominated by the same term. Not the
signal. The **cost**.

The cost term is also the only quantity in the entire system under your direct
control. So it was tested: same signals, same bars, same risk sizing, same
position management — only the execution style changed.

| execution | trades | win % | expectancy | profit factor |
|---|---|---|---|---|
| **taker** (market in and out) | 66 | 42.4 | **−0.089R** | 0.78 |
| maker, limit at the touch | 66 | 45.5 | **+0.016R** | 1.04 |
| maker, limit 5 bps better | 63 | 49.2 | **+0.053R** | 1.14 |
| maker, limit 10 bps better | 59 | 47.5 | **+0.169R** | 1.44 |
| maker, limit 25 bps better | 44 | 47.7 | −0.075R | 0.83 |

**The strategy crosses from negative to positive on execution style alone.**
That is a swing of +0.10R to +0.26R per trade — larger than every signal
improvement achieved across the entire project combined, including the whole
analyst swarm.

The curve has a sensible interior shape rather than being monotonic: waiting for
a better price improves both the fee and the entry, until you start missing the
trades that never come back (22 of 66 missed at 25 bps). That is a real
trade-off, not a fitted parameter.

### Caveats, because this is the most important number here

- **n = 44–66.** The ranking is consistent across every setting, but the peak at
  10 bps is not distinguishable from the touch case at this sample size. The
  honest claim is "maker beats taker decisively", not "10 bps is optimal".
- **Fill modelling is optimistic.** A limit fills here if price trades through
  it. Real queue position means you may not fill at all. The 25 bps row shows
  what missed fills cost.
- **Stops stay market orders.** A stop that waits for a maker fill is not a stop.
  Only entries and take-profits become passive, which is why the improvement is
  roughly half the full maker/taker spread rather than all of it.

---

## The alien answer

An alien looking at this would not ask how to predict the number better. It
would notice that you are trying to forecast a quantity that thousands of
better-capitalised, lower-latency, lower-fee actors are also forecasting, where
the act of forecasting changes it — and then ask what *else* in the system is
predictable, uncompeted, or structurally paid.

The answers it would find, in descending order of how much they are worth to you:

1. **Your own cost basis.** Uncompeted, fully under your control, and worth more
   than every signal in this repository combined. Measured: +0.10 to +0.26R per
   trade.
2. **Volatility.** Genuinely forecastable (R² ≈ 0.2) while direction is not. Use
   it to size positions and to decide *when* to be active, not what to buy.
3. **Funding.** A structural payment for providing leverage to people who want
   it more than you do. ~4%/yr, delta-neutral, boring, real.
4. **Direction.** Real but worth ~0.25 bps, against a cheapest-possible round
   trip of 2 bps. This is the thing everyone spends their time on, and it is the
   only item on this list where the edge is smaller than the cost of acting on it.

The loophole was never a better prophet. It was noticing that the house takes a
cut on every prediction, and that the cut is bigger than the prophecy is worth.

---

# Part II — Attacking the data

The brief for this round was: stop re-examining the system, go after the data
itself, look for what *isn't* being looked at, and don't believe anything —
attack it.

```bash
node research/anomaly-scan.js --tf 15 --horizon 4   # 126 hypotheses, FDR-controlled
node research/forced-flow.js --tf 15                # liquidation-cascade reversion
node research/volatility-harvest.js --tf 15         # bet on magnitude, not direction
```

## The result, stated first

**Nothing survived.** 126 calendar and market-state hypotheses, a causally
motivated forced-flow hypothesis, and a strategy built specifically around the
one quantity Study 1 proved to be forecastable. Across all of it, zero findings
cleared the bar.

That is not a failed search. It is a measured answer to "is there something
obvious nobody is looking at", and the answer on this data is no. What follows
is how that conclusion was reached, because the method matters more than the
verdict.

## How the scan nearly fooled us — twice

**First trap: multiple testing.** Test 126 slices at the 5% level and about 6
will look significant when nothing is there. The scan therefore applies
Benjamini-Hochberg FDR control across all tests jointly.

**Second trap: overlapping windows, and this one did fool me.** The first run
of the scan reported **18 survivors**, several with effect sizes of 6–14 bps,
including a +14.29 bps anomaly at 14:00 UTC on ETH that also appeared on SOL —
cross-symbol confirmation, which is normally strong evidence.

It was an artefact of my own error. A 4-bar forward return, sampled at every
bar, means consecutive observations share 3 of their 4 forward bars. They are
heavily autocorrelated, the naive standard error is far too small, and every
t-statistic comes out inflated. Adding Newey-West HAC standard errors removed a
median inflation of **1.44×** — and took the survivor count from 18 to **zero**.

Every single one of those 18 "discoveries" was noise wearing a p-value.

## Third trap: not enough data, which the data itself revealed

The scan was first run on 126 days, then on 418 days. Watch what happened to the
families that looked most promising:

| family | median \|t\| on 126 days | median \|t\| on 418 days |
|---|---|---|
| runs | 1.42 | **0.26** |
| volstate | 1.95 | **1.36** |
| location | 1.05 | 0.65 |
| hour | 0.83 | 0.85 |

Under a true null, median |t| sits near 0.67. The `runs` family collapsed from
1.42 to 0.26 when given more data. That is precisely what noise does, and it is
the cleanest demonstration in this repository that the method is working.

## Forced flow — a good hypothesis that didn't hold

The strongest *causal* idea tested: a liquidation is not a decision. When a
leveraged position crosses maintenance margin the exchange closes it at market,
at any price. That order is completely price-insensitive — the only flow in the
market with a mechanical reason to overshoot. If it overshoots mechanically, it
should revert mechanically.

Detected from bars via the cascade signature (outsized move against recent
volatility, extreme volume, long rejection wick, close back from the extreme),
tested at three severity thresholds and four horizons:

- On 126 days of SOL, the severe filter gave **+13.85 bps at a 1-bar horizon**,
  consistent across both halves (+12.6 / +15.0). Promising.
- On 418 days of BTC, the same filter gives **−5.64 bps**. Opposite sign.
- The largest effect found (BTC, severe, 48 bars, +18.58 bps, t=1.94) splits
  +40.7 in the first half and −3.9 in the second.

Signs disagree across symbols and across halves. The causal story is sound; the
effect is not measurable at these thresholds on this data.

## Betting on magnitude instead of direction

The logical conclusion of Study 1: if direction is unpredictable and magnitude
is predictable, stop betting on direction. The structure that expresses that
without options is a two-sided breakout bracket — a buy stop above the coil and
a sell stop below, so whichever way the expansion goes, you are in it.

| BTC, 418 days | n | mean bps | t (HAC) | win % | 1st half | 2nd half |
|---|---|---|---|---|---|---|
| coil 8, all | 4,714 | −3.16 | **−2.04** | 49% | −2.0 | −4.3 |
| coil 16, all | 2,351 | +2.47 | 0.80 | 51% | +2.8 | +2.2 |
| coil 32, all | 1,189 | +7.28 | 1.21 | 52% | +2.7 | +11.8 |
| coil 32, quietest 25% | 301 | +5.18 | 0.81 | 58% | +7.4 | +2.8 |

The only result reaching |t| > 2 is **negative**: fast breakouts on short coils
lose reliably. There is a visible gradient — longer coils and tighter
compression give higher win rates (58%) and positive means — but no t-statistic
reaches significance, so it stays a gradient, not a finding.

## What "nothing survived" is worth

Three things.

**One: it cost almost nothing to find out.** Each of these is a few hundred
lines. The alternative — trading them and finding out — is how the V2 system
lost 49–70%.

**Two: the anomalies that got killed are exactly the ones people trade.**
Hour-of-day patterns, day-of-week effects, fading big moves, fading streaks,
buying compression breakouts, fading volume spikes. All of them produce
convincing-looking numbers on a few months of data. None survived 418 days plus
correct standard errors.

**Three: it sharpens where the edge actually was.** After attacking the data
this hard and finding nothing, the finding from Part I is still standing:

> Changing execution from taker to maker moved the existing strategy from
> −0.089R to +0.016R… +0.169R per trade.

That was never a prediction. It was the cost term — the one quantity not
competed over, because it isn't a forecast at all. Every hypothesis in Part II
was an attempt to out-predict a market; the one thing that worked was declining
to pay 11 bps for the privilege.

## What would be worth attacking next

Ranked by the chance there is something there that this data cannot see:

1. **Order book data.** Everything here is OHLCV plus hourly derivatives. Queue
   dynamics, depth imbalance and iceberg behaviour live in the book, are not
   reconstructable from bars, and are where the shortest-horizon edges are.
   This is the largest genuine blind spot.
2. **Cross-exchange.** One venue was measured. Lead-lag, basis and funding
   differentials between venues are mechanical, and unlike direction they do not
   require anyone to be wrong.
3. **Liquidation feeds.** Exchanges publish actual liquidation prints. Study 6
   inferred cascades from bar shape, which is a lossy proxy for an event that is
   directly observable.
4. **Longer history.** 418 days covers roughly one regime. A seasonality claim
   needs years, and the 126-vs-418-day comparison above shows exactly how much
   a short sample can lie.

And a rule earned the hard way: any future candidate goes through the same four
gates — HAC standard errors, FDR correction across everything tested, both
halves of the sample, and an effect larger than the cost of trading it. The 18
anomalies that failed those gates would all have looked like discoveries.

---

# Correction to Part I — the maker finding, re-measured on 418 days

Part I reported that switching execution from taker to maker moved the strategy
from **−0.089R to +0.016R … +0.169R** per trade, and called that "crossing from
losing to profitable". That was measured on 126 days and 44–66 trades.

Re-run on 418 days, with 244 trades:

| execution | n (126d) | expectancy (126d) | n (418d) | **expectancy (418d)** |
|---|---|---|---|---|
| taker | 66 | −0.089R | 244 | **−0.137R** |
| maker, at the touch | 66 | +0.016R | 244 | **−0.057R** |
| maker, 10 bps better | 59 | +0.169R | 222 | **+0.000R** |

Two things to take from this, and they point in opposite directions.

**The relative finding got stronger.** The gap between taker and maker execution
is **+0.137R per trade**, now measured across 244 trades rather than 66. Every
rung of the ladder improves monotonically, and the mechanism is arithmetic
rather than fitted. That part holds.

**The absolute claim did not.** "Crosses into profitable" was a small-sample
artefact. On 3.3× the data the best execution lands at **exactly break-even**,
not at +0.169R. The strategy is not profitable; it is merely no longer paying
for the privilege of trading.

This is the same lesson the anomaly scan taught, arriving from the other
direction, and it applies to my own headline result as readily as to anyone
else's: **an effect measured on a few dozen trades will shrink when you give it
more data.** The honest summary of the whole project is therefore:

- V2 was decisively losing (−0.335R over 1,051 trades) for identifiable,
  fixed structural reasons.
- V3 with taker execution is still losing, −0.137R over 244 trades.
- V3 with maker execution is break-even, ±0 over 222 trades.
- No anomaly search, analyst swarm, or signal improvement moved it past that.

Getting to break-even from −0.335R is real progress and it came almost entirely
from removing defects and costs rather than from adding prediction. Getting from
break-even to profitable is a different problem, and nothing measured here
solves it yet.

---

# Part III — Attacking everything

Four remaining blind spots, attacked. One produced the clearest result in the
whole project.

```bash
node research/fetch-extra.js daily BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP
node research/fetch-extra.js index BTC-USDT,ETH-USDT,SOL-USDT
node research/fetch-extra.js spot BTC,ETH,SOL,AVAX,LINK,DOGE
node research/seasonality-multiyear.js    # calendar, 5-7 years of daily bars
node research/basis-convergence.js        # perp vs its own index
node research/cross-venue.js              # OKX perp vs Coinbase spot
node research/collect-orderbook.js        # the blind spot that needs time, not code
```

## The headline

**A large, unambiguously real inefficiency was found. It is unreachable.**

Cross-venue dislocation between OKX perpetuals and Coinbase spot reverts with
t-statistics up to **38**, on thousands of observations, surviving every gate.
On DOGE the p99 dislocation is **11.31 bps** — comfortably above any plausible
cost.

Then the execution-lag test:

| symbol | reversion measured at the close | reversion available at the NEXT bar's open |
|---|---|---|
| BTC | +0.45 bps | −0.05 bps |
| ETH | +1.15 bps | +0.02 bps |
| SOL | +2.32 bps | +0.19 bps |
| AVAX | +9.44 bps | +0.04 bps |
| **DOGE** | **+11.31 bps** | **−0.60 bps** |

**The entire dislocation closes inside the one minute between seeing it and
being able to act on it.** Not most of it. All of it, and on DOGE slightly past
zero. This is what a competitive market looks like from the inside: not an
absence of inefficiency, but an inefficiency that exists only in the interval
you cannot reach.

## Attack 1 — Perpetual basis

The most mechanically forced quantity available. Funding is a literal
negative-feedback controller wired into the contract: when the perp trades above
its index, longs pay shorts, in proportion to the gap. This needs nobody to be
wrong.

It works, overwhelmingly. Fading an extreme basis as a spread (long one leg,
short the other) is significant at **t up to 44.8**, p-values past 1e-300, on
8,000 observations. 19 of 24 tests survive FDR.

And the effect is **0.5–2.3 bps** against a two-leg cost floor of 2 bps. All but
one land below it.

The directional version — does an extreme basis predict the perp's own return —
is **not significant anywhere**. The mechanical part works exactly as the
contract specifies, and contributes nothing beyond that.

## Attack 2 — Calendar effects over 5–7 years

The earlier scan tested hour-of-day and weekday on 418 days. This used 2,000+
daily bars per instrument, back to 2019.

The first run found ETH in July at **+74.7 bps/day, t=3.20**, consistent across
both halves. It survived FDR. It looked like a genuine seasonal.

It was wrong, for two reasons I had to fix in my own test:

1. **It was measured against zero, not against the drift.** Crypto rose over the
   period (+19 bps/day for ETH). Any subset of a rising series has a positive
   mean. The question worth asking is whether July differs from an *ordinary
   day*, so the series must be demeaned first.
2. **The independent unit for a monthly effect is a year, not a day.** Reporting
   n=217 for "July" is misleading — those are 7 Julys, and days inside one July
   share a regime almost entirely. The honest sample size is 7.

With both corrected: **nothing survives**, and monthly effects become untestable
at all — 5–7 independent observations cannot support the claim. Family median
|t| collapses to 0.64 / 0.51 / 0.28 against a null expectation of 0.67. Textbook
null behaviour.

## Attack 3 — Cross-venue

Detailed above. Two further points worth keeping.

**The dislocation is the size of the cost of closing it.** Measured against live
top-of-book spreads:

| symbol | spot spread | perp spread | combined | p99 dislocation |
|---|---|---|---|---|
| BTC | 0.00 | 0.01 | 0.01 | 0.45 |
| ETH | 0.04 | 0.04 | 0.08 | 1.15 |
| SOL | 0.98 | 0.98 | 1.96 | 2.32 |
| AVAX | 2.68 | 1.34 | 4.02 | 9.44 |
| DOGE | 3.56 | 1.19 | 4.75 | 11.31 |

Across a 25× range of liquidity, the gap tracks the cost of removing it. That is
arbitrageurs competing until what remains is exactly their own friction.

**I had to kill an artefact first.** A thin venue's 1-minute bar may contain no
trades, making its close stale. Comparing a live price against a stale one
manufactures a gap that "reverts" the moment the thin venue prints — and
staleness rises as liquidity falls, which would generate this exact gradient
fraudulently. Filtering every bar with zero volume or an unchanged close (7–9%
of bars on the thin names) left the effect **slightly stronger**. The artefact
explanation is dead; the execution-lag one is what killed it.

**Lead-lag is zero.** Cross-venue return correlations at 1–3 minute lags are
0.01–0.04 in both directions, against a contemporaneous correlation of 0.99.
Neither venue leads the other at any resolution this data can see.

## Attack 4 — Order book and liquidations

This one could not be attacked, and the reason is worth stating precisely rather
than dressed up.

- **Order books are served live only.** No exchange offers historical depth via
  public API. Nothing about queue position, depth imbalance, or genuine
  spoofing is reconstructable from bars.
- **OKX's liquidation feed returns ~100 events**, roughly three hours, and
  backward pagination does not work. The fetcher was written and run; it
  retrieved 100 prints spanning 0.13 days per symbol. That cannot support an
  event study.

So `collect-orderbook.js` records both going forward — top-of-book depth,
imbalance at 5 and 25 levels, and liquidation prints — to newline-delimited
JSON. It needs to run for weeks before the questions become answerable. That is
a waiting problem, not a coding one, and this is the largest genuinely
unexplored area left.

## What all of this adds up to

Every real effect found across three rounds of attacking has the same shape:

| effect | statistically real? | large enough to trade? | reachable? |
|---|---|---|---|
| mean reversion (15m) | yes, ~5 SE | no, 0.26 bps vs 2 bps floor | yes |
| basis convergence | yes, t up to 44.8 | mostly no, 0.5–2.3 bps | yes |
| cross-venue dislocation | yes, t up to 38 | **yes, up to 11.3 bps** | **no — closes in <1 min** |
| calendar effects | no | — | — |
| forced-flow reversion | no | — | — |
| volatility breakout | no | — | — |

Nothing here is hidden. Everything is measurable with public data and a few
hundred lines. And each one is sealed — by cost, by speed, or by not existing.
That is not a discouraging finding, it is a specific one: it says the remaining
levers are **cost, speed, and access**, not insight. Which is where Part I
landed from the opposite direction, and why the execution change remains the
only thing in this repository that moved the number.

---

# Part IV — "We always land at 41-47%. What if we do the opposite?"

Three studies, answering the question directly.

```bash
node research/winrate-frontier.js --tf 15 --cost 15    # is win rate a free parameter?
node research/indicator-grid.js  --tf 15 --cost 15     # 2,448 configurations
```

## The 41-47% is not a signal problem. It is 50% minus the fee.

The grid ran **2,448 strategy configurations** — 10 custom indicators × 2-3
parameters each × 3 thresholds × momentum *and* reversion × 6 holding periods ×
6 symbols. Here is the single most important table it produced:

| holding period | configs | **gross win %** | **net win %** | mean bps |
|---|---|---|---|---|
| 15 min | 408 | **49.7%** | **25.0%** | −15.00 |
| 30 min | 408 | **49.8%** | 30.6% | −15.00 |
| 60 min | 408 | **49.8%** | 35.3% | −15.00 |
| 4 hours | 408 | **49.9%** | 41.7% | −15.00 |
| 12 hours | 408 | **50.0%** | 45.6% | −15.00 |
| 24 hours | 408 | **50.0%** | 47.2% | −15.00 |

**Gross win rate is 49.7-50.0% at every horizon.** Flat. Across every indicator,
every parameter, every threshold, both directions. The indicators have no
directional edge at any holding period — before costs, all of it is a coin flip.

**Net win rate climbs from 25% to 47%** as the hold lengthens. That entire climb
is cost dilution: the fee is fixed per trade while the expected move grows with
time, so a shorter hold has a larger share of its trades converted from winner
to loser by the fee. At 15 minutes, the fee flips a quarter of all trades.

So the 41-47% that keeps appearing is not the strategy failing to predict. It is
**50% minus what the exchange takes.** That is why it appears no matter what
indicator is used.

## Can we get 53-59%? Yes, trivially, and it does not help

`winrate-frontier.js` takes **random coin-flip entries** on BTC and changes only
the target/stop ratio:

| target : stop | win rate | gross expectancy | net expectancy |
|---|---|---|---|
| 0.25 : 1 | **80.0%** | +0.002 | −0.229 |
| 0.5 : 1 | **66.5%** | +0.002 | −0.229 |
| 1 : 1 | 50.2% | +0.005 | −0.225 |
| 2 : 1 | 37.2% | +0.018 | −0.212 |
| 3 : 1 | 33.1% | +0.017 | −0.214 |

An 80% win rate, on **no information whatsoever**, by putting the target at a
quarter of the stop distance. Gross expectancy stays pinned at zero across the
entire sweep, because moving the target trades win *rate* against win *size* at
a fixed exchange rate. There is nothing to manufacture expectancy from.

Across the 2,448-configuration grid, the **correlation between win rate and
expectancy is 0.036**. Effectively zero. The top 12 configurations by win rate
and the top 12 by expectancy share almost no members.

**Win rate is a dial. Expectancy is the score.**

## Does inverting a losing strategy make it a winning one?

No, and the reason is arithmetic: **Net = Gross − Cost.** Inverting flips the
sign of Gross. It does not flip Cost, because you pay the spread and the fee in
both directions.

Every rule run forwards and inverted on identical bars:

| rule | win% | exp R | inverted win% | inverted exp R | gross R |
|---|---|---|---|---|---|
| momentum, last bar | 36.8% | −0.162 | 37.3% | −0.154 | +0.005 |
| momentum, 5-bar | 37.6% | −0.142 | 36.5% | −0.175 | +0.025 |
| reversion, last bar | 37.3% | −0.154 | 36.8% | −0.162 | +0.013 |
| above/below 20-EMA | 37.0% | −0.156 | 37.1% | −0.161 | +0.012 |
| 20-bar breakout | 37.6% | −0.131 | 36.0% | −0.187 | +0.047 |

Both columns lose. The "gross R" column — the same rule with costs switched off
— is where the rules actually sit: between +0.005 and +0.047, i.e. nothing.
There is no negative edge to invert into a positive one. There is a zero edge
and a fee.

Inverting a strategy that loses to *costs* gives you a strategy that also loses
to costs. You do not get −(−0.137R). You get −(Gross) − Cost.

## After correcting for the size of the search

2,181 of the 2,448 configurations passed FDR correction — because they are
reliably, significantly **negative**. Of everything that passed:

> **0 are positive and hold their sign across both halves of the sample.**

Not one, out of 2,448 combinations of indicator, parameter, threshold,
direction, holding period and symbol.

## What this actually leaves

The three studies agree on the same mechanism from three directions:

1. Gross edge is ~0 at every horizon tested, for every indicator tested.
2. Win rate is set by the target/stop ratio, not by skill, and is uncorrelated
   with profitability (r = 0.036).
3. The observed 41-47% is 50% minus the fee, which is why it is so stable.

Which means there are exactly two levers that demonstrably move the number, and
neither is a signal:

- **Hold longer.** Not because prediction improves — gross win rate is 50.0% at
  24 hours just as it is at 15 minutes — but because the fixed fee becomes a
  smaller share of a larger move. Net win rate goes 25% → 47% on that alone.
- **Pay less.** The maker/taker result from Part I, worth +0.137R per trade.

Both shrink the cost term. Nothing found in three rounds of searching grows the
gross term.

---

# Part V — Patience at the entry

A fair challenge: every study up to here, including the 2,448-configuration
grid, entered **at market on the signal bar**. The holding period varied; the
entry never did. That is one narrow scenario tested many times.

```bash
node research/patient-entry.js --tf 15 --hold 48 --wait 8
```

Waiting before entering does two separate things, and they had to be measured
apart because only one of them would be new:

1. **It lowers cost.** A resting limit pays maker instead of taker and does not
   cross the spread. Already known, worth about +0.137R per trade.
2. **It selects which trades you get.** A limit below the market only fills if
   price comes back — so you systematically miss the trades that ran away
   immediately. That is a *filter on the population of trades*, and unlike a
   fee, a filter can change **gross** expectancy.

So gross is reported next to net everywhere. That comparison is the study.

## The answer

| entry method | mean **GROSS** bps | vs instant | mean **NET** bps | vs instant | fill rate |
|---|---|---|---|---|---|
| INSTANT (market) | −0.70 | — | −15.70 | — | 100% |
| delay 1 bar | −0.40 | +0.30 | −15.40 | +0.30 | 100% |
| delay 4 bars | −0.58 | +0.12 | −15.58 | +0.12 | 100% |
| limit 5 bps better | −1.19 | −0.49 | −3.19 | **+12.51** | 93% |
| limit 15 bps better | −0.57 | **+0.12** | −2.57 | **+13.12** | 81% |
| limit 40 bps better | −1.57 | −0.87 | −3.57 | **+12.13** | 54% |
| pullback 25% of move | −3.35 | −2.66 | −5.35 | **+10.34** | 67% |
| pullback 50% of move | −1.30 | −0.61 | −3.30 | **+12.39** | 41% |

**Gross does not move.** The column wobbles between −2.66 and +0.30 — noise
around zero, with no relationship to how patient the entry is. Waiting for a
better price does **not** select better trades. The ones you miss were not
systematically the good ones.

**Net improves by +10 to +13 bps** — and the taker/maker difference is 13 bps.
Patience is worth exactly the fee saving, to the basis point, and nothing more.

Even the most extreme filter tested — waiting for a 50% retracement, which
discards 59% of all signals — leaves gross unchanged. Throwing away most of the
trades does not improve the ones you keep.

## What it does do for win rate

| | win rate |
|---|---|
| instant, taker | 43.1% |
| limit 15 bps better, maker | 47.8% |
| limit 40 bps better, maker | **48.4%** |

Patient entry raises the win rate ~5 points, and on the mean-reverting signals it
reaches **51.9-52.1%**. That is the honest route to a higher win rate — but note
what produced it: not better prediction, just fewer trades being flipped from
winner to loser by the fee. Same mechanism as holding longer, from Part IV.

## After correction

28 signal × entry-method combinations, FDR at q=0.10: 12 pass, and

> **0 are positive and stable across both halves.**

Consistent with everything before it.

## Where this leaves the entry question

Patient limit entry is **worth doing** — it is the single largest improvement
available, it moves net expectancy by +13 bps, and it takes a losing
configuration to approximately break-even (net −0.34 to +0.68 bps on the
reverting signals, versus −13 to −20 instant).

But it is worth doing for the boring reason. It is a rebate, not an insight.
Gross expectancy was zero before the wait and is zero after it, which is now the
fourth independent route to the same conclusion:

- holding longer: gross flat at 50.0% win, net improves (Part IV)
- inverting: gross flips a number that is already zero (Part IV)
- patient entry: gross flat, net improves by exactly the fee (Part V)
- maker vs taker execution: +0.137R per trade (Part I)

Every lever that has ever moved the number in this repository moved the **cost**
term. Nothing has moved the **gross** term, across 2,448 indicator
configurations, 126 market-state slices, 69 calendar hypotheses, four
cross-venue and basis structures, and now 28 entry methods.

---

# Part VI — "Build advanced indicators, not MA and RSI"

The challenge: it's a binary outcome — up or down in 15/30/60 minutes. It's the
result of a million causes. Can we identify those causes, build genuinely
advanced indicators, and find the moments where the answer is knowable? And five
well-calculated trades would be enough — I don't need a hundred.

**The first half of that was right, and it exposed a real flaw in my own work.**

## The flaw: 2,448 tests of ~2 things

Study 11 measured the information content of the indicator set the grid used.
Eight "different" indicators, correlation matrix:

| | roc14 | rsi14 | boll20 | donch20 | emaX12 | eff30 |
|---|---|---|---|---|---|---|
| roc_14 | 1.00 | **0.99** | 0.80 | 0.80 | 0.60 | 0.63 |
| bollinger_20 | 0.80 | 0.82 | 1.00 | **0.94** | 0.50 | 0.59 |
| emaCross_12 | 0.60 | 0.61 | 0.50 | 0.54 | 1.00 | **0.91** |

RSI and rate-of-change correlate **0.99**. Bollinger and Donchian **0.94**. EMA
cross and efficiency ratio **0.91**.

**Effective rank: 2.42 out of 8.**

So the 2,448-configuration grid was not 2,448 independent attempts at
prediction. It was roughly 2.4 dimensions of information, tested 2,448 ways.
That is much weaker evidence than it appeared, and the criticism lands.

**Adding a cleverer price formula cannot help.** No transform of a series adds
information the series does not contain. That is not a market fact, it is an
arithmetic one.

## So new information was added

Open interest, funding, crowd long/short positioning, measured taker flow, and
cross-venue basis — five sources that are not functions of the price series.

| feature set | effective rank |
|---|---|
| 8 price indicators | 2.47 |
| + 5 non-price sources | **5.19** |

And each new source is genuinely orthogonal to the price block:

| non-price feature | max \|corr\| with ANY price indicator |
|---|---|
| crowd long/short ratio | 0.064 |
| cross-venue basis | 0.087 |
| funding z-score | 0.125 |
| open-interest change | 0.240 |
| taker imbalance | 0.299 |

The information content more than doubled. This is exactly the "advanced
indicators" the question asked for — not new arithmetic, new measurements.

## Then it was tested, walk-forward

Ridge-regularised, strictly walk-forward, standardised on training statistics
only, refit as the window rolls. 1,518 out-of-sample forecasts per symbol.

| symbol | feature set | accuracy | **IC** | net bps |
|---|---|---|---|---|
| BTC | price only (2.4 dims) | 49.9% | −0.044 | −3.97 |
| BTC | **price + derivatives (5.2 dims)** | 49.7% | −0.052 | −4.19 |
| ETH | price only | 50.1% | −0.052 | −3.70 |
| ETH | **price + derivatives** | 49.1% | −0.033 | −4.44 |
| SOL | price only | 50.1% | +0.026 | −2.52 |
| SOL | **price + derivatives** | 48.9% | +0.030 | −4.22 |

Doubling the information content changed nothing. IC — the correlation between
forecast and outcome — sits between −0.052 and +0.030 across every combination.
A good systematic equity signal runs an IC of 0.02–0.05; this is indistinguishable
from zero in both directions.

## And the "five well-calculated trades" test

This deserved its own test, because a model can be useless on average and still
be right where it is most confident. Restricting to the strongest forecasts:

| confidence slice | BTC | ETH | SOL |
|---|---|---|---|
| all 1,518 forecasts | 49.7% | 49.1% | 48.9% |
| strongest 10% (n=151) | **34.4%** | 47.0% | 56.3% |
| strongest 2% (n=30) | **26.7%** | 46.7% | **40.0%** |

**Accuracy gets worse as confidence rises.** On BTC the model's most confident
forecasts are right 26.7% of the time — it is reliably, strongly wrong exactly
where it is most sure.

That is the specific answer to "five well-calculated trades is enough". There is
no high-conviction subset to select. The model has no region where it knows
more; its confidence is anti-correlated with its accuracy. Five trades chosen
this way would be five trades chosen from its worst-performing region.

(The SOL 56.3% at n=151 is the sort of lone positive cell this whole project has
learned to distrust — it collapses to 40.0% at the next slice down.)

## What is honestly still open

This is a negative result on **thin data**, and that limit should be stated
plainly rather than buried:

- **The derivatives history is 30 days.** OKX retains only 720 hourly rows, which
  yields ~2,758 aligned observations and 1,518 out-of-sample forecasts. That is
  enough to rule out a large effect, not a small one. Years of open-interest and
  funding history would make this a real test rather than an indication.
- **The five sources are the ones that happen to be free.** Order book depth,
  liquidation prints, options positioning, on-chain flows and stablecoin
  issuance are all genuinely orthogonal information that was not available here.
  The collector in `collect-orderbook.js` exists precisely because that is the
  largest reachable gap.
- **A linear model was used.** A non-linear one could find interactions a ridge
  regression cannot. With 2,758 rows and 13 features that would mostly fit
  noise, so it was not attempted — but with years of data it would be the
  obvious next step.

So the correct summary is not "this is impossible". It is: **the information
that was reachable here, combined properly and tested honestly, does not
forecast 15-to-60-minute direction — and the model's own confidence is not a
guide to when it does.**

The premise of the question was right. Better formulas on the same data cannot
work, and new information is the only path. That path was taken as far as the
available data allows, and it stopped here.

---

# Part VII — Equations from outside technical analysis

The challenge here was specific: stop rearranging price into new indicator
shapes, and instead bring in mathematics from a field that isn't technical
analysis — the kind a market microstructure researcher or an information
theorist would reach for.

```bash
node research/novel-equations.js --tf 15 --cost 4
```

Three were implemented, each a real, named equation from a different
discipline, none a variant of anything in Part VI:

## 1. Kyle's Lambda (market microstructure, Kyle 1985)

The canonical price-impact equation: regress price change on signed order flow
over a trailing window. The slope, λ, is literally "how much does it cost in
price to push this much size" — the founding equation of market microstructure
theory, built from the same real taker-flow data Part VI used, not from price
alone.

Tested both directions — does an impact spike predict reversion (an
informationally-driven overshoot) or continuation (informed flow that keeps
being right) — at 2 horizons on 3 symbols, 728/609/756 extreme events:

Every configuration failed. One reaches conventional significance before
correction (BTC, follow, 4 bars, t=−2.61) — and it's negative and unstable
(+1.1 second half vs −7.8 first). Nothing survives FDR.

## 2. Jump / continuous decomposition (Barndorff-Nielsen & Shephard, 2004)

Realised variance mixes two different underlying stochastic processes —
continuous diffusion and discontinuous jumps. Bipower variation (the product of
adjacent absolute returns, correctly rescaled) estimates the continuous part
alone; realised variance minus that isolates the jump component. This is
mathematically distinct from "big candle" detection: it's asking which
*process* generated the move, not how large it was.

First pass was starved — only 30 days of derivatives-aligned data gave BTC 92
events and ETH/SOL too few to test at all. The equation itself needs only
price, so it got a fair rerun on the full 418-day series: **1,742 events** at
the loosest threshold.

| symbol | thresh | mode | hold | n | mean bps | t(HAC) | 1st half | 2nd half |
|---|---|---|---|---|---|---|---|---|
| ETH | 0.6 | fade | 48b | 380 | **−22.18** | **−2.98** | −14.5 | −25.3 |
| BTC | 0.4 | fade | 16b | 1560 | −8.09 | −3.32 | −7.7 | −8.2 |
| ETH | 0.5 | fade | 16b | 757 | −11.77 | −3.29 | −14.0 | −10.4 |

FDR across 54 configurations: **13 survive — all negative.** Fading a jump
loses reliably; following one doesn't reliably win either (t stays under 2
everywhere in that direction). **Zero positive and stable across both halves.**
The equation is real and now well-powered; the market doesn't reward trading it
in either direction after cost.

## 3. Transfer entropy (information theory, Schreiber 2000)

Correlation only sees linear dependence. Transfer entropy measures how much
uncertainty about price's future is resolved by another series' past, beyond
what price's own past already resolves — including non-linear, non-monotonic
dependence that Part VI's correlation-based effective-rank calculation is
structurally blind to. Significance via a 200-fold permutation test (the
correct null for this statistic, no normality assumption).

| symbol | source | TE observed | null mean | z | perm p |
|---|---|---|---|---|---|
| BTC | signed order flow | 0.0009 | 0.0020 | −1.08 | 0.900 |
| BTC | open interest | 0.0007 | 0.0019 | −1.40 | 0.955 |
| ETH | signed order flow | 0.0013 | 0.0020 | −0.78 | 0.766 |
| SOL | open interest | 0.0024 | 0.0020 | 0.35 | 0.294 |

Every observed value sits inside its own permutation null — several are
*below* the mean of 200 random shuffles. No detectable directional information
flow from either source into price direction, on this data, at this sample
size. (The self-referential control also showed nothing above null, consistent
with Part I's near-zero return autocorrelation — there was no strong known
signal available here to calibrate against, which is itself informative about
how little structure exists in this series at 15-minute resolution.)

## What this round adds to the picture

None of the three is a retail indicator. All three are legitimate, named
results from finance and information theory, computed correctly, tested with
the same four gates as everything else, and none found anything that survives.

That is now five structurally different attempts at the same question — price
transforms (Part VI), microstructure impact, jump/diffusion decomposition,
information-theoretic transfer, and (Part IV/V) the entry/exit/holding-period
grid — agreeing from five different angles. It doesn't prove no equation could
ever work. It does mean the ones a working researcher would reach for first
have now been tried, correctly, and came back empty on the data actually
available.

The honest limits stand exactly as in Part VI: Kyle's lambda and transfer
entropy are tied to the 30-day derivatives window and would deserve a real
retest on years of open-interest/funding history; the jump/diffusion result is
now well-powered and is a clean negative, not an inconclusive one.

---

# Part VIII — Three new constructs, exactly 30 minutes, four coins together

Two direct requests: commit to a fixed 30-minute forecast horizon instead of
instant exits, and use four coins' data together rather than one at a time,
testing constructs invented for this rather than reused from anywhere else.

```bash
node research/fetch-klines.js DOGEUSDT 15 40000     # 4th coin, same depth as BTC/ETH/SOL
node research/creative-equations.js --cost 4
```

On the 30-minute question directly: Part VI already fit a combined
price+derivatives model at exactly this horizon (2 bars) and found IC between
−0.052 and +0.030 — indistinguishable from zero. This round does not repeat
that model; it tests three constructs that were never tried in any form.

## 1. Market Gravity

Retail volume-profile tools weight nearby volume nodes roughly linearly. This
applies Newton's inverse-square law instead — literally F = mass / distance² —
treating each historical volume node as a mass and current price as a test
particle, summing the signed pull from every node within range. The
inverse-square kernel is a specific, physically-motivated choice nothing in
Part VI or any indicator library makes.

| symbol | lookback | n | mean bps | t(HAC) | 1st half | 2nd half |
|---|---|---|---|---|---|---|
| BTC | 40 | 8,012 | −3.80 | **−11.04** | −3.9 | −3.7 |
| ETH | 40 | 8,011 | −4.45 | **−8.81** | −5.4 | −3.6 |
| SOL | 96 | 8,000 | −4.97 | **−9.09** | −5.1 | −4.9 |
| DOGE | 40 | 8,011 | −3.51 | **−6.08** | −3.8 | −3.2 |

Overwhelmingly significant, negative, stable across both halves, and stable
across four unrelated coins. Betting price moves toward the strongest
inverse-square pull loses reliably.

## 2. Permutation Entropy regime filter

A genuine result from nonlinear dynamics (Bandt & Pompe, 2002), not finance:
measures how *ordered* the sequence of recent moves is via the diversity of
their ordinal patterns, independent of size or direction. This operationalises
the "five well-calculated trades" instinct precisely, as a testable selection
filter rather than a feeling: does a simple reversion signal work specifically
in the rare windows where price is behaving unusually non-randomly, even though
Part IV/VI showed it does not work on average?

| symbol | entropy band | n | mean bps | t(HAC) |
|---|---|---|---|---|
| BTC | lowest 20% (ordered) | 8,025 | **−4.39** | −10.52 |
| BTC | all bars (baseline) | 40,033 | −3.84 | −20.73 |
| ETH | lowest 20% (ordered) | 8,166 | **−5.49** | −9.03 |
| ETH | all bars (baseline) | 40,018 | −4.31 | −16.06 |

The ordered-regime filter does not beat the unfiltered baseline anywhere — on
BTC and ETH it is slightly *worse*. Restricting to the moments where the path
looks most non-random does not surface a better subset of trades. The
selectivity instinct was reasonable; this is a real test of it, and it says no.

## 3. Cross-Asset Dispersion Rank

The one construct here that structurally cannot be computed from a single
coin's data: at every bar, each coin's trailing return is z-scored against the
*other three* coins at that same instant, and the test is whether being the
basket's extreme outlier — laggard or leader relative to normally-correlated
peers — predicts snapping back toward the group.

(First pass had a bug worth naming: the z-score included the coin's own value
in the sample it was scored against, which mathematically bounds the maximum
achievable z-score with only 4 points and starved the test of the exact
extreme events it needed. Fixed to score strictly against the other three.)

| symbol | extreme | n | mean bps | t(HAC) |
|---|---|---|---|---|
| BTC | laggard (z<−1.5) | 1,544 | −5.00 | −5.28 |
| ETH | leader (z>1.5) | 4,011 | −4.89 | −5.61 |
| DOGE | laggard (z<−1.5) | 9,838 | −4.76 | −5.86 |

Same pattern. An outlier coin does not reliably snap back toward its peers at
this horizon.

## The result

**28 of 28 tests survive FDR correction. Zero are positive and stable across
both halves.** Every one of the three constructs, on every one of the four
coins, at exactly the 30-minute horizon asked for.

The one thing worth pointing out about this round specifically: the negative
means cluster remarkably tightly, −2 to −5 bps, across three unrelated
constructs and four unrelated assets. Pure noise would not do that — it would
scatter both in sign and in magnitude. This tight, one-sided clustering is
itself the signature of a real, near-zero gross edge being consistently
overwhelmed by the same few basis points of cost, which is the exact finding
of every part of this research from Part I onward, arrived at again from three
constructs invented specifically to be unlike anything already tried.

## Where the honest boundary is now

Eight structurally different approaches agree: price transforms (VI),
microstructure impact (VII), jump/diffusion decomposition (VII), information-
theoretic transfer (VII), the full entry/exit/hold grid (IV/V), and now
inverse-square volume gravity, ordinal-pattern entropy, and cross-sectional
dispersion (VIII). None of this proves no equation could work. It means the
mathematics genuinely worth trying — reused from finance, physics, and
information theory, and three built fresh for this — has been tried, correctly,
on the data actually available, at the exact horizon asked for, using all four
coins together where that was the point, and none of it clears its own cost.

What remains untried is not a smarter equation on this data. It is data this
project has not yet been able to reach: the order-book collector
(`collect-orderbook.js`) needs weeks of runtime it has not had, and every
derivatives-based construct here is still bounded by OKX's 30-day retention.
Those are the two honest places left to look.

# Part IX — What is the best this project can get, combined?

Every prior part tested *whether* a signal exists. This part asks a different
question: given the one lever that has ever moved net expectancy (execution
cost — Part V) and the one structural layer that ever showed a positive,
if noisy, number (the seven-analyst swarm's advisory gating — Part III), what
does the V3 engine actually produce if both are pushed together, end to end,
on the full 418-day / four-symbol backtest?

This grid was run twice. The first pass was reported to the user verbally,
from memory, as "roughly breakeven to +0.05R" for the disabled-swarm case and
"an erratic +0.44R at n=50" for advisory mode. That recollection undersold the
disabled-swarm case and used a stale number for advisory mode. The table below
is the actual second run, saved to JSON so it can be checked rather than
recalled — `swarmMode off|advisory`, `--exec maker`, offset swept 0-30bps,
`--makerTimeout 20`, full 418-day backtest, `--seed 1` (meta-learner seeded
from Part III's study):

| swarmMode | offset | n | win rate | expectancy (R) | total R | profit factor |
|---|---|---|---|---|---|---|
| off | 0bps | 320 | 49.1% | −0.009 | −2.99 | 0.98 |
| off | 5bps | 315 | 47.6% | +0.015 | +4.82 | 1.04 |
| off | 10bps | 310 | 47.1% | +0.065 | +20.04 | 1.16 |
| off | 15bps | 302 | 47.4% | +0.098 | +29.70 | 1.24 |
| off | 20bps | 300 | 47.7% | +0.155 | +46.48 | 1.38 |
| off | 30bps | 287 | 48.1% | **+0.288** | +82.72 | 1.70 |
| advisory | 0bps | 94 | 57.4% | −0.040 | −3.73 | 0.90 |
| advisory | 5bps | 90 | 57.8% | +0.045 | +4.07 | 1.11 |
| advisory | 10bps | 87 | 56.3% | +0.055 | +4.78 | 1.12 |
| advisory | 15bps | 84 | 57.1% | +0.077 | +6.46 | 1.16 |
| advisory | 20bps | 83 | 55.4% | +0.215 | +17.84 | 1.45 |
| advisory | 30bps | 76 | 56.6% | **+0.791** | +60.08 | 2.79 |

For reference, the original V2 baseline on the identical data is a constant
n=1,638, win rate 42.6%, expectancy **−0.174R**, total −284R — every row above
already beats it by a wide margin.

**Two different things are happening in this table, and they should not be
read as the same finding:**

1. **The `off`-mode column is the trustworthy one.** Win rate and n move
   smoothly and monotonically as offset rises — n falls only 10% (320→287)
   while expectancy rises steadily. This is patient limit entry doing exactly
   what Part V already proved it does: saving the spread/fee on every trade
   that fills, with no change to trade *selection* (win rate is flat at
   47-49% throughout, exactly Part V's finding that gross edge never moves).
   +0.155R to +0.288R at 20-30bps offset, on 300+ trades, is a real, stable
   number given everything this project has verified about the mechanism
   producing it.

2. **The `advisory`-mode column is not trustworthy at face value**, and this
   needs to be said plainly rather than quoted as a headline number. n
   collapses to 76-94 — the swarm's gating rejects roughly 70% of the setups
   `off` mode takes — and the expectancy figures are noisy and non-monotonic
   in a way `off` mode's are not (57.8%→56.3%→57.1% win rate has no trend).
   The advisory@30bps cell (+0.791R, n=76) is the largest number in the whole
   table and the least trustworthy one in it: at n=76 a handful of large
   winners can move expectancy by tenths of an R, and the analyst-reliability
   diagnostic printed with every one of these runs says so directly — every
   analyst in every regime is still below the ~15-observation floor the
   meta-learner itself uses before it will trust a reliability weight (see
   the per-run tables: RANGE/volumeProfile n=23-31, RANGE/liquidity n=24-28,
   TREND/liquidity n=17-19, RANGE/traps n=11-13, across all six advisory
   runs). The swarm is gating trades on weights the project's own code
   considers statistically premature. This isn't a new finding — Part III
   already reported the swarm's measured impact as "mostly negative/neutral"
   — but this grid shows specifically *why* the occasional positive advisory
   number should not be banked on: it's a small, cherry-pickable sample
   riding on unvalidated weights, not a stable effect the way the `off`-mode
   trend is.

**The honest combined ceiling, stated once and precisely:** with the swarm
left in an advisory role turned **off** and execution moved to a 20-30bps
patient maker offset, this project's full pipeline — four coins, 418 days,
real fee and slippage costs, the corrected intrabar stop-check logic from
Part I, every validated finding from Parts I-VIII folded in as "things not to
bother retesting" — produces **+0.155R to +0.288R average expectancy over
287-300 trades**, a profit factor of 1.38-1.70, entirely attributable to
paying less to enter and exit, not to predicting direction. Turning the
swarm's advisory gating on does not add a validated increment on top of that;
it produces a much smaller, noisier sample whose best cell looks better only
because it is the easiest of twelve numbers to have happened by chance.

# Part X — Re-testing an external formula report, and the candle/liquidation
# indicator family

Two things arrived from outside this project's own research: a user-supplied
report of 192 tests of 16 tanh-wrapped formulas (F1-F8 on price/volume/
volatility, A-H adding OKX derivatives) against a barrier-race event (does
price hit +0.5% before -0.5% within a horizon of 2/4/8/16 bars on 15m data),
and a proposed five-indicator family (Forced-Flow Absorption Residual, Candle
Rejection w/ Volume, Liquidation Concentration Shock, Cross-Asset Stress
Breadth, Trend Efficiency & Fragility) built against a liquidation+OHLCV
dataset bundle. Both were re-implemented and tested with this project's full
discipline — plus two checks specific to a barrier-race structure that a
naive read of "win rate" would get wrong: causal (expanding-window) decile
thresholds instead of a fixed cutoff that could leak the future, and a
moving-block bootstrap instead of a binomial test, because a horizon-race
outcome sampled every bar is exactly the kind of heavily-overlapping series
that inflated 18 anomalies to false significance back in Part II.

**Two rounds were needed to trust the result.** The first implementation had
a real performance bug — the causal decile threshold re-sorted the entire
"seen so far" history array at *every single bar*, which is O(n² log n) over
a ~40,000-bar series. Neither script finished; one ran 1h43m, the other
1h14m, both killed and fixed (refresh the sorted threshold every 200 bars
instead of every bar — still strictly causal, just not re-derived 40,000
times). Independently checking the first successful run then turned up a
second bug: the split-half stability check only ever computed the long
tail's first-half/second-half win rate, so a short-tail survivor's
"stability" was silently checked against the wrong tail's numbers entirely.
Both are fixed; the numbers below are from the corrected re-run.

## The liquidation-dependent indicators: untestable, and said so up front

Before testing anything against it, the liquidation feed was audited: OKX's
public liquidation-orders endpoint returns only its ~100 most recent records
per instrument — in this fetch, 100 events spanning 3-7 hours per coin.
Re-fetching gets a different recent 100, never a longer history. That is
far below this project's n≥300 floor for any HAC/FDR test, so Forced-Flow
Absorption Residual, Liquidation Concentration Shock, and Cross-Asset Stress
Breadth are reported only as descriptive snapshots of that one ~3-7h window
per coin (BTC/ETH/SOL all showed heavy net forced *selling*, F=−0.87 to
−0.99, in their respective captured windows) — not as tested hypotheses.
Dressing an n=100-over-a-few-hours sample up with a t-statistic would repeat
exactly the small-sample mistake this project's own Part II caught once
already.

## Candle Rejection & Trend Efficiency/Fragility: zero survive (again)

The two indicators that need only OHLCV — which this project has at 418
days depth for four coins — got the full test at all four requested
horizons (15min/30min/1h/1day). **0 of 224 tests (7 signal variants × 4
coins × 4 horizons × 2 tails) survive FDR.** Same pattern as every prior
part of this project.

## The formula report: the first result in this entire project to survive everything

This is different. **8 of the price formulas (F1_NonlinearPressure,
F3_TrendEfficiency, F6_Interaction, F7_Asymmetry, F8_MultiScale) show a
55-57% win rate at the 30-minute-to-1-hour horizon on BTC and ETH, stable in
sign across both halves of samples of 1,200-3,000 trades, surviving FDR
across 448 tests at p≈0.003-0.02.** That is a real, positive, HAC-equivalent-
significant, split-sample-stable, cost-floor-clearing result — the first
thing in sixteen studies (price transforms, microstructure impact, jump/
diffusion, transfer entropy, market gravity, permutation entropy, cross-
asset dispersion, the full entry/exit/hold grid, candle rejection, trend
fragility) to clear every one of this project's own gates simultaneously.

It needs three honest qualifications, not a headline without them:

1. **Effective rank of the 8 price formulas is 2.44 out of 8** — the same
   check Part VI ran on RSI/ROC/Bollinger etc. This is one real thing (short-
   horizon trend/momentum efficiency, expressed via a symmetric barrier
   instead of a hold-to-close return) restated in five slightly different
   linear combinations, not five independent discoveries.
2. **It is cost-fragile.** Converting each survivor's win rate to a
   breakeven round-trip cost (2×WR−1, in the 50bps-wide barrier's own units)
   gives 4.0-7.2bps for every price-formula survivor. This project's own
   Part I/V found realistic taker cost around 13-17bps and patient-maker
   execution reaching breakeven only around 0-10bps depending on offset —
   so this edge lives right at the boundary of what the best execution this
   project has demonstrated can reach, not comfortably inside it.
3. **The effect decays with horizon** — 26 of 56 long-tail tests clear
   WR>53% at h=2 (30min), falling to 18/56 by h=16 (4h) — consistent with a
   real but short-lived momentum-continuation effect, which is a sensible
   thing to find, not a red flag by itself, but it means this is not a
   "hold it and forget it" edge.

**The derivatives-formula results (D_RegimeWeighted, F_CrossPressure,
C_LiquidityCascade, A_ConvexFlow at 60-69% win rate, breakeven costs 10-19bps
— on paper the most attractive numbers in this entire project) get a fourth,
more serious qualification: every one of them is drawn from the same single
~33-day window** (2026-08-09 to 2026-09-11 — OKX's derivatives history
retention limit, the same ceiling noted throughout this project). Four
different formulas "confirming" a pattern inside one continuous 33-day
market episode is much weaker evidence than four independent months would
be — it is one regime, not four. This project has already watched exactly
this shape of finding (strong, clean, and confined to one narrow window)
evaporate once, in the cross-venue and seasonality studies' own early,
self-corrected mistakes. The derivatives numbers are worth re-checking
against a second independent window before being trusted at the same level
as the price-only result; they are not there yet.

## Where this leaves the project

The price-formula finding is real by every gate this project uses, and it is
the first one. It is also thin — a few basis points of edge that patient
execution can only just clear, decaying with horizon, and built from what
is effectively one underlying signal wearing five names. It belongs
alongside Part IX's finding, not above it: the swarm-off / patient-maker
ceiling of +0.155R to +0.288R remains the larger, better-established number,
built on 418 days and hundreds of trades rather than a symmetric barrier's
more forgiving payoff structure. Whether the two combine — patient entry
timed specifically around an F3/F8/F1-style trend-efficiency signal, tested
on this project's own asymmetric hold-to-close structure rather than the
report's barrier race — is the one genuinely new thing this round surfaced
that has not been tried yet.

# Part XI — Upgrading the surviving formula: what actually raises win rate

Asked directly: can the Part X survivor family be pushed to a higher win
rate? Four specific, testable ideas, not "tune it until it looks better" —
run on BTC/ETH at h=2 (30min), same discipline as Study 15 (causal decile
thresholds, block-bootstrap significance, split-half, FDR, cost-adjusted
breakeven):

1. **Ensemble** — average the five correlated formulas (F1, F3, F6, F7, F8)
   into one score, instead of using the single best (F3_TrendEfficiency)
   alone.
2. **Tighter percentile** — does the most extreme 5% or 2% of readings beat
   the top 10%, as a real monotonic signal should?
3. **Conviction** — require k-of-5 formulas to simultaneously sit in their
   own individual top decile at once.
4. **Regime filter** — split the F3 top-decile signal by whether the
   trend-efficiency feature itself is in a high or low regime.

**The ensemble is the one that actually works, consistently.** Averaging the
five formulas beats the single-best formula on *both* coins at *both*
thresholds where it was tested:

| coin | test | n | win rate | vs. F3-alone baseline | breakeven cost | split |
|---|---|---|---|---|---|---|
| BTC | F3 alone, top10% (baseline) | 1298 | 57.2% | — | 7.2bps | 59%/56% |
| BTC | Ensemble, top10% | 864 | 58.9% | **+1.7pp** | 8.9bps | 56%/62% |
| BTC | Ensemble, top5% | 579 | 59.1% | **+1.9pp** | 9.1bps | 59%/59% |
| ETH | F3 alone, top10% (baseline) | 1873 | 54.8% | — | 4.8bps | 56%/54% |
| ETH | Ensemble, top10% | 1544 | 56.3% | **+1.5pp** | 6.3bps | 55%/57% |
| ETH | Ensemble, top5% | 931 | 56.1% | **+1.2pp** | 6.1bps | 55%/57% |

That's a real, mechanism-explainable improvement, not noise: five correlated
but not identical measurements of the same underlying short-horizon trend-
efficiency signal, averaged, cancel some of each other's idiosyncratic
noise — the textbook reason ensembling helps. It raises the breakeven cost
this edge needs to clear from 4.8-7.2bps to 6.1-9.1bps, which matters
concretely: Part V/IX found patient maker execution reaching breakeven
around 0-10bps depending on offset, so the ensemble version sits more
comfortably inside what this project has actually demonstrated it can
execute at, rather than right on the boundary.

**The other three ideas did not hold up as cleanly:**

- *Tighter percentile alone* (without ensembling) is not monotonic — BTC's
  top5% (55.8%) is actually *worse* than its top10% (57.2%); ETH's top5%
  (54.3%) is worse than its top10% (54.8%). A real signal sharpening under
  extremity would show a clean rise; this doesn't, which argues the single-
  formula tails contain real noise, not just a purer version of the signal.
- *Conviction (k-of-5 agreement)* helped on BTC at k≥4 (+0.9pp) but was flat
  or negative on ETH at the same threshold, and the strictest version
  (5-of-5, n=217-294) flipped direction between coins entirely (BTC −1.9pp,
  ETH +1.6pp) — exactly the small-sample inconsistency this project treats
  as a warning sign, not a result.
- *The regime filter* moved win rate by only 0.3-1.4pp and only on BTC's
  high-efficiency half; ETH barely moved at all.

**The honest upgrade, stated once:** ensembling the five formulas — not
picking a cleverer one, not trading less often on a stricter cutoff, not
requiring unanimous agreement — is the one change here with a mechanism, a
consistent direction across both coins tested, and a stable split-sample
sign. It moves win rate from ~55-57% to ~56-59% and roughly doubles the
margin over realistic execution cost. It does not change the underlying
finding's nature: it is still one short-horizon trend-efficiency signal, now
measured slightly better.

# Part XII — New features on top of the ensemble

Four candidates, each adding information the ensemble does not already use
(not just re-slicing m4/m16/vz/eff again): volume confirmation, cross-asset
agreement (does ETH's ensemble agreeing with BTC's, at the same timestamp,
raise BTC's win rate — real new information, since it can't be derived from
BTC's own price series), signal persistence (has the ensemble been in its
own top decile for ≥2 consecutive bars, vs. just crossing in this bar), and
session timing (Asia/Europe/US UTC blocks).

**Signal persistence is the one that holds up on both coins:**

| coin | test | n | win rate | vs. baseline | split |
|---|---|---|---|---|---|
| BTC | Ensemble top10% (baseline) | 864 | 58.9% | — | 56%/62% |
| BTC | AND sustained ≥2 consecutive bars | 243 | 61.3% | **+2.4pp** | 60%/62% |
| ETH | Ensemble top10% (baseline) | 1544 | 56.3% | — | 55%/57% |
| ETH | AND sustained ≥2 consecutive bars | 450 | 57.1% | **+0.8pp** | 57%/57% |

Consistent direction on both coins, both with tight, stable split-halves,
and a real mechanism: a signal that has stayed elevated for more than one
bar is less likely to be a single noisy spike and more likely reflects
sustained conviction. The cost is trade count — this filters out roughly
70-72% of the baseline's trades, so it trades much less often in exchange
for a meaningfully higher win rate on what's left (breakeven cost rises to
11.3bps on BTC).

**Volume confirmation and cross-asset agreement added nothing.** Gating on
elevated volume (vz>0) moved win rate by less than half a point either way
on both coins — unsurprising, since vz is already an ingredient inside four
of the five ensemble formulas, so this "new" gate wasn't actually new
information. BTC's win rate barely moved (−0.3pp, n=852 — 99% of the
baseline's trades) when requiring ETH's ensemble to simultaneously agree,
meaning the two coins' signals are usually already pointing the same way at
the same time and the gate filters almost nothing.

**Session timing is a genuine "interesting, not yet trustworthy" lead.**
Both coins were *worse* in the US session (16-24 UTC: BTC −4.8pp, ETH
−2.0pp) — a directionally consistent result worth noting. But their best
sessions disagreed (BTC liked Asia, +3.8pp; ETH liked Europe, +1.2pp), six
session/coin combinations were tested here (more multiple-testing exposure
than the other three ideas combined), and BTC's worst cell (US session)
has a first-half win rate of exactly 50% — a flip risk sitting right at the
edge, not a stable pattern. This needs a longer, purpose-built session
study (more coins, explicit FDR across all 24 individual hours rather than
3 wide blocks) before being trusted as more than a lead.

**The honest combined picture:** persistence is a real, validated upgrade —
trade less often, win more when you do. Volume and cross-asset gates are
dead ends here specifically because the ensemble already captures what
they'd add. Session timing deserves a dedicated follow-up, not a verdict
yet.

# Part XIII — Does it generalize across the whole market?

Every survivor so far held on only 2 of 4-6 coins (BTC, ETH). This runs the
exact same, already-frozen ensemble+persistence formula from Parts XI/XII —
no new tuning — against 28 more liquid USDT perpetuals (majors, L1s, L2s,
DeFi names, memecoins; ~84 days of 15m history each, fetched fresh from OKX
for this test), to ask directly: is this a market-wide phenomenon, or were
BTC/ETH a fluke?

**A data mistake happened while fetching this set, disclosed here because
it affects how one number below should be read.** Re-running
`fetch-klines.js` for BTC/ETH/SOL/DOGE/AVAX/LINK at a shallower depth
(sized for the new 84-day fetch) silently replaced their existing 418-day
cached history — the script merged across different intervals but not
within the same interval, so a shallower re-fetch overwrote deeper history
instead of extending it. Fixed (union old+new rows before deduping,
history can now only grow) and the 418-day depth is being restored for
those 6 coins in the background. Until that finishes, **BTC's number in
this Part is measured on a recent 84-day window, not the 418-day series
Part X/XI/XII used** — it is not a like-for-like replication and is
flagged everywhere it appears below.

## The answer: no, it does not generalize

| | result |
|---|---|
| Coins clearing WR>52% | 13 of 28 |
| ...and also split-half stable | 12 of 28 |
| Coins surviving FDR (q=0.10) across all 28 | **1 (BTC only)** |
| Split-half sign flips | 10 of 28 — roughly a third |
| **Pooled test (all 28 coins' top-decile trades combined)** | **n=12,053, WR=51.46%, p=0.24 — not significant** |

The pooled test is the cleanest single answer, and it is decisive: combining
every coin's trades into one sample — the honest way to ask "is there a
market-wide edge" instead of eyeballing which individual coins happened to
clear 52% — gives a win rate barely above a coin flip with a p-value of
0.24. That is indistinguishable from noise. A third of the 28 coins flip
sign between the first and second half of their own sample, which is
exactly what pure noise scattered around 50% produces, not what a real,
shared signal would do.

BTC (67.7% WR, n=167, the sole FDR survivor here) is the one exception —
but on the *84-day* window this fetch used, not the 418-day one Part XI/XII
measured 57.2-58.9% on. A win rate that jumps from ~58% to ~68% when the
window shrinks from 418 days to 84 is itself a warning sign this project
has flagged before (Part X's single-33-day-window derivatives caveat,
almost verbatim): it means this particular 84-day stretch was unusually
favorable for BTC specifically, not that BTC is uniquely 68%-predictable.
ETH's number here (57.3%, n=293) sits close enough to Part XI's original
54.8-56.3% to look like a genuine, if modest, replication rather than
window-selection luck — but it, too, is on the shorter window and will be
re-checked once the 418-day restore finishes.

The persistence filter, applied across the same 28 coins, does not rescue
this: BTC again leads (68.5%, but n collapses to 54 trades on the short
window), a handful of coins (SOL, XRP, LINK, FIL) show a modest positive
tilt, but 11 of 28 flip sign and the overall pattern is the same scatter,
not a market-wide effect.

## What this means for the project's central finding

Part XI/XII's result is not overturned — BTC and ETH's numbers on their own
418-day series still stand as measured, and ETH's rough replication here is
a mildly encouraging sign. But "the whole crypto market" was the right
question to ask, and the honest answer is that this specific short-horizon
trend-efficiency signal is not a market-wide phenomenon. It is, at most, a
BTC/ETH-specific (large-cap, deep-liquidity) effect, and even that should
now be held with slightly less confidence than before this test, precisely
because the one thing that would have made it more convincing — the same
edge showing up broadly across many coins — did not happen. A pending
follow-up re-checks BTC/ETH once the accidentally-shortened cache is
restored to its original 418-day depth, to settle whether the numbers in
Part XI/XII still hold exactly, or shift now that a few more weeks of data
sit at the end of the series.

# Part XIV — Testing two user-supplied "universal crypto market equations"

Two uploaded specifications, CME-Ultimate and CME-X, describe the same
architecture in different words: a multi-scale momentum/coherence/regime
core, a cross-asset field (breadth, relative value, lead-lag,
synchronization, entropy), a temporal-geometry layer (persistence,
acceleration, exhaustion), a liquidity-stress discount, a derivatives-
reflexivity term, all combined and passed through tanh. Both documents are
explicit that the goal is a discovered *invariant*, not a fitted curve, and
both name the same decisive test before trusting one (CME-X section 22,
its own words: "the most important test"): train on one set of coins, test
on a completely different set, reject anything that doesn't survive.

```bash
node research/cme-invariant-test.js --cost 4
```

## What was actually built and tested

The shared "universal" (price+volume+cross-asset) core was implemented
across all 6 coins CME-X names — BTC/ETH/SOL/DOGE/LINK/AVAX, ~418 days of
15-minute bars, 40,099 aligned bars per coin. Both documents ask for
"learnable" coefficients everywhere; doing that literally would mean fitting
dozens of parameters from one pass over this data with no held-out check —
exactly the "immediately optimize thousands of coefficients" both documents
themselves warn against. So the lower-level sub-weights (the K={1..128}
momentum blend, the regime-gate blend, the liquidity-stress blend, the
lead-lag lags) were fixed at equal weight, and only the top-level
combination across 15 named state features was fit, by ridge regression,
strictly on the training rows. This is the same two-tier discipline Part VI
used for its own "combine everything into one score" model.

The derivatives-reflexivity term was left out of this core test on purpose:
OKX only retains ~30 days of derivatives history (this project's own
recurring caveat since Part VI), and folding a 30-day-bounded term into a
418-day, 6-coin invariance test would silently cut the whole test down to
30 days.

Three experiments, each evaluated with this project's full standard
discipline (moving-block bootstrap significance — a barrier-race outcome is
heavily overlapping, Part II's exact false-positive trap — FDR correction,
split-half stability, a 4bps cost floor):

- **Experiment A** — fit on BTC+ETH+SOL, freeze the weights, test on
  DOGE+LINK+AVAX, never seen during fit.
- **Experiment B** — the reverse: fit on DOGE+LINK+AVAX, test on
  BTC+ETH+SOL.
- **Experiment C** — time invariance: same coins, fit on the first 60% of
  the series in time, test on the last 40%. This separates "fails because
  of a different asset" from "fails because of a different period".

## The result

**A short-horizon (30-60 min) effect is real, but it is concentrated on
BTC/ETH specifically, not on the coins used to fit the weights.**

| experiment | best result | worst result at the same horizon |
|---|---|---|
| A: train majors, test alts (DOGE/LINK/AVAX) | DOGE, h=2 (30min): 54.3% WR, +0.006R, p=0.003, stable split (54%/55%) | AVAX, h=2: 53.8% WR but **negative** expR (-0.005) |
| B: train alts, test majors (BTC/ETH/SOL) | **BTC, h=2: 61.1% WR, +0.143R, p=0.003, stable split (57%/64%)** | SOL, h=2: 53.1% WR, negative expR (-0.018) |
| C: same-coin, time-split only | **BTC, h=2: 60.8% WR, +0.136R** · ETH, h=2: 59.5% WR, +0.110R | LINK, every horizon: 47-49% WR, negative |

The pattern across all three experiments is the same regardless of which
coins supplied the fitted weights: **BTC and ETH show a strong, stable,
cost-clearing edge at h=2 (30 minutes)** that decays fast — by h=8 (2
hours) BTC/ETH are down to 52-54% and by h=16 (4 hours) mostly
indistinguishable from a coin flip. **SOL, DOGE, LINK and AVAX never show
more than a marginal, mostly cost-negative effect**, in any experiment,
including Experiment C where LINK trained and tested on nothing but its own
history and still came back flat-to-negative at every horizon.

The fitted top-level weights make the mechanism visible: in both
Experiment A and B, `M_COH` (the multi-scale momentum × coherence × regime
term) and `EX`/`BA` (exhaustion, breadth acceleration) get the largest
magnitude — everything else (`BREADTH`, `RV`, `LL`, `SYNC`, `SYNC_A`, `DH`,
the cross-asset/entropy machinery both documents spend most of their length
on) is shrunk to near zero by the ridge. That is the same shape Part VI
found checking RSI/ROC/Bollinger's effective rank, and the same shape Part
XII found for volume/cross-asset gates on the ensemble formula: the
sophisticated-sounding additions are not where the signal lives.

## The verdict, by the documents' own stated rule

CME-X section 49, verbatim: *"If it finds Formula A: 92% historical win
rate but unseen assets = 51%... REJECT IT... If it works on BTC/ETH but
fails on SOL/DOGE/LINK/AVAX: REJECT or classify as asset-specific."*

Applying that rule to this project's own result: **REJECT as a universal
invariant. Classify as BTC/ETH-specific.** Experiment A — the literal
"train on majors, does it transfer to alts" test both documents call the
most important one — clears essentially nothing: one borderline survivor
(DOGE, +0.006R, barely above the 4bps floor) out of twelve cells, and every
other alt-coin cell is either not significant or has negative
cost-adjusted expectancy. The strength in Experiments B and C shows up
because BTC and ETH are in the *test* set, not because DOGE/LINK/AVAX's
history taught the model anything transferable — training on alts and
testing on majors works about as well as training on majors and testing on
the same majors in a later period (compare Experiment B's BTC h=2 +0.143R
to Experiment C's BTC h=2 +0.136R — nearly identical, which is the tell
that the alt-coin training data wasn't doing the work).

This is not a new phenomenon for this project. It is the same effect Part
X/XI/XII already characterized — a real, short-horizon, BTC/ETH-specific
trend-efficiency signal that decays with horizon — arrived at independently
through a completely different, much larger feature construction (cross-
asset breadth, synchronization, entropy, lead-lag, exhaustion, none of
which existed in the Part X-XII formulas), and landing on the same
conclusion Part XIII already reached when it asked the market-wide
question directly: real on BTC/ETH, not a market-wide invariant.

## What's honestly still open

- The derivatives-reflexivity term (OFI × OI × funding) was not tested here
  for the reason stated above. Part X already tested a derivatives-enhanced
  formula family on the same ~30-day window and found real-looking numbers
  confined to one continuous market episode — worth a look, not worth
  trusting at the same level as this 418-day result.
- The lower-level sub-weights (momentum-horizon blend, regime-gate blend,
  lead-lag lags) were fixed at equal weight rather than fit, on the
  MDL/no-thousand-coefficient grounds stated above. It's possible a properly
  cross-validated fit of those too would move the numbers — but given how
  hard the top-level ridge already shrank the cross-asset/entropy terms
  toward zero, there is no positive evidence yet that a bigger search would
  find something this one didn't.
- Placebo/shuffled-label and shuffled-asset tests (CME-X sections 26-27)
  were not run. Given the result already fails the project's and the
  documents' own headline test, running the remaining robustness checks on
  a result already classified "asset-specific, not universal" would mostly
  confirm what Experiment A already showed.

# Part XV — CME-X2: a revision that erased the one real thing Part XIV found

A follow-up specification, CME-X2, was uploaded next — explicitly written
as a response to Part XIV, quoting its exact BTC/ETH numbers back as "the
current baseline to beat." Three real structural changes from CME-X:

1. **Volatility-relative targets.** Instead of Part XIV's one fixed
   +/-0.5% barrier for every coin and regime, TP/SL scale to causal
   realized volatility: `B_t(H) = sigma_t * sqrt(H)`, `TP = alpha*B_t`,
   `SL = beta*B_t` — the document's own example values, alpha=1.0,
   beta=0.75, used as given rather than re-fit (same one-thing-at-a-time
   discipline as fixing Part XIV's low-level sub-weights).
2. **An explicit "Market Potential" construct** and the document's own
   proposed core equation (its section 49): `I = tanh(w1*D*C*POT + w2*F*D
   + w3*RR*SYNC + w4*P*A - w5*X*L + w6*E*C)` — six named interaction terms,
   fit by ridge, replacing Part XIV's flat blend of 15 raw features.
3. **Separate long/short models.** The document explicitly bans
   `P_SHORT = 1 - P_LONG`; two independent fits were run, one per
   direction, each against its own success event.

```bash
node research/cme-x2-test.js --cost 4
```

Same 418-day, 6-coin dataset as Part XIV. Every mandatory test the
document itself names was run: the universal-asset test (train
BTC+ETH+SOL / test DOGE+LINK+AVAX, and the reverse), leave-one-asset-out
for all 6 coins individually, and a 60/20/20 time-transfer split.

## A performance bug worth naming, because it nearly ended the study

The first implementation recomputed each causal Z-score by rescanning its
full trailing window from scratch at every call site — and the entropy
feature called that rescan 64 times per bar. That's O(n × 64 × window)
compounding across 6 coins and ~40,000 bars, and it did not finish in 10
minutes of wall time before being killed. Rewritten as O(1)-amortized
sliding-window statistics (running sum / sum-of-squares for mean and std,
running cross-sums for covariance) — mean/std in place of median/MAD,
mitigated by clamping every Z-score to [-4,4] before use, same as before —
it now runs the full battery in under 3 minutes. Worth stating plainly:
this is the second study in a row where the actual engineering bottleneck
was accidental O(n²)-shaped code, not the statistics.

## The result: no edge anywhere, on any coin, in any experiment

| test | LONG topN win rate range | SHORT topN win rate range | AUC range (both directions) |
|---|---|---|---|
| Universal A (train majors, test alts) | 39.5–43.6% | 39.9–46.5% | 0.503–0.525 |
| Universal B (train alts, test majors) | 38.9–42.4% | 42.2–46.2% | 0.508–0.523 |
| Leave-one-asset-out (all 6 coins) | 38.9–43.3% | 40.5–46.7% | 0.504–0.524 |
| Time transfer (per-coin, time-split only) | 37.5–47.3% | 36.4–45.7% | 0.481–0.519 |

**175 of 180 tests "pass" FDR correction (p ≤ 0.093) — and every single one
of them is negative or indistinguishable from the fair baseline. Zero
clear the cost floor with a stable split-sample sign.** That combination —
significant by p-value, wrong sign or no lift — is exactly what a real,
if small and unhelpful, effect looks like, not what noise looks like (pure
noise would scatter roughly evenly around the baseline; this is uniformly
on one side of it).

The most telling number is AUC, because it's a full-sample rank statistic
that doesn't depend on where a decile cutoff happens to fall: **every
single AUC in this entire study sits between 0.48 and 0.52** — literally
indistinguishable from a coin flip's ranking ability. Compare that to Part
XIV's cleanest result, BTC at 30 minutes: AUC 0.556 (train alts) / 0.588
(same-asset time-split). That was a thin edge, but it was a real,
measurable one. Here, on the identical BTC and ETH price history, it is
gone entirely.

**One reporting mistake worth flagging rather than hiding:** this study's
own console output initially compares raw win rates against CME-X2's own
benchmark table (55% weak / 58-61% baseline / 62%+ target), which assumes
a standard 50%-random-baseline scale. It doesn't apply directly here.
Because alpha (1.0) and beta (0.75) are unequal, the TP sits farther from
entry than the SL does, so a pure random walk under this exact barrier
geometry has a fair, breakeven win rate of about **42.9%**, not 50% —
derived from solving `WR × (alpha/beta) = (1 − WR)`. Every observed win
rate in this study (37–47%) clusters right around that 42.9% figure, not
around 50%. So "RANDOM" is still the right verdict — the topN selections
show no measurable lift over the fair baseline either way — but the
*reason* is "no lift over 42.9%," not "far below 50%." Anyone re-running
this with a different alpha/beta ratio needs to re-derive that baseline
before reading win rates off CME-X2's own 50%-anchored scale.

## Why this is more informative than "also fails"

Part XIV, on the same data, the same coins, and the same underlying idea
(momentum × coherence, cross-asset context, temporal geometry), found a
real if thin BTC/ETH-specific edge. CME-X2 — a more elaborate
construction of essentially the same ingredients, aimed explicitly at
*improving* on that number — found nothing at all, on any coin, in any of
three independent validation designs. Something in the revision actively
destroyed the one real signal in the system rather than merely failing to
add to it. Two candidate mechanisms, not yet isolated from each other:

1. **The asymmetric barrier itself is a much harder target.** A symmetric
   50/50 barrier (Part XIV) gives every trade the same fixed payout
   structure; an alpha≠beta barrier changes the fair baseline and, more
   importantly, changes which *kind* of move counts as a win — a shallow
   drift that would have closed a symmetric barrier in profit now needs to
   travel proportionally farther to clear the bigger TP before the closer
   SL catches it. That's a structurally different, likely harder,
   statistical target, independent of anything about the formula scoring
   it.
2. **Market Potential may be diluting rather than sharpening the momentum
   term it's built from.** `POT = tanh(|D|·C·(1+F)/(1+|X|+|L|))` is built
   from `|D|` (magnitude, not sign) and can itself go negative whenever
   `F < -1`, since nothing in the specification floors `(1+F)` at zero.
   `D·C·POT` — the single largest fitted weight in both Universal Asset
   Tests, exactly like Part XIV's `M_COH` was — is therefore a noisier,
   more conditionally-signed version of the same momentum×coherence term
   that worked before, rather than a strict refinement of it.

Distinguishing these needs one further, cheap experiment neither this nor
Part XIV ran: score Part XIV's original symmetric barrier with THIS
study's core-equation structure, and separately score THIS study's
asymmetric barrier with Part XIV's flat feature blend. Whichever one still
shows the BTC/ETH edge identifies which change actually broke it. That
2×2 is the natural next step, not yet run here.

## What this means for the project

CME-X2's own section 56 lists what would count as a discovery; section 57
lists what would invalidate the theory, including "performance disappears
on unseen assets" and "only one short time period works." This result
clears neither bar in either direction — it isn't a discovery, but it also
isn't the specific asset-only or time-only failure CME-X2 anticipated. It
is a plainer thing: the specific formula as revised carries no detectable
information at all, by the single most theory-agnostic measure available
(AUC), on data where a related but simpler construction did. The standing
result to build from is still Part XIV's: a real, thin, BTC/ETH-specific,
short-horizon momentum effect — and, so far, every attempt to make it more
sophisticated has made it disappear rather than improve it.

# Correction — a label-pooling bug in Parts XIV and XV, found while building Part XVI

While building the next study (below), a real bug turned up in code shared
by Part XIV (`cme-invariant-test.js`) and Part XV (`cme-x2-test.js`), and
it needs disclosing before anything else.

**The bug.** Every multi-coin training step built its labels like this:

```js
const trainY = new Map();
for (const s of trainSymbols) {
  const y = outcomesFor(s, horizon);
  for (const r of rows[s]) if (y.has(r.i)) { trainRows.push(r); trainY.set(r.i, y.get(r.i)); }
}
```

`r.i` is each coin's own 0..n-1 position in its aligned series — the same
range for every coin, since all 6 are aligned to one common timeline.
BTC's row `i=5000` and DOGE's row `i=5000` are different bars that happen
to share that index. `trainY` is a single `Map` keyed only by `i`, so
pooling multiple coins into it let whichever coin was processed last
silently overwrite every earlier coin's label at every shared index. In
practice this meant every multi-coin ridge fit trained most of its rows
against the *last-processed coin's own outcome sequence*, applied to
other coins' feature vectors at the same timestamps — not each row's
actual outcome.

**What it did and didn't affect.** Only steps that pool more than one
coin into one fit: Part XIV's Experiments A and B, and Part XV's
Universal Asset Tests A/B and leave-one-asset-out. Any single-coin fit
(Part XIV's Experiment C, Part XV's Time Transfer Test) builds and uses
its `trainY` within one coin's loop iteration — no collision possible,
unaffected.

**The fix.** Every row now carries its own `y` embedded (`{i, x, y}`)
instead of being looked up afterward through a shared index-keyed map,
which makes this class of bug structurally impossible rather than
relying on remembering to key correctly. Applied to both files
(`cme-invariant-test.js`, `cme-x2-test.js`) and to the new study below.

**Re-verified: both studies' conclusions hold.** Every affected
experiment was re-run. The numbers move by roughly the width of ordinary
sampling noise, and every finding stands as reported:

| | before | after |
|---|---|---|
| Part XIV, Experiment B, BTC h=2 | WR 61.1%, expR +0.143 | WR 62.6%, expR +0.172 |
| Part XIV, Experiment A, DOGE h=2 | WR 54.3% | WR 54.8% |
| Part XV, Universal A, DOGE h=2 long | WR 41.8% | WR 41.5% |
| Part XV, FDR summary | 175/180 pass, 0 solid | 175/180 pass, 0 solid |

The likely reason the bug didn't distort the results more: crypto coins
share enough common market-wide structure (this project's own SYNC/
synchronization measurements put pairwise correlation well above zero
throughout) that training against "AVAX's real outcome, applied to DOGE's
and LINK's feature vectors at the same timestamp" is not radically
different from training against each coin's own outcome — the mislabeling
is real but the substitute label is still a real, correlated momentum
target, not noise. That is a fortunate accident, not something to rely on
next time; the fix stands regardless.

# Part XVI — CME-X3 (scoped): a real ML challenger, a persistence test, and the honest verdict

A third specification, CME-X3, was uploaded next — 53 sections calling for
a full research program (random forests, gradient boosting, neural nets,
symbolic regression search, hierarchical clustering, conformal prediction,
Pareto frontiers across model families). This environment has no
sklearn/numpy/xgboost, pure Node.js only, and implementing the full
document in one sitting would produce a shallow result across 53 sections
rather than a trustworthy one on a focused few. With the user's explicit
sign-off, this scoped to four things:

1. **Close Part XV's open question.** Part XIV (symmetric barrier, flat
   features) found a real BTC/ETH edge; Part XV (asymmetric barrier,
   "Market Potential" interaction terms) found nothing on the same data.
   A 2×2 — {symmetric, asymmetric} barrier × {flat, interaction} feature
   structure — isolates which change was responsible.
2. **A genuine nonlinear challenger.** Every prior study fit only linear
   (ridge) blends. A from-scratch gradient-boosted shallow-tree model
   (depth-2 trees, 40 rounds) can express conditional relationships
   ("feature A matters only at high volatility") no linear blend can,
   without needing symbolic-regression infrastructure this session
   didn't have time to build correctly.
3. **Signal-vs-persistence decomposition**, precisely as CME-X3's own
   section 53.24 asks: signal alone, persistence alone, both together,
   and persistence computed on a shuffled version of itself (a
   placebo control with the identical marginal distribution but no real
   temporal link to any given bar).
4. **Leave-one-asset-out on all 6 coins, cost robustness, and threshold/
   selectivity robustness**, ending in one of the document's own three
   mandated verdicts (its section 53.36).

Not implemented, and why: random forests and neural nets (redundant with
the gradient-boosted-tree challenger for the question "can nonlinearity
find what linear blends missed" — one genuine nonlinear model answers it);
symbolic regression search, hierarchical/regime-conditional models, state
clustering, distribution-shift detection, and conformal prediction (each
a real sub-project, only worth building once something here survives the
tests below).

```bash
node research/cme-x3-test.js --cost 4
```

## Part 1 — the 2×2, resolved

Train DOGE+LINK+AVAX, test BTC+ETH+SOL, h=2 (30 min) — the exact cell
Part XIV found strongest:

| barrier | structure | fair baseline | BTC AUC | ETH AUC | SOL AUC |
|---|---|---|---|---|---|
| symmetric | flat | 50.0% | 0.523 | 0.534 | 0.527 |
| symmetric | interact | 50.0% | 0.515 | 0.519 | 0.520 |
| asymmetric | flat | 42.9% | 0.532 | 0.538 | 0.531 |
| asymmetric | interact | 42.9% | 0.514 | 0.518 | 0.519 |

Two things resolve here. First, **AUC barely moves between barriers** —
0.52-0.54 whether symmetric or asymmetric, whether flat or interaction
terms. What moved in Part XV wasn't the ranking ability, it was **whether
the fair baseline eats the entire top-decile win rate**: under the
asymmetric barrier every "lift" (topWR minus the 42.9% fair baseline) is
tiny or negative even where AUC is highest, because the payout structure
itself is harder to profit from, not because the signal vanished from a
ranking point of view. Second, **flat consistently beats interact** at
every barrier — the "Market Potential" interaction terms lose a little
ranking power relative to the plain feature blend regardless of which
barrier is used, confirming Part XV's second hypothesis (the interaction
structure dilutes the signal) over its first (the barrier alone was
responsible). Both changes hurt; the interaction-term structure is the
larger of the two.

## Part 2 — the model tournament: a small, real, worthless edge

Leave-one-asset-out, h=2, the winning (asymmetric, flat) combination,
mean AUC across all 6 held-out coins:

| model | mean AUC |
|---|---|
| **gbm (gradient-boosted trees)** | **0.5334** |
| ridgeFlat | 0.5316 |
| ridgeInteract | 0.5162 |
| momentum (raw D, unfit) | 0.5099 |
| persistenceOnly | 0.5027 |
| random | 0.5019 |
| meanReversion | 0.4901 |

The nonlinear model wins, barely — 0.5334 vs. ridge's 0.5316, a gap well
inside noise. Nonlinearity was not hiding a stronger relationship a
linear blend missed. **All 42 leave-one-out cells (7 models × 6 coins)
are negative or worthless in expectancy after a 4bps cost, despite every
one nominally "passing" FDR at p≈0.003** — the tightest p-value this
bootstrap can report, because the effect is consistent enough in
*direction* to be non-random while remaining too small to trade. Zero
cells survive the combined cost-floor-plus-stable-split filter.

## Part 3 — persistence contributes nothing

| variant (BTC held out) | AUC | topWR | expR |
|---|---|---|---|
| signal only (persistence zeroed) | 0.533 | 41.5% | −0.086 |
| persistence only | 0.499 | 38.9% | −0.145 |
| both together | 0.533 | 41.4% | −0.087 |
| **persistence replaced by a shuffled version of itself** | **0.533** | **41.4%** | **−0.088** |

Signal-only, both-together, and the shuffled-persistence placebo are
statistically identical (0.533 AUC all three ways); persistence alone is
indistinguishable from random (0.499). Replacing real persistence with a
placebo that has the same marginal distribution but zero genuine temporal
relationship to any given bar changes **nothing** — proof that whatever
this model captures comes entirely from the non-persistence features,
and persistence itself (contra this project's own earlier suspicion in
Part XII) is not doing any of the work here, for better or worse.

## Part 4 — cost and selectivity robustness: it dies at the first bps

Gradient-boosted model, BTC held out:

| cost | win rate | expR |
|---|---|---|
| 0 bps | 43.1% | **+0.006** |
| 2 bps | 43.1% | −0.020 |
| 4 bps | 43.1% | −0.047 |
| 8 bps | 43.1% | −0.100 |
| 16 bps | 43.1% | −0.207 |

The edge is barely positive at literally zero transaction cost and goes
negative the moment any realistic cost is applied — 2bps is far below
even the cheapest maker fee this project has ever modeled. The
selectivity curve confirms this isn't a concentration-of-confidence
problem either: win rate sits at 41-43% whether trading the top 1% of
signals or all of them, which is what a diffuse, uniform, barely-there
tilt looks like — a genuinely strong signal concentrates its edge in its
most confident decile, and this one doesn't.

## Final verdict

> **NO ROBUST UNIVERSAL INVARIANT FOUND.**

Per CME-X3's own decision rule (section 53.36): the tournament clears
FDR almost everywhere (42/42) but zero cells clear the combined
cost-and-stability bar, the best model (gradient boosting) beats a coin
flip by 0.033 AUC — not the 0.55+ this project's other results call
weak-but-real — and that entire edge evaporates before 2bps of cost. This
is a more decisive, more cautious answer than Part XIV's "a real, thin,
BTC/ETH-specific effect" — under a harder, more realistic (volatility-
relative, asymmetric) target and with a genuine nonlinear challenger
added specifically to rule out "a linear blend just isn't flexible
enough," nothing here would survive contact with a live order book.

Part XIV's original ±0.5%-fixed-barrier result still stands on its own
terms (re-verified above, unaffected by the label-pooling bug). What Part
XVI adds is the harder, more skeptical retest CME-X3 itself asked for —
and on that retest, nothing survives.

# Part XVII — CME-X4 forensic recovery: the edge is real, and here is exactly where it lives

Every prior part since Part XV asked "can we build something better." This
one asks a different, better question, put directly by the user: *"What
measurable market state causes the small predictive edge observed in
CME-X, and under what conditions does that state cease to exist?"* Not
another formula — a post-mortem on the one real result this project has
produced (Part XIV's BTC/ETH edge), designed to find out whether it's
genuine microstructure, an artifact of one coin, one regime, persistence,
target construction, multiple-testing luck, or a leak.

```bash
node research/cme-x4-forensic.js --cost 4
```

## Method: freeze the original, perturb everything around it

The exact Part XIV construction (`buildAssetSeries`, `buildCrossAsset`,
`buildTemporal`, the 15-feature `FEATURE_NAMES` vector) was copied
verbatim — not rebuilt, not adjusted — and fit exactly as Experiment B
did (train DOGE+LINK+AVAX, horizon 4). Those weights were **frozen once**
and reused, unmodified, for every branch below except the one explicitly
marked as a refit. Baseline reproduces Part XIV's own number exactly
(BTC AUC 0.557, WR 62.6%), confirming the frozen model is a faithful
reconstruction before touching anything.

| baseline (h=2, unmodified) | BTC | ETH | SOL |
|---|---|---|---|
| AUC | 0.557 | 0.547 | 0.521 |
| win rate | 62.6% | 58.8% | 52.5% |
| expectancy | +0.212R | +0.137R | +0.011R |

## Removal: momentum matters, volatility and volume don't

Each named feature's real-time value was neutralised (set to its training
mean) at prediction time, frozen weights otherwise untouched:

| removed | BTC AUC | BTC WR | verdict |
|---|---|---|---|
| momentum (M_COH → mean) | 0.557 → **0.535** | 62.6% → 58.6% | **matters most, but the edge survives its removal** |
| volatility (TREND_GATE → mean) | 0.557 → 0.554 | 62.6% → 61.6% | contributes almost nothing |
| volume (ABSD → mean) | 0.557 → 0.556 | 62.6% → 62.2% | contributes almost nothing |
| nonlinear interaction (M_COH → raw M, dropping the ×coherence×regime modulation) | 0.557 → **0.566** | 62.6% → 61.5% | **plain linear momentum does at least as well as the "nonlinear" version** |

That last row matters beyond this one study: it's the third independent
confirmation (after Part XV's interaction terms and Part XVI's 2×2) that
the multiplicative coherence/regime dressing around momentum never adds
what it promises — a plain linear momentum term captures the same edge,
sometimes slightly better by AUC.

## Shuffle: a real surprise — breadth acceleration, not momentum, is the single biggest lever

> **⚠ SUPERSEDED — see Part XVIII.** The conclusion in this section does not
> survive. Refitting with BA removed (Part XVIII, Part A) shows the model is
> *better* without it, at every differencing lag and under every composition of
> the breadth average. The shuffle test below measures how much a feature moves
> the frozen model's output, which is not the same as how much it contributes —
> and BA is 0.56–0.58 correlated with momentum by construction, because BTC and
> ETH are 89.5% of the dollar-volume weight in the breadth average it
> differences. Ridge splits weight across a collinear pair, producing a large
> shuffle effect with no removal cost. The rest of this part (the leak checks,
> the single-asset refits, the regime split) is unaffected.

Each of the 15 features was shuffled across time independently (breaks
temporal alignment, keeps the marginal distribution identical) — this
asks a sharper question than removal: does *when* this feature said what
it said matter, not just *what* it said on average.

| shuffled feature | BTC AUC drop |
|---|---|
| **BA (breadth acceleration, cross-asset)** | 0.557 → **0.517** (−0.040) |
| M_COH (momentum) | 0.557 → 0.531 (−0.026) |
| everything else (12 features) | ≤ ±0.008 |

**BA — how much the whole market's breadth has accelerated over the last
16 bars — moves the frozen model more than momentum does**, despite
momentum being the feature this whole formula family was named for. This
lines up with the frozen weights themselves: `BA=0.014` is the single
largest-magnitude weight in the fit, edging out `M_COH=0.006`. Every
other cross-asset/entropy/synchronization term this project spent three
studies building (Parts XIV–XVI) moves the needle by less than a
percentage point of AUC when shuffled — consistent with everything found
since Part XIV, except this one term.

**Label shuffle (the placebo control):** AUC collapses to 0.49–0.50 on
all three coins, exactly the coin-flip result a working evaluation
harness should produce when the answer is scrambled. The methodology
itself is sound.

## Shift: a clean leakage sanity check, and it passes

Features were re-evaluated from 2 bars before (stale) or 2 bars after
(future) the real decision bar, scored against the same real outcome:

| shift | BTC AUC | BTC WR |
|---|---|---|
| backward 2 bars (stale) | 0.542 | 57.7% |
| **none (the actual result)** | **0.557** | **62.6%** |
| forward 2 bars (future leak, deliberately contaminated) | **0.829** | **96.3%** |

The forward-shift number is supposed to look absurd — feeding the model
information computed 30 minutes after the fact makes it look almost
perfectly predictive, because at that point the "feature" is measuring
the very move being predicted. That is exactly the shape a genuine leak
produces. **The real, unshifted result (0.557 / 62.6%) looks nothing
like that** — if the original construction had an off-by-a-bar alignment
bug of this kind, the baseline would already look like the forward-shift
row, not sit at a modest 62.6%. This is a real, informative negative
result: no leak of this shape is present.

## Single-asset-only: BTC and ETH support it alone, with zero cross-asset pooling

The identical formula, refit on each coin's own history alone (60/40
split, no other coin's data involved at all):

| coin | AUC | win rate | p |
|---|---|---|---|
| **BTC** | 0.588 | 57.2% | 0.076 |
| **ETH** | 0.554 | 57.3% | **0.007** |
| SOL | 0.521 | 52.4% | 0.279 |
| DOGE | 0.511 | 49.2% | 0.787 |
| LINK | 0.494 | 47.4% | 0.299 |
| AVAX | 0.540 | 52.5% | 0.316 |

This is the cleanest asset-specificity evidence in the whole project.
BTC and ETH support the edge on **nothing but their own price history** —
no cross-asset training, no pooling, no borrowed labels. DOGE/LINK/AVAX
don't, whether trained alone (here) or pooled with everything else
(Parts XIV–XVI). The phenomenon is not a training-setup artifact; it is
intrinsic to BTC and ETH specifically.

## Regime: the edge lives in positive momentum, and doesn't need high volatility

Baseline scores, partitioned by regime (thresholds fixed from the
training population only, never from the test data being split):

| regime | BTC WR | BTC AUC |
|---|---|---|
| **positive momentum (M>0)** | **62.0%** | 0.551 |
| negative momentum (M<0) | 47.8% (below breakeven) | 0.502 (no info) |
| high volatility | 61.1% | 0.550 |
| low volatility | **66.5%** | 0.571 |
| high volume | **63.3%** | 0.567 |
| low volume | 55.2% | 0.535 |

**The edge is almost entirely one-sided**: it lives in positive-momentum
states and is essentially absent (AUC ≈ 0.50, WR below the 50% breakeven)
when momentum is negative — this formula finds continuation, not
reversal, and only in one direction. It does **not** require high
volatility to appear (if anything it's slightly stronger in low-vol
conditions, the opposite of the "compression precedes opportunity"
intuition several of the uploaded specs assumed) and is somewhat stronger
with elevated volume, though present either way.

## The forensic conclusion

Applying the same standard the user proposed: *if the original edge
survives untouched on BTC/ETH but nowhere else, the correct conclusion
isn't "universal formula failed" — it's "CME-X discovered a BTC/ETH-
specific short-horizon market effect."* That is exactly what happened
here, now with a mechanism attached rather than just a number:

> **A short-horizon, BTC/ETH-specific continuation effect, present
> specifically when recent momentum is positive, driven mostly by (1) the
> asset's own plain (not coherence/regime-modulated) momentum and (2) —
> unexpectedly, and more strongly — market-wide breadth acceleration.**
> *[Part XVIII retracts clause (2): BA contributes nothing. It also rewrites
> "BTC/ETH-specific" as the top of a 16-coin liquidity gradient.]* **
> Volatility framing and volume framing, as encoded in this formula, are
> not load-bearing. It shows no sign of the leakage shape a real leak
> would produce, and it does not depend on cross-asset training data to
> appear.**

## What's now worth testing, precisely because this survived

- **Breadth acceleration deserves its own follow-up.** It was one
  ingredient among fifteen, added almost as an afterthought in Part XIV;
  this study says it may be doing more work than momentum itself. A
  dedicated study isolating BA (its own removal ablation, its own
  horizon sweep, its own asset-normalized representation) is the
  single highest-value next step this project hasn't done yet.
- **The asset-normalized-representation question the user posed is now
  the right one to ask.** Given the mechanism (own momentum + market
  breadth acceleration, positive-momentum-only), is there a scaling or
  normalization under which this stops being "a BTC/ETH fact" and
  becomes "a large-cap / high-market-weight fact" that would apply to
  whichever coins are large enough next cycle? That's testable (rank
  coins by market weight instead of by name) and hasn't been tried.
- **The positive-momentum-only asymmetry is itself a lead.** Every
  study so far has scored a single symmetric "up before down" event.
  A model that explicitly separates long and short setups — not
  Part XV/XVI's failed attempt at that, but built around momentum sign
  as a hard gate rather than a continuous feature — follows directly
  from this result.

# Part XVIII — CME-X5: the breadth finding was an artifact, and the real result is a liquidity gradient

Part XVII closed with two recommendations, and the user picked both: isolate
breadth acceleration (BA), which the forensic study had flagged as a bigger
lever than momentum itself, and test whether the BTC/ETH edge has an
asset-normalized representation — is it a *BTC/ETH fact* or a *large-cap
fact*? Those became Parts A and B below. Parts C and D were added mid-study
when the first results made them necessary.

```bash
node research/cme-x5-normalization.js --cost 4          # Parts A-D
node research/cme-x5-collinearity.js                    # the corr(M_COH, BA) diagnostic
```

Raw output is committed at `research/results/cme-x5-run-ABC.log` and
`research/results/cme-x5-run-BD.log`.

## Part A — breadth acceleration is not the engine. It is not even load-bearing.

Refit the frozen Part XIV construction on DOGE+LINK+AVAX under seven feature
subsets, test on BTC/ETH. 6 coins, 40,099 aligned bars (418 days), fit
horizon 4, eval horizon 2, ±0.5% barrier, 4bps cost.

| variant (refit each time) | BTC AUC | BTC WR | BTC expR | ETH AUC | ETH WR |
|---|---|---|---|---|---|
| full 15-feature (reference) | 0.557 | 62.6% | +0.212 | 0.547 | 58.8% |
| minus BA | **0.563** | 61.1% | +0.181 | 0.547 | 58.2% |
| minus momentum (M_COH) | 0.552 | 62.1% | +0.201 | 0.544 | 57.1% |
| minus ALL cross-asset (8 terms) | **0.571** | 59.7% | +0.154 | 0.551 | 57.7% |
| BA only (1 feature) | 0.554 | 54.5% | +0.050 | 0.547 | 54.2% |
| **momentum only (1 feature)** | **0.574** | 60.8% | +0.176 | **0.558** | 56.7% |
| momentum + BA (2 features) | 0.559 | 55.4% | +0.067 | 0.550 | 55.2% |

**This reverses Part XVII's headline.** Removing BA does not hurt the model —
it slightly improves it (0.557 → 0.563). A single momentum feature beats the
full fifteen (0.574 vs 0.557), and dropping all eight cross-asset terms is the
second-best variant. Adding BA *to* momentum actively destroys the signal:
0.574/60.8%/+0.176 collapses to 0.559/55.4%/+0.067.

BA-only deserves its own line. Its AUC (0.554) looks respectable, but its
top-decile win rate is only 54.5% against momentum-only's 60.8%. BA's ranking
information is diffuse — it does not concentrate in the high-conviction tail
where trades are actually taken. AUC alone would have hidden that.

The BA differencing-lag sweep (momentum+BA, 2 features) says the same thing
from another direction:

| BA lag | BTC AUC | BTC WR |
|---|---|---|
| 4 bars | 0.563 | 57.5% |
| 8 bars | 0.569 | 60.0% |
| **16 bars — the lag Parts XIV–XVII all used** | **0.559** | **55.4%** |
| 32 bars | 0.570 | 59.9% |
| 64 bars | 0.574 | 60.5% |
| *momentum only, BA absent entirely* | *0.574* | *60.8%* |

There is no monotone structure — the spread is noise around a flat line, which
is what a feature carrying no signal looks like. Two details stand out. The
16-bar window every prior part was built on happens to be the **worst** of the
five tested. And performance peaks at lag 64 at exactly the momentum-only
numbers: at that window BA is slow enough that ridge shrinks it out, and the
2-feature model converges on the 1-feature model. The best the BA model can do
is stop using BA.

## Why Part XVII got this wrong — measured, not assumed

Part XVII's BA claim rested on a **shuffle** test against **frozen** weights:
shuffle each feature across time, see which one moves the output most. BA moved
it most (−0.040 AUC vs momentum's −0.026) and carried the largest fitted
weight, so it was reported as the biggest lever. BA was never removal-tested
there; the removal table covered only momentum, volatility, volume, and the
nonlinear interaction. Part A is the first test that actually asks whether BA
*helps*, and it answers the opposite way.

Part C explains the mechanism. `BA` differences `BREADTH_M`, a
**dollar-volume-weighted** average of every coin's momentum — and in the 6-coin
universe **BTC and ETH are 89.5% of that weight**. "Market-wide breadth" was
mega-cap momentum wearing a different name. Six recompositions of the breadth
average, each refit and tested on BTC/ETH:

| breadth composition | BTC AUC | BTC WR | ETH AUC | ETH WR |
|---|---|---|---|---|
| dollar-vol wtd, all coins (reference) | 0.559 | 55.4% | 0.550 | 55.2% |
| dollar-vol wtd, excluding the test coin | 0.557 | 54.5% | 0.546 | 55.3% |
| equal wtd, all coins | 0.555 | 54.1% | 0.549 | 54.3% |
| equal wtd, excluding the test coin | 0.554 | 55.1% | 0.548 | 54.9% |
| BTC+ETH only (pure mega-cap momentum) | 0.559 | 56.9% | 0.550 | 55.1% |
| alts only (BTC+ETH removed entirely) | 0.554 | 57.6% | 0.547 | 56.3% |

Every composition lands in AUC 0.554–0.559, and every one is worse than
dropping BA altogether (0.574). Note that **BTC+ETH-only breadth reproduces the
reference exactly** (0.559 both), which is what an 89.5% weight share predicts.
BA's composition is irrelevant because BA is inert.

`research/cme-x5-collinearity.js` measures the cause directly —
corr(M_COH, BA) per coin over 39,755 rows each:

| ETH | BTC | SOL | LINK | DOGE | AVAX |
|---|---|---|---|---|---|
| 0.577 | 0.560 | 0.515 | 0.511 | 0.495 | 0.470 |

BA is most collinear with own-momentum **precisely for the two coins that
dominate the breadth average** — as the weight share predicts. Ridge splits
weight across a collinear pair, which makes the output highly sensitive to each
of them individually while neither carries information the other lacks. That
produces exactly what was observed: a large shuffle effect alongside zero
removal cost.

> **Standing methodological rule for this project, from here on.** Shuffle
> sensitivity against frozen weights measures *leverage on the output*, not
> *contribution to accuracy*. The two come apart whenever features are
> collinear — which, in a hand-built feature set full of overlapping market
> aggregates, is the normal case rather than the exception. No feature-importance
> claim stands without a refit-and-remove test behind it.

## Part B — the edge is a liquidity gradient, not a BTC/ETH identity

The confound in every prior part: with 6 coins, BTC and ETH are *always* the
two most liquid, so "coin identity" and "liquidity rank" are perfectly
collinear and cannot be told apart. This widens the universe to 16 coins,
ranks them by median dollar volume per bar, and runs leave-one-asset-out —
train on the other 15, test on the held-out coin. 20,089 aligned bars
(2026-02-15 → 2026-09-12); the inner join is bounded by the shortest series,
so absolute numbers are **not** comparable to Part A's 418-day window.

Momentum-only (Part A's best model):

| rank | coin | AUC | WR | expR | p |
|---|---|---|---|---|---|
| 1 | ETH | 0.562 | 57.6% | +0.112 | 0.003 |
| 2 | BTC | 0.585 | 61.7% | +0.194 | 0.003 |
| 3 | SOL | 0.544 | 54.4% | +0.048 | 0.043 |
| 4 | DOGE | 0.542 | 52.4% | +0.008 | 0.292 |
| 5 | XRP | 0.538 | 53.7% | +0.034 | 0.136 |
| 6 | ADA | 0.528 | 53.1% | +0.023 | 0.066 |
| 7 | LINK | 0.530 | 55.0% | +0.059 | 0.023 |
| 8 | NEAR | 0.503 | 50.9% | −0.021 | 0.601 |
| 9 | BCH | 0.514 | 47.9% | −0.081 | 0.306 |
| 10 | AVAX | 0.536 | 55.1% | +0.062 | 0.013 |
| 11 | LTC | 0.525 | 52.2% | +0.003 | 0.462 |
| 12 | DOT | 0.518 | 53.1% | +0.021 | 0.150 |
| 13 | ARB | 0.515 | 50.1% | −0.037 | 0.934 |
| 14 | OP | 0.520 | 53.5% | +0.030 | 0.013 |
| 15 | APT | 0.513 | 50.1% | −0.037 | 0.904 |
| 16 | ATOM | 0.505 | 50.2% | −0.036 | 0.927 |

**Spearman(liquidity rank, AUC) = −0.815** (momentum-only), −0.762 under
momentum+BA. Rank-vs-win-rate −0.644. Top-2 mean AUC 0.5733 against 0.5237 for
the other fourteen; top-5 0.5541 against bottom-5 0.5142. With n=16 the p=0.001
critical value is ≈0.72, so this clears it comfortably, and it is *stronger*
under the better model rather than weaker.

Read plainly: **BTC and ETH are the top of a smooth gradient, not outliers off
it.** SOL, DOGE, XRP, LINK and AVAX all sit above chance in the middle; the
bottom of the ranking sits at 0.505–0.520. That is the shape of a large-cap
effect with an asset-normalized representation, not a two-coin curiosity.

## Part D — how much of that gradient is the barrier, not the market?

Part B's `topN` column undercuts a naive reading. Every coin is scored against
the **same absolute ±0.5%** barrier over 2 bars, but illiquid coins are more
volatile and resolve that race far more often — BTC produced 368 resolved
top-decile bars against OP's 1,342. For BTC the label therefore only exists
when a ≥0.5% move happened inside 30 minutes: a heavily selected, trending
subset that a momentum feature is naturally good at calling. For OP almost
everything resolves and the problem is near-unconditional.

Measured, the three are collinear: Spearman(liquidity rank, volatility) =
**+0.638**, Spearman(liquidity rank, resolution rate) = **+0.626**. So the
gradient could be measuring how selective an absolute target is on a
low-volatility asset rather than anything about large-cap markets.

Part D equalizes label difficulty: each coin gets its own barrier, found by
bisection so all coins resolve at the same rate (39.9%, the median under the
fixed barrier, so labels are re-scaled rather than made uniformly harder),
calibrated on the **first 30% of bars only** and then held fixed, so the
barrier never sees the evaluation window. BTC's barrier tightens to ±0.382%
and its resolution rate rises 18.0% → 28.9%; OP's widens to ±0.750% and falls
60.3% → 36.6%.

| | momentum only | momentum + BA |
|---|---|---|
| Spearman(rank, AUC), fixed ±0.5% barrier | −0.815 | −0.762 |
| **Spearman(rank, AUC), equalized labels** | **−0.665** | **−0.650** |
| top-2 vs rest AUC gap, fixed barrier | 0.0496 | 0.0505 |
| **top-2 vs rest AUC gap, equalized** | **0.0332** | **0.0341** |

**The gradient attenuates but survives.** Roughly a third of the top-2-vs-rest
gap was barrier scaling; the rest is not. BTC's AUC falls 0.585 → 0.561 once
its labels stop being so heavily selected, while ETH is nearly unchanged
(0.562 → 0.563) — so the single most impressive number in Part B was partly an
artifact, and the effect is real but smaller than first measured.

A partial rank correlation nets volatility out entirely:

- Spearman(rank, AUC) = −0.665
- Spearman(rank, volatility) = +0.638
- Spearman(volatility, AUC) = −0.359
- **partial Spearman(rank, AUC | volatility) = −0.606**

Liquidity still predicts edge strength after volatility is removed, and does so
better than volatility predicts it directly. The gradient is a liquidity
gradient, not a volatility gradient wearing liquidity's clothes.

**Honest limit on the equalization.** Bisection compressed the resolution-rate
*spread* from 42.8pp (18.0–60.8%) to 17.3pp (28.9–46.1%), but did not change
the rank *ordering* — Spearman(rank, resolution rate) is +0.626 before and
+0.626 after. Because the calibration window is the first 30% and the barrier
is then frozen, out-of-window drift keeps a monotone residual. So Part D
removes most of the magnitude of the confound but not all of its ordering, and
−0.665 should be read as an upper bound on the surviving gradient rather than a
fully cleaned estimate.

## Conclusion

Three things changed in this study, and only one of them is good news.

1. **Part XVII's breadth-acceleration finding does not survive.** BA is nowhere
   near the dominant lever it was reported to be. The finding was an artifact of
   reading shuffle sensitivity as importance on a feature that is 0.56–0.58
   collinear with momentum by construction.
   *Precision about how far this goes:* in Part A's setting (6 coins, 418 days,
   fixed ±0.5% barrier) BA is actively harmful — momentum-only beats momentum+BA
   0.574/60.8% to 0.559/55.4%. In Part D's setting (16 coins, equalized labels)
   the two are near-identical, with BA marginally *ahead* (top-2 AUC 0.5642 vs
   0.5619; 10 of 16 coins clear breakeven vs 9). So "BA is not the engine" is
   robust everywhere; "BA actively hurts" holds in Part A's window and not in
   Part D's, and should not be stated unconditionally.
2. **The whole cross-asset apparatus built across Parts XIV–XVII is
   net-negative.** One plain momentum feature outperforms all fifteen. Fourteen
   features of breadth, synchronization, entropy, liquidity and dispersion
   subtract from a model that a single momentum term carries.
3. **The BTC/ETH-specificity from Part XVII is better described as a liquidity
   gradient.** Across 16 coins, edge strength is a strong monotone function of
   liquidity rank (−0.815 raw, −0.665 with label difficulty equalized, −0.606
   net of volatility). BTC and ETH are the top of that gradient, not outliers
   off it.

The user's standing instruction — *do not force universality* — is what makes
(3) reportable. The honest label is not "a universal formula" and no longer
"a BTC/ETH-specific effect" either: it is **a short-horizon momentum
continuation effect whose strength scales with venue liquidity, strongest in
the two most liquid assets and decaying smoothly to nothing by the bottom of a
16-coin liquidity ranking.** That *is* an asset-normalized representation — the
production rule it implies is "trade the top-k by liquidity," which survives
the universe changing next cycle, rather than "trade BTC and ETH because they
are BTC and ETH."

What it does not imply is that the thing is large. Under equalized labels at
4bps, only the top three coins clear +0.05R on top-decile selections, and most
of the ranking sits at or below zero expectancy. Part XVI's verdict still
stands where it applies: nothing here survives contact with a materially worse
cost assumption.

## What follows from this

- **Any future model built from this research track should start at momentum
  and justify every addition against it.** Every result in this part points the
  same way: one plain momentum feature is the baseline that fourteen hand-built
  market aggregates failed to beat.
  *(To be explicit about scope: this implies no change to the live engine. The
  CME-X feature set was never wired into it — `app.js` and `masis-engine.js`
  contain no breadth, coherence, synchronization or entropy terms. Parts
  XIV–XVIII are an offline research track, and nothing in them has yet earned
  a place in live trading.)*
- **Re-audit Parts XIV–XVI for the same shuffle/collinearity error.** Part XV's
  interaction terms and Part XVI's 2×2 both leaned on importance arguments that
  were never refit-and-remove tested. The label-pooling bug already showed this
  project's studies share failure modes across parts.
- **Test the liquidity gradient forward, not just cross-sectionally.** Rank is
  measured over the same window the edge is measured in. The rule "trade the
  top-k by liquidity" is only useful if rank measured in one period predicts
  edge in the *next* — that is a different and harder test than the one run here.
- **The positive-momentum-only asymmetry from Part XVII is still untested** and
  is now the most interesting surviving lead, since momentum is the only feature
  left standing.

# Part XIX — CME-X6: can the rate be raised without inventing another formula?

The user asked whether the win rate could be raised by inventing more formulas
rather than depending on one. Parts XIV–XVIII answer the formula half: no —
five successive formula families, and fifteen hand-built features lost to one.
This part tests the other half, the two levers that need no new formula at all,
on the momentum-only model Part XVIII left standing. 16 coins, leave-one-asset-
out, 4bps, symmetric ±0.5% barrier, **breakeven 52.0%**.

```bash
node research/cme-x6-selectivity.js
```

## Lever 1 — selectivity: a real but small and quickly-exhausted gain

Every number in Parts XIV–XVIII came from the top **decile** of scored bars.
Nothing forces that choice.

| top % | BTC WR (n) | ETH WR (n) | top-5 coins WR | top-5 expR | all-16 WR | #>breakeven |
|---|---|---|---|---|---|---|
| 10% | 61.7% (368) | 57.6% (618) | 56.0% | +0.079 | 53.2% | 11/16 |
| **5%** | 61.2% (183) | 60.2% (299) | **57.3%** | **+0.106** | 53.6% | 11/16 |
| 2% | 53.4% (73) | 62.7% (118) | 57.3% | +0.105 | 54.6% | 11/16 |
| 1% | 38.1% (42) | 71.2% (66) | 55.7% | +0.075 | 54.5% | 9/16 |

Tightening from the top decile to the top 5% raises top-5 expectancy from
+0.079R to **+0.106R**, a ~34% gain for free. Past that it flattens and then
declines.

The per-coin columns show why the tail of the table cannot be taken at face
value. BTC falls to 38.1% while ETH rises to 71.2% — at n=42 and n=66 over
seven months. With n≈42 the standard error on a win rate is ≈7.7pp, so neither
number is distinguishable from the other, let alone from chance. That is the
threshold selecting noise, exactly the failure mode the sweep was built to
expose. **The usable finding is 10% → 5%, and no further.**

## Lever 2 — the positive-momentum gate: no gain, and the reason matters

Part XVII found the edge lives entirely in positive-momentum states (BTC 62.0%
WR, against 47.8% and AUC 0.502 when momentum is negative) and recommended
building around a hard momentum-sign gate. That had never been applied. Applied
here at both training and decision time:

| top % | top-5 WR | top-5 expR | all-16 WR | #>breakeven | vs ungated |
|---|---|---|---|---|---|
| 10% | 56.0% | +0.079 | 52.6% | 9/16 | identical expR, **fewer coins clear** |
| 5% | 55.7% | +0.075 | 52.5% | 9/16 | worse (+0.075 vs +0.106) |
| 2% | 52.8% | +0.017 | 51.4% | 7/16 | much worse |
| 1% | 55.4% | +0.068 | 50.8% | 3/11 | much worse |

**The gate does nothing at best and hurts everywhere else**, while halving the
trade count (BTC 368 → 178 at the top decile).

The reason is the useful part. **You cannot gate on the variable you are already
ranking by.** The model is momentum-only; its score *is* momentum. High scores
are already positive-momentum states, so the gate removes rows the ranking was
never going to select anyway, then refits on a truncated range of the one
variable carrying the signal. Part XVII's regime split was not describing an
independent filter — it was describing what the score already does.

This is the same lesson as Part XVIII's BA result in different clothing. There,
a feature 0.56–0.58 collinear with momentum looked important under a shuffle
test and contributed nothing. Here, a gate perfectly redundant with the ranking
looked like a free filter and contributed nothing. **Both are cases of adding
machinery that carries no information the model did not already have.**

## Conclusion — the answer to "more formulas?"

No, and this part explains why in a way the formula studies alone could not.
The binding constraint is not the number of equations, it is the amount of
independent information available to them. Every formula in Parts XIV–XIX reads
the same input — 15m OHLCV on the same coins over the same window — so each new
one is largely a restatement of the last. That is why fifteen features lost to
one, why a 0.57-collinear feature added nothing, and why a redundant gate added
nothing. More formulas over one data source multiply the multiple-testing
burden without adding information, and `lib-stats.js`'s FDR machinery exists to
punish exactly that, not to enable it.

What actually moved the number in this part was **spending the existing signal
better** (10% → 5% selectivity, +34% expectancy) — and that lever is now spent.

The next real gain has to come from **new information, not new arithmetic**:
order-book depth and imbalance, funding rates, open interest, taker aggressor
side, cross-venue basis. Short-horizon crypto edge is a microstructure
phenomenon, and this project has never fed its models a single microstructure
input. That is the gap, and it is a data-acquisition problem rather than a
formula-design one.

# Part XX — Stop geometry: the losses were the stop distance, not the signal

This part began from a live observation rather than a study. A UNI position was
entered at 6.367, moved favourably, retraced, and was closed by the position
manager before it resolved. That is not an anecdote — it is the modal failure of
this system, and instrumenting for it turned up the largest validated
improvement the project has produced.

```bash
node backtest/run-backtest.js --symbols ... --exec maker --makerOffset 20 \
  --makerTimeout 20 --stopMult 2.5 --invalMult 2.0
node backtest/loss-forensics.js
```

## The measurement that was missing

Every prior part reported how trades ended and none reported what they offered
along the way, which makes the central question about a loss unanswerable: was
the entry wrong, or was the entry right and the exit gave it back? Those have
opposite fixes. Max favourable and adverse excursion now land on every trade
record, along with the average bar range in R while the position is open.

On 98 trades across 12 symbols, the deployed configuration:

| | value |
|---|---|
| dead on arrival (never reached +0.25R) | 29.9% of losses |
| **reached +0.5R, then lost** | **67.2% of losses** |
| winners that ever traded to −0.5R | 6.5% |
| **median bar range while in trade** | **1.39R** |
| typical bar exceeds 1R | 54 of 82 trades |

**One ordinary bar reached the stop.** A trade had to go right immediately or
die, however good the signal was. Two thirds of losses were trades already in
profit, and only 6.5% of winners ever survived an adverse move — winners were
not trades that endured drawdown, they were trades that never faced any. That
is not an edge surviving noise; it is a coin flip on the next bar.

The same geometry broke the position manager. Its structural exit closed at the
invalidation level, but at that stop distance a routine retrace *is* a
structural break: the guardian won 9.1% of the time with nine of its ten losses
already in profit — the worst exit path in the system, and the one that closed
the UNI trade.

## The fix, and why it is risk-neutral

Stops and invalidation levels are set at multiples of what the playbook
proposes. This costs nothing in risk because position size is derived from the
stop distance: a wider stop simply buys less, and every trade still risks 0.5%
of equity. What it costs is that targets are absolute prices, so each win is
worth proportionally fewer R — which is exactly the trade-off the sweep prices.

98 trades, 12 symbols, 42 days:

| stop × inval | WR | expR | total R | maxDD | P(loss < −2R) |
|---|---|---|---|---|---|
| 1.0 × 1.0 | 31.6% | −0.179 | −17.55 | 5.5% | 8.2% |
| 1.0 × 2.0 | 38.8% | −0.034 | −3.29 | 5.5% | 7.1% |
| 1.5 × 1.0 | 45.9% | −0.034 | −3.29 | 4.3% | 3.1% |
| 1.5 × 2.0 | 49.0% | +0.032 | +3.15 | 4.5% | 2.0% |
| 2.0 × 2.0 | 51.0% | +0.028 | +2.70 | 3.9% | 2.0% |
| **2.5 × 2.0** | **53.1%** | **+0.063** | **+6.21** | **3.4%** | **2.0%** |
| 3.0 × 2.0 | 54.1% | +0.055 | +5.38 | 3.4% | 2.0% |

Neither lever worked alone at first — stop-only and invalidation-only both
landed on exactly −0.034R, because widening one simply handed the trade to the
other. They were two doors on the same trap.

**Risk improved alongside returns rather than against them**, which is unusual
enough to deserve suspicion. Max drawdown fell, average loss shrank from −1.109R
to −0.763R, trades losing more than 2R fell from 8.2% to 2.0%, and the worst
single trade held near −2.5R at every width, so the tail never grew. The
mechanism is plain: with a stop inside the noise every gap and guardian exit
overshoots into a large fraction of R. The tight stop was not only causing more
losses, it was causing bigger ones.

2.5× is a plateau, not a peak — 2.0× through 3.0× land within noise of one
another — so nothing here is finely tuned.

## Validation on 418 days, and a failure to reproduce Part IX

The sweep above sits on one 42-day window whose baseline was losing, so it
could not distinguish "fixes bad geometry" from "rescues one bad window". Re-run
on 120,099 5m bars per symbol (2025-07-22 → 2026-09-12), Part IX's four symbols:

| | baseline | **2.5 × 2.0** |
|---|---|---|
| n | 233 | 236 |
| win rate | 38.2% | **55.5%** |
| expectancy | −0.195R | **+0.104R** |
| total R | −45.38R | **+24.55R** |
| profit factor | 0.76 | **1.28** |
| max drawdown | **15.5%** | **5.6%** |

A third width confirms the plateau survives the long window too: 3.0 × 2.0
returns n=236, 56.4%, **+0.108R**, +25.38R, PF 1.34, max drawdown 5.2% — within
noise of 2.5 × 2.0, and the two swap order between the 42-day and 418-day
windows (2.5 wins the short one, 3.0 the long one by +0.004R). Nothing turns on
the exact multiplier anywhere in the 2.0–3.0 band, which is the useful finding:
there is no parameter here to tune or to overfit.

The fix holds on 2.4× the sample and 10× the window, in the same direction and
at similar magnitude. The drawdown result is arguably the more important half:
15.5% → 5.6% at identical per-trade risk means the old geometry was not merely
unprofitable, its losses clustered.

**Part IX does not reproduce, and that has to be recorded rather than left
standing.** Its documented result for this exact configuration — swarm off,
20bps maker, four symbols, 418 days — is n=300, 47.7% win rate, +0.155R, +46.5R,
PF 1.38. The run above, same configuration and window length, returns n=233,
38.2%, −0.195R, −45.38R, PF 0.76: the opposite sign.

The new stop code is not the cause (at `stopMult 1.0` it takes an identity
branch). The differing trade count points at genuinely different data: Part IX
ran earlier, so its 418 days ended earlier and covered a different regime, and
OKX is now the source after the Bybit geo-block. Part IX also used `--seed 1`,
though that seeds analyst weights and should not affect swarm-off rows.

Until that is explained, **Part IX's +0.155R and +0.288R should not be used as a
baseline or quoted as this project's ceiling**, and the recommendation derived
from it — raise the maker offset to 30bps — is withdrawn pending a reproduction
on current data. It may well still be correct; it is simply no longer supported.

## Where this leaves the project

**+0.104R at 55.5% over 236 trades and 418 days, PF 1.28, max drawdown 5.6%, is
the best reproducible result this project has**, and it comes from stop
placement rather than from any signal, formula, model or gate. Parts XIV–XIX
searched for a better prediction across five formula families, sixteen coins,
per-coin selection, LLM gating and an analyst swarm, and none of it moved
expectancy the way changing one distance did.

The standing lesson, consistent with Part V's execution finding: on this system,
**how a trade is entered and exited has repeatedly mattered more than what it
predicts.** Two of the three largest effects ever measured here — patient maker
entry and stop geometry — are mechanical rather than predictive.

Outstanding: the sample is 236 trades in one regime; live behaviour will differ
from a backtest that assumes the widened stop fills where modelled; and the
concurrency limits raised alongside this change are unvalidated, because the
backtest holds one position at a time by construction.

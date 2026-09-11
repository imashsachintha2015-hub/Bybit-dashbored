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

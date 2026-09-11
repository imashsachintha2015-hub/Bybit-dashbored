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

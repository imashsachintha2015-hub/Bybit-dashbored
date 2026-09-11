# MASIS V3 — Bybit Live Trading Terminal

A multi-timeframe confluence trading engine, risk governor and backtest harness,
rebuilt from the V2 dashboard. This document is the reverse-engineering of what
V2 was doing, the specific reasons it lost money and burned model credit, and
what replaced each part.

---

## 1. What the measurement says

V2's decision and exit logic was reconstructed from the shipped source and run
over ~42 days of real 15-minute bars on BTC, ETH and SOL, with taker fees and
slippage charged on every fill. V3 was run over the identical bars with the
identical cost model and fill rules.

| | V2 (reconstruction) | V3 |
|---|---|---|
| trades | 1,051 | 38 |
| win rate | 39.6% | 39.5% |
| average win | 0.73R | 1.06R |
| average loss | 0.87R | 0.75R |
| **expectancy per trade** | **−0.335R** | **−0.015R** |
| profit factor | 0.46 | 0.97 |
| total R | −352 | −0.6 |
| return on $1,000 | −49% / −54% / −70% per symbol | −0.6% |

Read those two columns carefully, because they say two different kinds of thing.

**V2's failure is established, not suggested.** Over 1,051 trades the standard
error on expectancy is about 0.03R, so −0.335R sits roughly eleven standard
errors below zero. That is not variance. V2 was structurally losing money, and
it would have kept losing money for as long as it ran.

**V3's improvement is real; V3's profitability is not established.** The
expectancy gap of +0.32R per trade is large and traceable to specific fixed
defects. But V3's own expectancy of −0.015R comes from 38 trades, where the
standard error is around 0.15R. That interval comfortably contains both
modestly profitable and modestly unprofitable. The honest statement is: **the
bleeding is stopped and the known defects are fixed; a positive edge is not yet
demonstrated and needs several hundred more trades before anyone should believe
one exists.**

Anyone promising you a high win rate from a code change is guessing. What code
can do is remove the structural reasons a system loses. That is what this is.

### Costs are not a footnote

Running the same V3 logic on 5-minute structure instead of 15-minute produces a
negative expectancy at **every** parameter setting tried (−0.304R to −0.408R
across a six-cell sweep). The reason is arithmetic, not tuning. A round trip on
Bybit linear perps costs roughly 0.15% of notional once taker fees and slippage
are counted both ways. Against a stop 0.3% away, that is half of every 1R the
system earns. Against a stop 1.2% away, it is an eighth. Faster trading with
market orders does not have a parameter that fixes this.

That single finding is why V3 analyses 5m/15m/1h rather than the 1m series V2
used.

---

## 2. Why V2 lost money

### 2.1 The exit that cut every position the moment it turned red

This is the one you noticed, and it was the largest single defect.

`renderPositionGuardian()` ran on a 3-second timer and market-closed a position
whenever **any** of three conditions held: the live decision for that symbol had
flipped to the opposite side, the whale tracker's dominant side had flipped, or
the trap guard had tripped.

None of those are stable on a 3-second cadence. The decision was recomputed
every 3 seconds from 1-minute data and oscillated continuously; the whale side
flipped on a single $50k print inside a 5-minute window. So in practice: enter,
hit the ordinary retrace that follows almost every entry, get closed at market
for a small loss plus fees, repeat.

**In the reconstruction, 23.7% of all V2 exits were this cut** — 249 of 1,051
trades. And because the check never looked at P&L, it closed winners on exactly
the same trigger. A position at +2R could be flattened by a three-second blip in
whale side.

**Replaced by** `agents/position-manager.js`. A position now exits only on a
named structural event:

| Exit | Trigger |
|---|---|
| `STOP` | The broker-side stop attached at entry. The hard boundary. |
| `TARGET` | Planned scale-outs at TP1 / TP2 / TP3. |
| `STRUCTURE_BROKEN` | A **confirmed candle closes** beyond the invalidation level the setup was built on — not a tick through it. |
| `THESIS_FLIP` | A grade-A opposing setup persisting across 4 consecutive evaluations **and** the position is underwater. |
| `TIME_STOP` | The idea has had its bars and gone nowhere. Releasing dead risk is a good cut. |
| `EMERGENCY` | Data invalid, spread blowout, feed halt. |

Plus two rules that protect winners rather than culling them: a grace period
after entry during which only the hard stop applies, and a stop that ratchets to
break-even once TP1 is banked and then trails. Once the stop is at break-even
the remainder is a free option — its worst case is zero — so it is never retired
on a clock and never closed on an opinion.

### 2.2 A target structure that could not win

V2 set its stop at `max(1.2 × ATR, 0.3% of price)` and attached `takeProfit[0]`
broker-side **at full size**. That target sat at 1.1R.

So every winner was capped at 1.1R and every loser was a full 1.0R. The observed
figures were worse still: average win 0.73R against average loss 0.87R, which
needs a **54.4% win rate just to break even**. V2 achieved 39.6%.

The three-level take-profit ladder in the V2 source never ran, because the
full-size broker TP closed the whole position at TP1 before TP2 was reachable.

**Replaced by** stops placed beyond a structural invalidation level with an ATR
buffer, targets at 1R / 2R / 3.5R with TP2 pulled in when an opposing level sits
in front of it, a hard floor of 1.6 reward:risk to TP2, and a rejection of any
setup whose first target sits inside the fee-plus-spread band. Only the stop is
attached broker-side; the ladder is managed client-side so it can actually run.

### 2.3 Direction decided by counting correlated bullets

V2 called a BUY when at least three "bullish observations" existed and at most
one bearish one. The observations included `price > VWAP`, `EMA9 > EMA21` and
`delta > 0`.

Those are three restatements of "price went up recently". The count reached
three the moment price ticked up, in any tape, with no edge attached — and
nothing in the rule referenced *where* price sat relative to structure. Buying
strength at the top of a range and buying strength off a defended low are
opposite trades with opposite outcomes, and V2 could not tell them apart.

**Replaced by** four named playbooks in `agents/playbooks.js`, each requiring a
specific structural context and each carrying an invalidation level you can
point at on a chart: `TREND_PULLBACK`, `SWEEP_RECLAIM`, `RANGE_FADE`,
`BREAKOUT_RETEST`. Quality is scored from weighted, deliberately non-redundant
components and graded A+ through D. Only A and above are traded automatically.

### 2.4 A trust score that was a constant

V2's trust score was assembled from four hard-coded numbers
(`evidenceScore = 85`, `riskScore = 85`, `execScore = 90/70`, `histScore = 70`).
An authorised call always scored about 83. The `MIN_AUTO_TRADE_TRUST = 65`
filter downstream could therefore never reject anything — it was decoration.

**Replaced by** a score computed from the actual evidence, which is the same
number the backtest reports, so the grades are falsifiable.

### 2.5 Structure read from unconfirmed bars

Every indicator was computed including the forming candle, so the EMA stack and
market-state classification flickered within each bar.

Worse, the break-of-structure test compared live price against
`Math.max(...highs.slice(-20))` — a window that **includes the current bar's own
high**. Since a bar's high is always ≥ its last price, that test could
essentially only pass on the exact tick that printed a new high. The
`trend_expansion` classification that depended on it was crippled.

**Replaced by** structure derived from confirmed candles only, using fractal
swing pivots that require lower highs on both sides. Live price is used solely
for entry distance and risk geometry.

### 2.6 One timeframe

Everything was read from the 1-minute series, and the chart's interval buttons
silently changed it — so adjusting the chart changed the trading logic.

**Replaced by** a fixed 5m / 15m / 1h analysis set, subscribed independently of
whatever the chart displays. A higher-timeframe regime gate decides whether the
tape is tradable at all before the lower timeframe may propose anything. In the
backtest that gate stands the system down about 60% of the time, which is the
point.

### 2.7 A risk supervisor that was a UI card

`DIV-08 Risk Supervisor` rendered "Max Loss: 3.0% / Drawdown: 0.0%". There was
no code behind it. No daily loss limit, no consecutive-loss breaker, no cap on
concurrent positions, no cooldown after a stop-out.

And position size was a flat dollar margin per trade. A fixed $50 behind a
0.15%-away stop and behind a 1.2%-away stop are different risks by a factor of
eight, wearing the same label — which also means **no win rate computed across
V2's trades meant anything**, since the trades were not comparable units.

**Replaced by** `agents/risk-governor.js`, enforced before any order is sent:
size derived from risk so every trade risks the same fraction of equity, daily
loss limit, consecutive-loss circuit breaker, per-symbol cooldown after a trade,
concurrent-position cap, and a correlation cap (BTC/ETH/SOL open together is one
bet, not three).

### 2.8 Fabricated performance statistics

`trade_stats.json` shipped seeded with 14 wins, 6 losses, $3,482 of profit and
four invented trades, and `index.html` hard-coded the same figures into the
markup. The dashboard displayed a 70% win rate and a 3.1 profit factor before
the system had placed a single order.

Separately, `/api/performance` **added** Bybit's closed-PnL list to the locally
recorded stats — but the client also posted every close to `/api/trades/record`,
so every trade was counted twice, once from each source.

**Replaced by** zeroed counters, Bybit closed-PnL as the single source of truth
for money, local records supplying only setup and exit-reason metadata matched
by symbol/side/time, and R-multiple expectancy reported beside the win rate with
its sample size attached.

### 2.9 Live API keys in source

`server.py` carried working Bybit, DeepSeek, Benzinga and CoinGecko keys as
string literals.

**Rotate all four now.** They were in a file that was zipped and shared; treat
them as compromised. A Bybit key with trade permissions is enough to move money
the moment it is pointed at the live endpoint. Credentials now come from the
environment — see `.env.example` — and the server refuses to start without the
required ones rather than falling back to a default.

---

## 3. Why DeepSeek credit was burning

Three uncapped call sites, none gated on whether the answer could change a
decision:

1. **Every decision change, per symbol.** The decision was recomputed every 3
   seconds and oscillated `WAIT → BUY → WAIT → BUY`; each re-entry fired a fresh
   700-token synthesis, across five symbols in parallel.
2. **A blind 60-second refresh** of the focused symbol — about 1,440 calls a day
   that nobody asked for.
3. **The news sentinel re-scored all 15 headlines every 90 seconds**, re-sending
   headlines it had already scored — about 960 calls a day, nearly all duplicates.

That is on the order of 2,400 calls a day. And the output was prose: a
five-section "institutional executive brief" that no code parsed. **A 700-token
essay no code reads cannot change an outcome.** It was pure cost.

### What replaced it

The model is now consulted at the one point where its answer can change
something: a fully-formed candidate that has already passed every local gate. It
returns a compact structured verdict — `CONFIRM` / `DOWNGRADE` / `VETO` with a
one-sentence rationale, at 150 max_tokens — and the engine's gate consumes it.

`agents/deepseek-governor.js` refuses everything else locally:

- grade A or better only;
- a 10-minute per-symbol cooldown;
- a state fingerprint, so re-firing on the same setup is served from cache
  rather than paid for again;
- a hard daily ceiling (default 120), after which the system runs on local logic
  — which is the design, not a degradation.

Headline scoring is deduplicated: headlines are immutable once published, so
each is scored exactly once, and a feed where nothing changed costs zero calls.

`GET /api/llm/status` reports calls made, calls refused, cache hits and spend by
caller, so the burn is visible rather than inferred from a bill.

**Expected reduction: roughly 2,400 calls/day to under 120, with the token count
per call cut by about 80%** — and unlike before, the calls that remain are wired
to the decision.

---

## 4. Architecture

```
index.html
├── agents/indicators.js        pure math — EMA, Wilder ATR, VWAP, ADX,
│                               Kaufman efficiency ratio, fractal swings
├── agents/regime-agent.js      DIV-03  is this tape tradable, and which way
├── agents/flow-agent.js        DIV-02/04 CVD, absorption, flow bursts, spoof guard
├── agents/playbooks.js         DIV-05  the four named setups + scoring + geometry
├── agents/risk-governor.js     DIV-08  sizing, loss limits, breakers, cooldowns
├── agents/position-manager.js  DIV-12  structural exits, BE ratchet, trailing
├── agents/deepseek-governor.js DIV-07  model call gating and budget
├── masis-engine.js             orchestrator — per symbol, multi-timeframe
├── whale-agent.js              DIV-11  adaptive large-print detection
├── bybit-ws.js                 multi-timeframe subscriptions
└── app.js                      UI + execution wiring

server.py                       Bybit proxy, news, macro, supervisor, budget
backtest/                       walk-forward harness + V2 reconstruction
```

Every agent module loads both in the browser and in Node, so **the backtest
exercises the exact code the browser runs** rather than a reimplementation of it.

### Agents, and what each can actually do

An agent can only ever *remove* a trade. None of them can create one. A setup
exists because a playbook pattern is present, or it does not exist at all — the
context agents (news, macro, whale, spoof) act as vetoes on something that
already stands on its own. This is deliberate: V2's failure mode was
accumulating weak, correlated confirmations until a threshold was crossed.

---

## 5. Running it

```bash
cp .env.example .env          # fill in rotated keys
export $(grep -v '^#' .env | xargs)
python3 server.py             # http://localhost:8080
```

### Backtesting

```bash
node backtest/fetch-klines.js BTCUSDT,ETHUSDT,SOLUSDT 1,5,15 12000
node backtest/run-backtest.js --mode swing --equity 1000 --json results.json
node backtest/run-backtest.js --mode fast    # the fee-destroyed configuration
```

The harness commits to: no look-ahead (decisions at bar *i* use bars 0..*i*,
fills at bar *i+1*'s open); pessimistic intrabar ordering (if a 1-minute bar
contains both stop and target, the stop is taken); real costs on every fill
including partial scale-outs; and R reported **net** of every fee paid.

Two caveats stated plainly:

- **Data source.** Bybit's CDN geo-blocks the environment this was built in, so
  the cached data came from OKX USDT-margined perpetuals — same instrument type,
  highly correlated pricing. The fetcher tries Bybit first and records which
  venue it used; on your machine it will likely use Bybit directly.
- **No historical tape.** Klines carry no trade-by-trade data and no order book,
  so the backtest substitutes candle-derived proxies for taker flow and
  absorption (`backtest/flow-proxy.js`, which documents each proxy). Flow-weighted
  components are noisier there than live, and `RANGE_FADE` in particular is
  under-represented. Treat the backtest as a test of the **structure and risk**
  logic, which is where V2's losses actually came from — not as a forecast of
  live win rate.

### On the parameter values

Exit parameters were set from the **middle of the stable region** of a sensitivity
sweep, not its peak. Across `timeStopBars` of 12/18/30 the expectancy moved by
less than 0.1R, which on 38 trades is noise. Picking the best cell of that sweep
would be fitting to the sample and would not survive contact with new data.

---

## 6. What to do next

In priority order, with the reasoning:

1. **Rotate the four exposed API keys.** Before anything else.
2. **Paper-trade to 200+ trades before believing any win rate**, including the
   ones in this document. Expectancy in R is the number to watch, not win rate —
   a 39% win rate with 2R winners beats a 60% win rate with 0.5R winners.
3. **Try maker (limit) entries.** Round-trip cost is the single largest drag
   identified here. Bybit's maker fee is roughly a third of taker, and the
   `SWEEP_RECLAIM` and `BREAKOUT_RETEST` playbooks both have a natural resting
   price. This is likely worth more than any further signal work.
4. **Widen the sample before tuning anything.** Six weeks on three symbols is
   not enough to distinguish a real parameter effect from noise. Fetch a year
   and re-run before changing a threshold.
5. **Resist adding playbooks.** The failure mode being escaped is exactly the
   one that more, weaker signals recreate.

---

## 7. The analyst swarm (V3.1)

V3's "agents" were deterministic rule modules with grand names. This adds a real
panel of specialists that read the market the way a desk does, argue about it,
and can veto each other.

### The analysts

| Analyst | What it actually reads |
|---|---|
| **Liquidity Cartographer** | Where the stops are. Equal highs/lows, session and previous-day extremes, round numbers, swing clusters — each scored for how much resting liquidity is likely there, how untested it is, and how close. Price is drawn to resting liquidity because that is the only place size can be filled. |
| **Trap Forensics** | Completed trap sequences: inducement → raid → rejection → corroboration. Classifies SFP, failed breakout, liquidity grab and stop hunt. A poke through a level is not a trap; all four legs are required. |
| **Positioning & Derivatives** | Open interest, funding, crowd long/short ratio and measured taker volume. Classifies the OI/price quadrant (new longs / short covering / new shorts / long liquidation), detects squeeze fuel and coiled leverage. |
| **Market Structure** | BOS, CHoCH, order blocks, fair value gaps, premium/discount location. |
| **Volume Profile** | POC, value area, high- and low-volume nodes. Where business actually got done. |
| **Manipulation Forensics** | Spoofing, layering, iceberg signatures (live book), plus momentum ignition, wash-trade and liquidity-vacuum patterns from bars alone. |
| **Session & Time** | Asia/London/NY, killzones, thin-book hours, weekends. Scales confidence; never argues a direction. |

### How they argue

Analysts read the market independently — none sees another's conclusion, so
their agreement carries information. Then `swarm/debate.js` runs four stages:

1. **Weighted vote** — conviction × reliability weight from the meta-learner.
2. **Red team** — the provisional thesis is handed to an adversary that builds
   the counter-case from dissenting evidence and every veto naming this
   direction. A thesis that cannot outweigh its own best counter-argument is
   not traded.
3. **Fatal vetoes** — a small set of objections that cannot be outvoted:
   entering into a dense untapped liquidity shelf, joining crowded and
   well-paid positioning that price is refusing to reward, leaning on a level
   shown to be spoofed, or trading into a fresh trap.
4. **Concerns** — graded objections that shrink conviction without arguing the
   other side. A concern is a reason to trade smaller; an opposing read is a
   reason not to trade.

`swarm/meta-learner.js` tracks how often each analyst agreed with the
profitable direction, bucketed by regime, and reweights accordingly. Weights
are bounded to [0.4, 1.6] and do not move at all below 15 observations.

### What the measurement says — read this part

Trade-level statistics cannot settle whether an analyst is any good; a backtest
window produces a few dozen trades and a few dozen observations cannot separate
skill from luck. So `backtest/evaluate-analysts.js` scores every analyst against
forward returns on **every bar** — thousands of observations each.

Average forward move over 8 × 15m bars, in the direction the analyst pointed
(0.00 = no edge):

| Analyst | n | hit % | avg forward (ATR) | 1st half | 2nd half |
|---|---|---|---|---|---|
| Positioning & Derivatives | 1,692 | 49.0 | **+0.198** | no data | +0.198 |
| Trap Forensics | 1,159 | 54.7 | **+0.142** | +0.145 | +0.139 |
| *Debate output* | 7,592 | 52.6 | **+0.109** | +0.086 | +0.130 |
| Volume Profile | 3,397 | 57.2 | +0.095 | +0.169 | +0.019 |
| Liquidity Cartographer | 6,546 | 49.0 | +0.064 | +0.017 | +0.105 |
| Market Structure | 8,092 | 45.8 | **−0.040** | — | — |

Three things came out of this, and two of them changed the code:

- **Market Structure has no directional edge.** Break-of-structure pointed the
  right way 45.8% of the time — most breaks fail, which is a known property of
  the pattern. It no longer votes on direction and contributes levels, order
  blocks and location instead. It was *not* inverted to profit from the negative
  reading: fitting a sign to one dataset is how backtest artefacts are born.
- **Volume Profile and Trap Forensics were voting where their model is invalid.**
  Reversion to the POC is the wrong model in a trend (measured −0.241 ATR there),
  and a trap fade against an established trend is a counter-trend trade wearing
  a pattern's clothing (−0.096 ATR). Both now abstain in those conditions. That
  single change took the debate from +0.040 to +0.109 ATR and made it positive
  in *every* regime.
- **Derivatives is the strongest analyst by a wide margin**, and in a trending
  regime it is worth +0.491 ATR. Caveat: OKX retains only 30 days of open
  interest and funding, so this analyst has **no first-half data and no
  out-of-sample validation.** Treat it as promising, not proven.

### And now the part that matters most

**The analytical layer has measurable, split-sample-consistent directional edge.
None of the three integration modes converts it into better trade outcomes.**

Swing mode, same bars, same costs:

| Panel mode | trades | win % | expectancy |
|---|---|---|---|
| off | 66 | 42.4 | −0.089R |
| advisory (fatal vetoes only) | 18 | 50.0 | −0.153R |
| gating (thesis must agree) | 34 | 32.4 | −0.389R |

Full gating is clearly worse. Advisory is indistinguishable from off on 18
trades. The simplest configuration is still the best one measured.

The likely reason is arithmetic rather than mysterious: a **+0.109 ATR** edge is
real but small against a **~1 ATR** stop plus **~0.2R** of round-trip costs. The
panel is more often right about direction without being right by enough to pay
for the risk being taken. Requiring consensus may also select for *later*
entries — consensus forms after a move is underway — which is consistent with
the gating mode showing a lower win rate and larger average loss.

**Default is `advisory`.** The panel runs, its reasoning is displayed, and its
fatal vetoes stay active because they are sound risk rules on their own terms
rather than directional guesses. It is set to `advisory` and not `gating`
because gating measured worse, and shipping the more sophisticated-sounding
mode over the better-measured one is the same mistake V2 made with its agent
names. `swarmMode: 'off'` is one option away.

### A correction to Section 1

While building this I found a bug in my own harness: each timeframe was fetched
to the same **bar count**, so 12,000 one-minute bars covered 8 days while 12,000
fifteen-minute bars covered 126 days. The intrabar stop check used only the
1-minute series, so **stops went unchecked inside the bar for roughly 93% of
every swing-mode run** and losses could run past 1R before a bar closed. The
path now falls back through progressively coarser series and finally to the
decision bar's own high/low, which is the most pessimistic option available.

Section 1's V3 figures were produced before that fix. The corrected swing-mode
V3 baseline is **66 trades at −0.089R** rather than 38 at −0.015R. The V2
comparison is unaffected in its conclusion — V2 remains decisively negative at
−0.335R over 1,051 trades — but the gap between V2 and V3 is smaller than
Section 1 states.

### Running the analyst study

```bash
node backtest/fetch-derivatives.js BTC,ETH,SOL     # OI, funding, positioning, real taker volume
node backtest/evaluate-analysts.js --horizon 8 --step 3
node backtest/run-backtest.js --mode swing --ab 1 --swarmMode advisory
```

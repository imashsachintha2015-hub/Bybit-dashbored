# MASIS strategy runner

`daemons/masis_runner.py` replaces the CME-X5 / Championship engine. `launcher.py` starts it next to the web
server and restarts it if it crashes. The old engine only runs with `MASIS_LEGACY_ENGINE=1`.

## What it trades

| | trend4h | snapback |
|---|---|---|
| Mode by default | **orders** on the Bybit demo account | **paper** (simulated fills, real prices, fees charged) |
| Coins | 42 (`core20` + `extra22`) | 42 on paper; 20 (`core20`) if live |
| Timeframe | 4h candles, checked at each 4h close | 15m candles, checked at each 15m close |
| Entry | 4h EMA50 vs EMA200 sets the side; previous candle touched the EMA20; this candle closes beyond the previous candle's high/low on the trend side | a 15m candle closes more than 2.5 ATR from its EMA20 (first time, 2h cooldown); fade it |
| Extra conditions | none | within 0.5 ATR(4h) of the 4h EMA20, 1h ATR > 1.3x its 30-day mean, and (rule A) open interest fell >= 1% in 4h **or** (rule B) BTC's last 1h candle was small |
| Stop | 2.5 x ATR(4h), set on the exchange with the order | 1 x ATR(1h), set on the exchange with the order |
| Exit | trailing stop, 4 x ATR behind the best close, moved after every 4h close; no target | take-profit 1 x ATR(1h) on the exchange, or market close after 12h |
| Risk per trade | 0.25% of equity | 0.50% of equity |

**Evidence, honestly.** Backtests Jan 2023 - Sep 2026, 12 bps costs plus funding:
* trend4h: about +0.13R per trade on the 20 coins it was researched on (positive in every period and calendar year),
  and +0.09R on 22 coins nobody had looked at (positive in all three periods, 16 of 22 coins). About 34% of trades win;
  the profit comes from a few long trends. Expect long losing streaks and a worst historical drawdown near 26% at this size.
* snapback: about 60% wins / +0.14R on the original 20 coins, but only 54.6% / +0.03R on the 22 unseen coins, and the
  edge was shrinking from period to period. That is why it starts on paper. The runner tags every trade `core20` or
  `extra22`, so the forward data will show whether the rule is specific to the original coins.

## Safety

* One position per coin, and only in coins the account does not already hold or have an order in (it re-reads the account right before every order).
* Caps: open risk per strategy and in total, total notional, one position's notional (`RUNNER_*` below).
* Daily loss halt (default 3% of the day's starting equity): no new entries until the next UTC day.
* Drawdown halt (default 25% from the peak): no new entries until you resume by hand.
* Halt switch: `--halt`, the dashboard endpoint, or `MASIS_HALT=1`. Open positions keep their stops and keep being managed.
* Every order carries its stop. If the stop is missing after the fill and cannot be set, the position is closed.
* Order ids are deterministic per signal, so a crash and restart cannot open the same trade twice.
* A signal older than 20 minutes (runner was down) is not traded. Time comes from Bybit, not the local clock.
* The runner refuses a non-demo endpoint unless `MASIS_ALLOW_REAL_MONEY=1`; without usable keys it runs on paper and says so.
* Edge monitor (every 6h) and history replay (weekly, last four months through the same signal code): a **live** strategy that is clearly
  failing is paused automatically (no new entries; open positions are still managed). Paper strategies are never paused.
  A paused strategy stays paused until `--resume`.

## Operating it

```
python daemons/masis_runner.py --status          # what it is doing right now
python daemons/masis_runner.py --report          # lifetime results per strategy / account / group
python daemons/masis_runner.py --halt "reason"   # stop new entries      (--unhalt to undo)
python daemons/masis_runner.py --pause snapback  # pause one strategy    (--resume snapback | all)
python daemons/masis_runner.py --scan            # run the history replay now (takes several minutes)
python daemons/masis_runner.py --paper --once    # dry run, no orders
python tests/test_runner.py                      # tests (parity checks need the research data and are skipped without it)
```

On the server: `GET /api/runner/status`, `GET /api/runner/log?n=100`, `POST /api/runner/control`
with `{"halt": true, "note": "..."}`, `{"pause": "snapback"}` or `{"resume": "snapback"|"all"}`.
Set `RUNNER_CONTROL_TOKEN` to require an `X-Runner-Token` header on the control endpoint.

**Going live with the snap-back:** set `RUNNER_SNAP_LIVE=1` (orders on the 20 original coins only). Do it only after the paper
results justify it: `--report` shows the win rate with a 90% confidence interval; break-even is about 53.5%.

## Settings (environment)

| Variable | Default | Meaning |
|---|---|---|
| `MASIS_RUNNER` | 1 | 0 = launcher does not start the runner |
| `MASIS_LEGACY_ENGINE` | 0 | 1 = also start the old CME-X5 engine |
| `MASIS_ALLOW_REAL_MONEY` | 0 | must be 1 to use a non-demo endpoint |
| `MASIS_PAPER` | 0 | 1 = simulate both strategies, place no orders (a cautious first deploy: set it, check the status page, then remove it) |
| `MASIS_HALT` | 0 | 1 = no new entries |
| `MASIS_FORWARD_LAB` | 1 | 0 = do not start the record-only Forward Lab daemon |
| `RUNNER_SNAP_LIVE` | 0 | 1 = snap-back places real orders |
| `RUNNER_SNAP_RULES` | `A\|B` | `A`, `B`, `A\|B` or `A&B` |
| `RUNNER_ENABLE_TREND` / `RUNNER_ENABLE_SNAP` | 1 / 1 | switch a strategy off |
| `RUNNER_TREND_COINS` / `RUNNER_SNAP_COINS` | built in | comma separated override |
| `RUNNER_RISK_TREND_PCT` / `RUNNER_RISK_SNAP_PCT` | 0.25 / 0.50 | % of equity risked per trade |
| `RUNNER_MAX_OPEN_RISK_TREND_PCT` / `_SNAP_PCT` / `_TOTAL_PCT` | 5.5 / 2.0 / 7.5 | caps on risk in open positions |
| `RUNNER_DAILY_LOSS_HALT_PCT` / `RUNNER_DRAWDOWN_HALT_PCT` | 3 / 25 | halts |
| `RUNNER_MAX_GROSS_NOTIONAL_X` / `RUNNER_MAX_POSITION_NOTIONAL_X` | 6 / 3 | notional limits (x equity) |
| `RUNNER_LEVERAGE` | 10 | leverage set on a coin before its first order |
| `RUNNER_PAPER_EQUITY` | 2000 | starting equity of the paper account |
| `RUNNER_DATA_DIR` | `scratch/runner` | log and status files |
| `RUNNER_STATE_BACKEND` | `kv` | `file` keeps state local (use for dry runs; the default uses the shared Supabase store so state survives redeploys) |
| `RUNNER_CONTROL_TOKEN` | unset | require a token on the control endpoint |

## Deploying on Railway

Railway deploys only when you trigger it. After pushing to GitHub: Railway dashboard -> the service -> deploy the latest commit.
That replaces the running container, so the old engine stops. Then check `https://<your-app>/api/runner/status`:
`running: true`, the modes you expect, and `accounts.live.equity` showing your demo balance. The Bybit variables the old
engine used are the ones the runner uses. For a cautious first deploy set `MASIS_PAPER=1` (no orders), look at the status page,
then delete the variable and redeploy to start placing demo orders.

## Files

`daemons/masis_runner.py` (loop, controls, status) - `daemons/runner/core.py` (clock, state, exchange, risk limits) -
`trading.py` (order lifecycle) - `trend4h.py`, `snapback.py` (strategies, pure signal functions) - `scans.py` (monitoring) -
`tests/test_runner.py`.

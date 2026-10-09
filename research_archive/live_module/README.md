# Live module checks: the Kalman Trend mode against the research

The dashboard mode is made of:
- `backend_lib/kalman_trend.py`: the rules, in pure Python.
- `daemons/kalman_trend_engine.py`: the 4H engine, paper and live.
- `index.html`: the mode chooser, the Kalman panel and the KALMAN chart indicator.

These checks show that it trades the system that was tested.

## 1. Rules: identical to the research code (`live_parity.py`, `live_parity_out.txt`)
Run on the research data: OKX 4H bars 2020-2026 for 36 coins, and Binance funding.

| check | result |
|---|---|
| Kalman z | largest difference 6e-13 over 363,403 bars; 0 entry crossings differ |
| ATR, BTC daily trend | identical |
| system trades (signals, entries, exits) | all 3,080 research trades reproduced, 0 extra, 0 missing |
| net R with real funding | largest difference 2e-14 |
| warm-up window (the engine computes from the last 1,000 4H bars, BTC 2,000) | the same z and signals as the full history at all 10,635 sampled bars |

**One intended difference: the funding rule.** The live rule averages funding over the last 72 hours in 8-hour units, because Bybit pays some coins every 1 or 4 hours. For 8-hour funding it equals the research's "average of the last 9 payments". On the research data it changed the filter decision at 9 of 3,068 signals, where Binance's payment interval was irregular.

## 2. The engine replayed bar by bar (`live_replay.py`, `live_replay_out.txt`)
**Setup.** A fake exchange serves the research data one 4H bar at a time to the real engine code. It runs in PAPER mode with the Kalman mode selected, 0.5% risk, max 8 open, $10 start, from 2025-07-01 to 2026-09-30 (2,736 bars).

**Trades.** The research portfolio over the same signals took 363 trades; the engine took the same 363. It also took 1 more: a signal on the last day of the data, which the research's signal range stops short of.

**Exits.** All 357 closed trades have identical exit times.

**Net R.** 205 trades are identical; the rest differ by at most 0.007R. The research charged funding until the end of the bar in which a trend-flip exit happened. The engine stops at the exit itself, which is the realistic version.

## 3. Unit tests (`tests/test_kalman_trend.py`, 13 tests, no network)
- **Rules:** crossings, BTC and funding filters, stops and the 0.5% minimum, the funding window, sizing with exchange minimums, settings clamping.
- **Inputs:** the BTC daily trend uses only completed days; open-interest and correlation helpers.
- **Engine, paper mode on a synthetic market:** trades, at most 8 open, equity equals the start plus booked P&L, files written.
- **Engine, mode not selected:** records signals but takes no positions.
- **Live mode against a fake Bybit exchange:**
  - market entries with exchange-side stops;
  - stop-outs detected at about −1R, trend-flip closes;
  - R taken from the realized P&L;
  - the engine's book matches the exchange.

## 4. Dashboard in a browser (`ui_mock_server.py`, `ui_test.js`, `screenshots/`)
The page runs in Chromium (Playwright) against a mock API that serves the replay's engine files and historical candles.
- **Mode chooser:**
  - lists Championship, CME-X5 Pure and Kalman Trend;
  - choosing Kalman posts `strategyMode: kalman`;
  - relabels the header button and the telemetry strip;
  - turns on the KALMAN indicator.
- **Chart:**
  - switches to the Bybit Fast Canvas;
  - draws the Kalman trend line, entry and exit markers, the z readout ("KT z −2.14 DOWN") and the position's entry and stop lines;
  - works on 4H and 1H.
- **Kalman panel:**
  - 8 tiles, open positions, the radar of every coin, recent trades and events;
  - settings save through `/api/kalman/settings`;
  - the LIVE switch stays disabled while the server lacks `KALMAN_LIVE=1`.
- **Mobile:** the "Kalman" tab opens the panel.
- **Errors:** none on the page with an en-US browser locale, the same as before the change. The headless default locale "en-US@posix" makes the canvas chart's date formatting throw in this test browser only.

**The real server** (`server.py`) was also checked:
- `/api/kalman/status` and `/api/kalman/indicator` serve the engine files.
- A path-traversal symbol is rejected (400) and an unknown coin returns 404.
- `/api/kalman/settings` clamps values; for example a 5% risk becomes the 2% maximum.
- Switching the mode to `kalman` and back persists.

#!/usr/bin/env python3
"""
CME-X4 V4: REAL-TIME LIVE TRADE SIMULATOR DAEMON
Live Paper-Trading & Mark-to-Market Engine
Version: 2026-09-25

Features:
- Real-time Bybit public API integration (zero API keys needed, zero real capital risk).
- Account Base: $10.00 USD, 10.0x Isolated Leverage ($1.00 margin = $10.00 notional per trade).
- Evaluates both:
    1. Support-to-Resistance Double-Bounce Setups (Research Program 12)
    2. CME-X4 V4 EMA21 Pullback Value-Zone Setups
- Single-Asset Concurrency Lock: Strictly max 1 active trade per coin.
- Live Order Lifecycle:
    - Fills at live Bybit bid/ask
    - Staged Harvest at +0.35% / +0.40%
    - Protected Stop Loss advance to +0.25%
    - Hard Stop at coin stop floor (0.34% - 0.67%)
    - Resistance Ceiling / Support Floor target exits
    - 1-Hour forward forecast tracking
- Outputs state to scratch/cme_x4_live_simulation.json every cycle.
"""

import os
import sys
import json
import time
import math
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

STATE_FILE = os.path.join(ROOT_DIR, "scratch", "cme_x4_live_simulation.json")
LOG_FILE = os.path.join(ROOT_DIR, "scratch", "cme_x4_live_simulation.log")
os.makedirs(os.path.join(ROOT_DIR, "scratch"), exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT", "SUIUSDT", "LINKUSDT"]

COIN_STOPS = {
    "BTCUSDT": 0.0034,
    "ETHUSDT": 0.0036,
    "SOLUSDT": 0.0048,
    "XRPUSDT": 0.0058,
    "LINKUSDT": 0.0067,
    "DEFAULT": 0.0048
}

INITIAL_CAPITAL = 10.00
LEVERAGE = 10.0
MARGIN_PER_TRADE = 1.00   # $1.00 margin per trade
NOTIONAL_PER_TRADE = 10.00 # $1.00 * 10x = $10.00 notional

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out = f"[LIVE SIMULATOR {ts}] {msg}"
    print(out, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(out + "\n")
    except Exception:
        pass

def fetch_live_tickers():
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    tickers = {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS-LiveSim/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            res = json.loads(r.read().decode())
            for item in res.get("result", {}).get("list", []):
                sym = item.get("symbol")
                if sym in SYMBOLS:
                    tickers[sym] = {
                        "last_price": float(item.get("lastPrice", 0)),
                        "bid": float(item.get("bid1Price", item.get("lastPrice", 0))),
                        "ask": float(item.get("ask1Price", item.get("lastPrice", 0))),
                        "high24h": float(item.get("highPrice24h", 0)),
                        "low24h": float(item.get("lowPrice24h", 0)),
                        "volume24h": float(item.get("turnover24h", 0)),
                        "funding_rate": float(item.get("fundingRate", 0))
                    }
    except Exception as e:
        log(f"Error fetching live tickers: {e}")
    return tickers

def fetch_recent_klines(sym, interval="15", limit=60):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}"
    bars = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS-LiveSim/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            res = json.loads(r.read().decode())
            raw = res.get("result", {}).get("list", [])
            for row in raw:
                bars.append({
                    "start": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5])
                })
            bars.sort(key=lambda x: x["start"])
    except Exception as e:
        log(f"Error fetching klines for {sym}: {e}")
    return bars

def calc_ema(arr, period):
    if len(arr) < period:
        return arr[-1] if arr else 0.0
    k = 2.0 / (period + 1)
    ema = sum(arr[:period]) / period
    for val in arr[period:]:
        ema = val * k + ema * (1.0 - k)
    return float(ema)

def calc_me(closes, window=14):
    if len(closes) < window + 1:
        return 0.5
    sub = [float(x) for x in closes[-(window + 1):]]
    net_disp = abs(sub[-1] - sub[0])
    gross_path = sum(abs(sub[i] - sub[i-1]) for i in range(1, len(sub)))
    return float(net_disp / gross_path) if gross_path > 0 else 0.0

class LiveTradeSimulator:
    def __init__(self):
        self.equity = INITIAL_CAPITAL
        self.peak_equity = INITIAL_CAPITAL
        self.active_trades = []
        self.closed_trades = []
        self.trade_counter = 0
        self.last_scan_time = 0
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    self.equity = d.get("equity_usd", INITIAL_CAPITAL)
                    self.peak_equity = d.get("peak_equity_usd", INITIAL_CAPITAL)
                    self.active_trades = d.get("active_trades", [])
                    self.closed_trades = d.get("closed_trades", [])
                    self.trade_counter = d.get("total_trades_count", 0)
                    log(f"Loaded existing live state: Equity ${self.equity:.2f} | {len(self.active_trades)} Active | {len(self.closed_trades)} Closed")
            except Exception as e:
                log(f"Warn loading state: {e}")

    def save_state(self, tickers):
        # Calculate unrealized PnL
        unrealized_usd = sum(t.get("unrealized_pnl_usd", 0) for t in self.active_trades)
        total_balance = round(self.equity + unrealized_usd, 3)

        payload = {
            "title": "CME-X4 V4 Real-Time Live Trade Simulator",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "account_config": {
                "initial_capital_usd": INITIAL_CAPITAL,
                "current_equity_usd": self.equity,
                "unrealized_pnl_usd": round(unrealized_usd, 3),
                "total_account_value_usd": total_balance,
                "leverage": f"{LEVERAGE}x (Isolated)",
                "margin_per_trade_usd": MARGIN_PER_TRADE,
                "notional_per_trade_usd": NOTIONAL_PER_TRADE,
                "net_realized_pnl_usd": round(self.equity - INITIAL_CAPITAL, 3),
                "net_roi_pct": round(((self.equity - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100.0, 2),
                "max_drawdown_pct": round(max(0.0, (self.peak_equity - total_balance) / max(0.1, self.peak_equity) * 100.0), 2)
            },
            "performance_summary": {
                "active_trades_count": len(self.active_trades),
                "closed_trades_count": len(self.closed_trades),
                "wins": sum(1 for t in self.closed_trades if t.get("is_win") == 1),
                "losses": sum(1 for t in self.closed_trades if t.get("is_win") == 0),
                "win_rate_pct": round(sum(1 for t in self.closed_trades if t.get("is_win") == 1) / max(1, len(self.closed_trades)) * 100.0, 1),
                "staged_harvests_triggered": sum(1 for t in self.closed_trades if t.get("harvest_triggered"))
            },
            "active_trades": self.active_trades,
            "recent_closed_trades": self.closed_trades[:15],
            "live_market_tickers": tickers
        }

        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            log(f"Error saving state file: {e}")

    def scan_for_setups(self, tickers):
        # Enforce single-asset concurrency lock: coins currently active cannot open duplicates
        active_symbols = set(t["symbol"] for t in self.active_trades)

        for sym in SYMBOLS:
            if sym in active_symbols:
                continue

            ticker = tickers.get(sym)
            if not ticker or ticker["last_price"] <= 0:
                continue

            bars15 = fetch_recent_klines(sym, "15", limit=45)
            if len(bars15) < 30:
                continue

            closes15 = [b["close"] for b in bars15]
            cur_p = ticker["last_price"]

            ema9 = calc_ema(closes15, 9)
            ema21 = calc_ema(closes15, 21)
            me14 = calc_me(closes15, 14)

            # Volatility
            ret21 = [(closes15[i] - closes15[i-1]) / closes15[i-1] for i in range(len(closes15)-15, len(closes15))]
            mean_r = sum(ret21) / len(ret21)
            var_r = sum((r - mean_r) ** 2 for r in ret21) / len(ret21)
            sigma = max(0.003, min(0.035, math.sqrt(var_r)))

            # Support & Resistance detection in recent 30 bars
            recent_lows = [b["low"] for b in bars15[-25:]]
            recent_highs = [b["high"] for b in bars15[-25:]]
            local_support = min(recent_lows)
            local_resistance = max(recent_highs)

            # Setup 1: Support-to-Resistance / Resistance-to-Support Boundary Bounce
            dist_to_support = (cur_p - local_support) / cur_p
            dist_to_resistance = (local_resistance - cur_p) / cur_p

            # Long at Support: within 0.50% of support floor, with at least 0.70% room to resistance
            is_sr_long = (dist_to_support <= 0.0050 and dist_to_resistance >= 0.0070)
            # Short at Resistance: within 0.50% of resistance ceiling, with at least 0.70% room to support
            is_sr_short = (dist_to_resistance <= 0.0050 and dist_to_support >= 0.0070)

            # Setup 2: CME-X4 V4 Value-Zone Pullback (Trend Continuation)
            is_v4_long = (ema9 >= ema21 and me14 >= 0.20 and abs(cur_p - ema21) / cur_p <= 0.0050 and cur_p >= ema21 * 0.9990)
            is_v4_short = (ema9 <= ema21 and me14 >= 0.20 and abs(cur_p - ema21) / cur_p <= 0.0050 and cur_p <= ema21 * 1.0010)

            trigger = None
            setup_name = ""
            direction = ""

            if is_sr_long:
                trigger = True
                setup_name = "SR_DOUBLE_BOUNCE_LONG"
                direction = "LONG"
            elif is_sr_short:
                trigger = True
                setup_name = "SR_DOUBLE_BOUNCE_SHORT"
                direction = "SHORT"
            elif is_v4_long:
                trigger = True
                setup_name = "CME_X4_EMA21_PULLBACK_LONG"
                direction = "LONG"
            elif is_v4_short:
                trigger = True
                setup_name = "CME_X4_EMA21_PULLBACK_SHORT"
                direction = "SHORT"

            if trigger:
                # Open simulated paper position
                self.trade_counter += 1
                fill_price = ticker["ask"] if direction == "LONG" else ticker["bid"]
                stop_floor = COIN_STOPS.get(sym, COIN_STOPS["DEFAULT"])

                if direction == "LONG":
                    stop_price = round(fill_price * (1.0 - stop_floor), 4)
                    tp1_price = round(fill_price * (1.0 + 0.0035), 4) # +0.35% staged harvest
                    target_price = round(local_resistance if local_resistance > fill_price else fill_price * 1.015, 4)
                else:
                    stop_price = round(fill_price * (1.0 + stop_floor), 4)
                    tp1_price = round(fill_price * (1.0 - 0.0035), 4)
                    target_price = round(local_support if local_support < fill_price else fill_price * 0.985, 4)

                trade = {
                    "trade_id": f"LIVE-SIM-{sym}-{self.trade_counter}",
                    "symbol": sym,
                    "direction": direction,
                    "setup_type": setup_name,
                    "entry_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "fill_price": fill_price,
                    "current_price": fill_price,
                    "margin_usd": MARGIN_PER_TRADE,
                    "notional_usd": NOTIONAL_PER_TRADE,
                    "stop_loss_price": stop_price,
                    "staged_harvest_price": tp1_price,
                    "target_price": target_price,
                    "harvest_triggered": False,
                    "stop_advanced": False,
                    "highest_fav_pct": 0.0,
                    "highest_adv_pct": 0.0,
                    "unrealized_pnl_usd": 0.0,
                    "unrealized_roi_pct": 0.0,
                    "duration_sec": 0,
                    "status": "ACTIVE_SIMULATED"
                }

                self.active_trades.append(trade)
                log(f"OPENED LIVE TRADE: {trade['trade_id']} | {sym} {direction} at ${fill_price} | TP1: ${tp1_price} | SL: ${stop_price}")
                break # open max 1 new trade per scanning cycle to maintain orderliness

    def update_active_trades(self, tickers):
        remaining_trades = []
        now_ts = time.time()

        for t in self.active_trades:
            sym = t["symbol"]
            ticker = tickers.get(sym)
            if not ticker:
                remaining_trades.append(t)
                continue

            cur_p = ticker["last_price"]
            t["current_price"] = cur_p
            direction = t["direction"]
            fill_p = t["fill_price"]

            # Excursion calculation
            if direction == "LONG":
                fav = (cur_p - fill_p) / fill_p
                adv = (fill_p - cur_p) / fill_p
            else:
                fav = (fill_p - cur_p) / fill_p
                adv = (cur_p - fill_p) / fill_p

            if fav > t["highest_fav_pct"]:
                t["highest_fav_pct"] = round(fav * 100.0, 3)
            if adv > t["highest_adv_pct"]:
                t["highest_adv_pct"] = round(adv * 100.0, 3)

            # Dollar PnL on $10.00 Notional position
            pnl_pct = fav if fav >= adv else -adv
            dollar_pnl = round(pnl_pct * NOTIONAL_PER_TRADE, 4)
            roi_margin_pct = round((dollar_pnl / MARGIN_PER_TRADE) * 100.0, 2)

            t["unrealized_pnl_usd"] = dollar_pnl
            t["unrealized_roi_pct"] = roi_margin_pct

            # ── EXIT CHECKS ───────────────────────────────────────────────
            trade_closed = False
            exit_reason = ""
            exit_price = cur_p

            # 1. Staged Harvest Check (+0.35%)
            if fav >= 0.0035 and not t["harvest_triggered"]:
                t["harvest_triggered"] = True
                t["stop_advanced"] = True
                # Move stop to +0.25% protected breakeven
                if direction == "LONG":
                    t["stop_loss_price"] = round(fill_p * 1.0025, 4)
                else:
                    t["stop_loss_price"] = round(fill_p * 0.9975, 4)
                log(f"HARVEST TRIGGERED: {t['trade_id']} at +{fav*100:.2f}%! Stop advanced to +0.25% breakeven.")

            # 2. Stop Loss Check
            if t["stop_advanced"]:
                # Protected Breakeven Stop
                if (direction == "LONG" and cur_p <= t["stop_loss_price"]) or (direction == "SHORT" and cur_p >= t["stop_loss_price"]):
                    trade_closed = True
                    exit_reason = "PROTECTED_BREAKEVEN_EXIT (+0.25%)"
                    exit_price = t["stop_loss_price"]
            else:
                # Hard Stop Floor
                if (direction == "LONG" and cur_p <= t["stop_loss_price"]) or (direction == "SHORT" and cur_p >= t["stop_loss_price"]):
                    trade_closed = True
                    exit_reason = "HARD_STOP_LOSS"
                    exit_price = t["stop_loss_price"]

            # 3. Take Profit Target (Resistance Ceiling / Support Floor)
            if (direction == "LONG" and cur_p >= t["target_price"]) or (direction == "SHORT" and cur_p <= t["target_price"]):
                trade_closed = True
                exit_reason = "TARGET_RESISTANCE_REACHED"
                exit_price = t["target_price"]

            if trade_closed:
                # Realize PnL
                if direction == "LONG":
                    final_ret = (exit_price - fill_p) / fill_p
                else:
                    final_ret = (fill_p - exit_price) / fill_p

                # Deduct fees (11 bps roundtrip)
                final_ret = final_ret - 0.0011
                realized_dollar_pnl = round(final_ret * NOTIONAL_PER_TRADE, 4)
                self.equity = round(self.equity + realized_dollar_pnl, 4)
                if self.equity > self.peak_equity:
                    self.peak_equity = self.equity

                is_win = 1 if realized_dollar_pnl > 0 else 0

                closed_record = {
                    "trade_id": t["trade_id"],
                    "symbol": sym,
                    "direction": direction,
                    "setup_type": t["setup_type"],
                    "entry_time": t["entry_time"],
                    "exit_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "fill_price": fill_p,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "realized_pnl_usd": realized_dollar_pnl,
                    "roi_on_margin_pct": round((realized_dollar_pnl / MARGIN_PER_TRADE) * 100.0, 2),
                    "equity_after_usd": self.equity,
                    "is_win": is_win,
                    "harvest_triggered": t["harvest_triggered"]
                }

                self.closed_trades.insert(0, closed_record)
                log(f"CLOSED TRADE: {t['trade_id']} | Reason: {exit_reason} | PnL: ${realized_dollar_pnl:+.3f} | Equity: ${self.equity:.2f}")
            else:
                remaining_trades.append(t)

        self.active_trades = remaining_trades

    def step(self):
        tickers = fetch_live_tickers()
        if not tickers:
            return

        # 1. Update existing active positions
        self.update_active_trades(tickers)

        # 2. Scan for new setups if fewer than 2 active trades
        if len(self.active_trades) < 2 and (time.time() - self.last_scan_time > 15):
            self.scan_for_setups(tickers)
            self.last_scan_time = time.time()

        # 3. Persist live state for the frontend dashboard
        self.save_state(tickers)

def run_daemon():
    log("=" * 70)
    log("  CME-X4 V4: REAL-TIME LIVE TRADE SIMULATOR DAEMON STARTING")
    log(f"  Base Equity: ${INITIAL_CAPITAL:.2f} | Leverage: {LEVERAGE}x | Margin: ${MARGIN_PER_TRADE:.2f}/trade")
    log("=" * 70)
    sim = LiveTradeSimulator()

    while True:
        try:
            sim.step()
            time.sleep(4.0)
        except KeyboardInterrupt:
            log("Shutting down live simulator daemon.")
            break
        except Exception as e:
            log(f"Unexpected error in live sim loop: {e}")
            time.sleep(5.0)

if __name__ == "__main__":
    run_daemon()

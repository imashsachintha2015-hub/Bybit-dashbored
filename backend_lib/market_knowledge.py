"""
Market Prophet Knowledge Base & Self-Learning Engine.
Backed by Supabase Cloud Database (https://rzizfjujdlmevywutjvv.supabase.co) with local SQLite fallback.
Persists trade episodes, microstructure snapshots, and DeepSeek reflections across both
Vercel serverless functions and continuous local daemons.
"""

import os
import sys
import json
import sqlite3
import time
import math
import urllib.request
from datetime import datetime

from .supabase_client import (
    supabase_get,
    supabase_post,
    supabase_patch,
    SUPABASE_URL,
    SUPABASE_KEY
)

DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(DIRECTORY, "market_knowledge.db")

# Load DeepSeek credentials
ENV_PATH = os.path.join(DIRECTORY, ".env")
env = {}
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL") or env.get("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL") or env.get("DEEPSEEK_MODEL", "deepseek-chat")


class MarketKnowledgeBase:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_sqlite_fallback()

    def _get_sqlite_conn(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception:
            return None

    def _init_sqlite_fallback(self):
        conn = self._get_sqlite_conn()
        if not conn:
            return
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ai_target_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        target_equity REAL DEFAULT 15.0,
                        target_profit REAL DEFAULT 5.5,
                        is_armed INTEGER DEFAULT 1,
                        status TEXT DEFAULT 'ACTIVE',
                        last_updated INTEGER
                    )
                """)
                cur.execute("""
                    INSERT OR IGNORE INTO ai_target_state (id, target_equity, target_profit, is_armed, status, last_updated)
                    VALUES (1, 15.0, 5.5, 1, 'ACTIVE', ?)
                """, (int(time.time() * 1000),))

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trade_episodes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL,
                        size REAL NOT NULL,
                        pnl_net REAL,
                        pnl_pct REAL,
                        mfe_pct REAL DEFAULT 0.0,
                        mae_pct REAL DEFAULT 0.0,
                        exit_reason TEXT,
                        microstructure_json TEXT,
                        deepseek_reflection TEXT,
                        recorded_at INTEGER NOT NULL
                    )
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS learned_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        pattern_name TEXT NOT NULL,
                        rule_summary TEXT NOT NULL,
                        sample_count INTEGER DEFAULT 1,
                        win_rate REAL DEFAULT 0.0,
                        confidence REAL DEFAULT 0.8,
                        last_updated INTEGER NOT NULL
                    )
                """)
                conn.commit()
        except Exception as e:
            print(f"[MarketKnowledgeBase SQLite init error]: {e}")

    # ── Live Market Volatility & Feasibility Assessment Engine ───────────
    def get_live_market_volatility(self):
        """
        Calculates live market volatility across linear perps (BTC, ETH, SOL, DOGE, XRP, ICP, AVAX, SUI).
        Cached for 90 seconds to prevent rate-limiting and keep response instant.
        """
        global _market_vol_cache
        if "_market_vol_cache" not in globals():
            _market_vol_cache = {"ts": 0, "avg_range_pct": 5.5, "regime": "NORMAL_EXPANSION"}

        now = time.time()
        if _market_vol_cache["ts"] and (now - _market_vol_cache["ts"] < 90):
            return _market_vol_cache

        # 1. Try reading recent snapshot from scratch/live_market_state.json
        state_file = os.path.join(DIRECTORY, "scratch", "live_market_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    s = json.load(f)
                    if now - s.get("timestamp", 0) < 180:
                        leaderboard = s.get("leaderboard", [])
                        if leaderboard and len(leaderboard) >= 4:
                            # Use high-low ranges from leaderboard
                            pass
            except Exception:
                pass

        # 2. Query Bybit tickers directly (fast, lightweight public endpoint)
        try:
            url = "https://api.bybit.com/v5/market/tickers?category=linear"
            req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
            with urllib.request.urlopen(req, timeout=4) as r:
                data = json.loads(r.read().decode())
                tickers = data.get("result", {}).get("list", [])
                core_syms = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT", "ICPUSDT", "AVAXUSDT", "SUIUSDT"}
                watched = [t for t in tickers if t.get("symbol") in core_syms]
                ranges = [
                    ((float(t["highPrice24h"]) - float(t["lowPrice24h"])) / float(t["lowPrice24h"])) * 100.0
                    for t in watched if float(t.get("lowPrice24h", 0)) > 0
                ]
                if ranges:
                    avg_range = round(sum(ranges) / len(ranges), 2)
                    regime = "HIGH_VOLATILITY" if avg_range >= 7.0 else ("NORMAL_EXPANSION" if avg_range >= 4.0 else "LOW_VOL_CHOP")
                    _market_vol_cache = {
                        "ts": now,
                        "avg_range_pct": avg_range,
                        "regime": regime
                    }
                    return _market_vol_cache
        except Exception:
            pass

        _market_vol_cache["ts"] = now
        return _market_vol_cache

    def evaluate_target_feasibility(self, target_equity, time_horizon_hours, current_equity=None):
        """
        Evaluates whether a target equity is realistically achievable within the specified
        time horizon given current live market volatility, Bybit contract limits, and account equity.
        """
        eq = float(current_equity) if current_equity is not None and current_equity > 0 else 10.22
        tgt = float(target_equity) if target_equity is not None and target_equity > 0 else 15.0
        horizon = max(0.25, float(time_horizon_hours) if time_horizon_hours is not None and time_horizon_hours > 0 else 24.0)

        needed_usd = max(0.0, tgt - eq)
        needed_pct = round((needed_usd / eq) * 100.0, 1) if eq > 0 else 0.0
        req_velocity = round(needed_usd / horizon, 2)

        vol_data = self.get_live_market_volatility()
        avg_range_pct = vol_data.get("avg_range_pct", 5.5)
        regime = vol_data.get("regime", "NORMAL_EXPANSION")

        # Bybit micro-account dynamics:
        # Swing target: +3.85% move. Net gain per win with 5x leverage on $5-$8 margin is ~$1.15.
        # Average duration of 3.85% swing in current volatility:
        if avg_range_pct >= 7.0:
            avg_swing_duration = 3.5
            sustainable_velocity = round(0.028 * eq, 2)
        elif avg_range_pct >= 4.0:
            avg_swing_duration = 5.0
            sustainable_velocity = round(0.020 * eq, 2)
        else:
            avg_swing_duration = 9.0
            sustainable_velocity = round(0.010 * eq, 2)

        avg_gain_per_swing = 1.15
        swings_needed = math.ceil(needed_usd / avg_gain_per_swing) if needed_usd > 0 else 0

        # Minimum realistic hours needed allowing for 1.35x trade spacing and minor scratches
        min_realistic_hours = max(2.0, round(swings_needed * avg_swing_duration * 1.35, 1)) if needed_usd > 0 else 0.0

        # Determine recommended horizon rounded to clean presets
        if min_realistic_hours <= 12:
            recommended_horizon = 12.0
        elif min_realistic_hours <= 24:
            recommended_horizon = 24.0
        elif min_realistic_hours <= 48:
            recommended_horizon = 48.0
        else:
            recommended_horizon = 72.0

        feasibility_ratio = horizon / min_realistic_hours if min_realistic_hours > 0 else 999.0

        if needed_usd <= 0:
            verdict = "COMPLETED"
            score = 100
            status_label = "TARGET ACHIEVED"
            color = "#10b981"
            badge_bg = "rgba(16, 185, 129, 0.15)"
            advice = f"Target equity ${tgt:.2f} is already achieved! Capital safely parked."
            is_achievable = True
        elif feasibility_ratio >= 1.0 and req_velocity <= sustainable_velocity * 1.35:
            verdict = "ACHIEVABLE"
            score = min(98, round(75 + min(23, (feasibility_ratio - 1.0) * 15)))
            status_label = "ACHIEVABLE IN CURRENT MARKET"
            color = "#16c784"
            badge_bg = "rgba(22, 199, 132, 0.15)"
            advice = (
                f"Target +${needed_usd:.2f} (+{needed_pct}%) is realistic with current market volatility "
                f"({avg_range_pct:.1f}% 24h range). Required velocity +${req_velocity:.2f}/hr is sustainable "
                f"across ~{swings_needed} swing cycles in {horizon:.0f}h."
            )
            is_achievable = True
        elif feasibility_ratio >= 0.55 or req_velocity <= sustainable_velocity * 2.2:
            verdict = "STRETCH"
            score = round(45 + max(0, min(29, (feasibility_ratio - 0.55) / 0.45 * 29)))
            status_label = "STRETCH (ELEVATED RISK)"
            color = "#f59e0b"
            badge_bg = "rgba(245, 158, 11, 0.15)"
            advice = (
                f"Aggressive pace: +${req_velocity:.2f}/hr requires rapid back-to-back runner momentum with zero drawdowns. "
                f"Current volatility ({avg_range_pct:.1f}%) requires ~{min_realistic_hours:.0f}h for safe execution. "
                f"Recommended Horizon: ≥ {recommended_horizon:.0f}h."
            )
            is_achievable = False
        else:
            verdict = "UNREALISTIC"
            score = max(10, round(feasibility_ratio * 40))
            status_label = "UNREALISTIC IN CURRENT MARKET"
            color = "#f87171"
            badge_bg = "rgba(248, 113, 113, 0.15)"
            advice = (
                f"{horizon:.0f}h is too brief for +${needed_usd:.2f} (+{needed_pct}% gain) in current market volatility ({avg_range_pct:.1f}%). "
                f"Forcing +${req_velocity:.2f}/hr velocity carries extreme liquidation risk. "
                f"Recommended Horizon: ≥ {recommended_horizon:.0f}h."
            )
            is_achievable = False

        return {
            "verdict": verdict,
            "score": score,
            "status_label": status_label,
            "color": color,
            "badge_bg": badge_bg,
            "is_achievable": is_achievable,
            "target_equity": tgt,
            "current_equity": eq,
            "needed_profit": round(needed_usd, 2),
            "needed_return_pct": needed_pct,
            "time_horizon_hours": horizon,
            "required_velocity_usd_hr": req_velocity,
            "sustainable_velocity_usd_hr": sustainable_velocity,
            "market_volatility_pct": avg_range_pct,
            "market_regime": regime,
            "swings_needed": swings_needed,
            "min_realistic_hours": min_realistic_hours,
            "recommended_horizon_hours": recommended_horizon,
            "advice": advice
        }

    # ── Target Mode Controls ─────────────────────────────────────────────
    def get_target_state(self, current_equity=None):
        """
        Reads target mode state from Supabase target_mode_state table.
        Auto-parks if current_equity >= target_equity.
        """
        # ── In-Memory Cache to slash Supabase network egress ──
        global _target_state_cache
        if "_target_state_cache" not in globals():
            _target_state_cache = {
                "data": None, "timestamp": 0,
                "last_patched_eq": 0.0, "last_patch_time": 0,
                # pacing KV cached separately — only changes when user sets horizon
                "pacing": None, "pacing_ts": 0,
            }

        now = time.time()
        # 1. Supabase Primary (Cached for 45s unless force updated)
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                if not _target_state_cache["data"] or (now - _target_state_cache["timestamp"] >= 45):
                    # Only select the 6 columns we actually use — avoids pulling heavy jsonb cols
                    rows = supabase_get("target_mode_state", {
                        "id": "eq.1",
                        "select": "id,target_equity,is_armed,status,strategy_mode,current_equity"
                    })
                    if rows and isinstance(rows, list) and len(rows) > 0:
                        _target_state_cache["data"] = rows[0]
                        _target_state_cache["timestamp"] = now

                row = _target_state_cache["data"]
                if row:
                    target_eq = float(row.get("target_equity") or 15.0)
                    is_armed = bool(row.get("is_armed"))
                    status = str(row.get("status") or "ACTIVE")

                    progress_pct = 0.0
                    if current_equity is not None and target_eq > 0:
                        progress_pct = round(min(100.0, max(0.0, (current_equity / target_eq) * 100.0)), 1)

                    if current_equity is not None and is_armed:
                        should_patch = False
                        patch_payload = {}

                        if current_equity >= target_eq and status != "TARGET_REACHED_PARKED":
                            status = "TARGET_REACHED_PARKED"
                            is_armed = False
                            patch_payload = {"status": status, "is_armed": False, "current_equity": current_equity, "progress_pct": 100.0}
                            should_patch = True
                        elif status == "TARGET_REACHED_PARKED" and current_equity < target_eq:
                            status = "ACTIVE"
                            patch_payload = {"status": status, "current_equity": current_equity, "progress_pct": progress_pct}
                            should_patch = True
                        elif (abs(current_equity - _target_state_cache.get("last_patched_eq", 0)) >= 0.05) or (now - _target_state_cache.get("last_patch_time", 0) >= 300):
                            patch_payload = {"current_equity": current_equity, "progress_pct": progress_pct}
                            should_patch = True

                        if should_patch and patch_payload:
                            supabase_patch("target_mode_state", {"id": "eq.1"}, patch_payload)
                            _target_state_cache["last_patched_eq"] = current_equity
                            _target_state_cache["last_patch_time"] = now
                            row.update(patch_payload)

                    strategy_mode = str(row.get("strategy_mode") or "SWING_RUNNER")
                    
                    # ── Target Horizon, Velocity & ETA Engine ──
                    # Cache pacing KV for 5 minutes — it only changes when user sets a new horizon
                    from .supabase_client import supabase_kv_get
                    if not _target_state_cache["pacing"] or (now - _target_state_cache["pacing_ts"] >= 300):
                        _target_state_cache["pacing"] = supabase_kv_get("target_pacing_state") or {}
                        _target_state_cache["pacing_ts"] = now
                    pacing_kv = _target_state_cache["pacing"]
                    time_horizon_hours = float(pacing_kv.get("time_horizon_hours") or 24.0)
                    start_time = float(pacing_kv.get("start_time") or (now - 3600))
                    start_eq = float(pacing_kv.get("start_equity") or (target_eq - 5.0))

                    elapsed_hours = max(0.1, (now - start_time) / 3600.0)
                    remaining_hours = max(0.1, time_horizon_hours - elapsed_hours)

                    effective_eq = current_equity if current_equity is not None else start_eq
                    needed_usd = max(0.0, target_eq - effective_eq)
                    profit_earned = max(0.0, effective_eq - start_eq)

                    req_velocity = round(needed_usd / remaining_hours, 2)
                    actual_velocity = round(profit_earned / elapsed_hours, 2)

                    if actual_velocity > 0.05:
                        eta_hours = round(needed_usd / actual_velocity, 1)
                    else:
                        # Baseline: ~$0.85 per 3.5h swing cycle in HTF Swing Runner mode
                        swings_needed = math.ceil(needed_usd / 0.85) if needed_usd > 0 else 0
                        eta_hours = round(swings_needed * 3.5, 1)

                    eta_days = int(eta_hours // 24)
                    eta_rem_hrs = int(eta_hours % 24)
                    eta_mins = int((eta_hours % 1) * 60)
                    eta_display = f"{eta_days}d {eta_rem_hrs}h" if eta_days > 0 else f"{int(eta_hours)}h {eta_mins}m"

                    if current_equity and current_equity >= target_eq:
                        pacing_status = "COMPLETED"
                        pacing_msg = f"Target ${target_eq:.2f} reached! Capital parked."
                    elif actual_velocity >= req_velocity * 1.15:
                        pacing_status = "AHEAD_OF_PACE"
                        pacing_msg = f"Velocity +${actual_velocity:.2f}/hr (Ahead of +${req_velocity:.2f}/hr needed). ETA: {eta_display}"
                    elif actual_velocity >= req_velocity * 0.75:
                        pacing_status = "ON_TRACK"
                        pacing_msg = f"Velocity +${actual_velocity:.2f}/hr (On pace for {time_horizon_hours:.0f}h sprint). ETA: {eta_display}"
                    elif remaining_hours <= 2.0 and needed_usd > 1.0:
                        pacing_status = "HORIZON_EXPIRING"
                        pacing_msg = f"Window closing ({remaining_hours:.1f}h remaining). High-conviction runner required."
                    else:
                        pacing_status = "PACING_ACTIVE"
                        pacing_msg = f"Sprint Pace: +${req_velocity:.2f}/hr needed for ${target_eq:.2f} within {remaining_hours:.1f}h. ETA: {eta_display}"

                    feasibility = self.evaluate_target_feasibility(
                        target_equity=target_eq,
                        time_horizon_hours=time_horizon_hours,
                        current_equity=effective_eq
                    )

                    return {
                        "target_equity": target_eq,
                        "target_profit": round(needed_usd, 2),
                        "is_armed": is_armed,
                        "status": status,
                        "strategy_mode": strategy_mode,
                        "current_equity": current_equity,
                        "progress_pct": progress_pct,
                        "time_horizon_hours": time_horizon_hours,
                        "start_equity": start_eq,
                        "start_time": int(start_time),
                        "elapsed_hours": round(elapsed_hours, 1),
                        "remaining_hours": round(remaining_hours, 1),
                        "required_velocity_usd_hr": req_velocity,
                        "actual_velocity_usd_hr": actual_velocity,
                        "eta_hours": eta_hours,
                        "eta_display": eta_display,
                        "pacing_status": pacing_status,
                        "pacing_message": pacing_msg,
                        "feasibility": feasibility,
                        "last_updated": int(now * 1000)
                    }
            except Exception as e:
                print(f"[Supabase get_target_state failed]: {e}")

        # 2. SQLite Fallback
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute("SELECT target_equity, target_profit, is_armed, status, last_updated FROM ai_target_state WHERE id = 1")
                    r = cur.fetchone()
                    if r:
                        target_eq = r["target_equity"]
                        is_armed = bool(r["is_armed"])
                        status = r["status"]
                        progress_pct = round(min(100.0, max(0.0, (current_equity / target_eq) * 100.0)), 1) if current_equity and target_eq > 0 else 0.0
                        feasibility = self.evaluate_target_feasibility(
                            target_equity=target_eq,
                            time_horizon_hours=24.0,
                            current_equity=current_equity
                        )
                        return {
                            "target_equity": target_eq,
                            "target_profit": r["target_profit"],
                            "is_armed": is_armed,
                            "status": status,
                            "strategy_mode": "SWING_RUNNER",
                            "current_equity": current_equity,
                            "progress_pct": progress_pct,
                            "time_horizon_hours": 24.0,
                            "elapsed_hours": 1.0,
                            "remaining_hours": 23.0,
                            "required_velocity_usd_hr": 0.21,
                            "actual_velocity_usd_hr": 0.0,
                            "eta_hours": 17.5,
                            "eta_display": "17h 30m",
                            "pacing_status": "PACING_ACTIVE",
                            "pacing_message": "24-Hour Sprint active. Targeting ~$0.85/winner.",
                            "feasibility": feasibility,
                            "last_updated": r["last_updated"]
                        }
            except Exception:
                pass

        default_feasibility = self.evaluate_target_feasibility(
            target_equity=15.0,
            time_horizon_hours=24.0,
            current_equity=current_equity
        )
        return {
            "target_equity": 15.0,
            "target_profit": 5.0,
            "is_armed": True,
            "status": "ACTIVE",
            "strategy_mode": "SWING_RUNNER",
            "current_equity": current_equity,
            "progress_pct": 0.0,
            "time_horizon_hours": 24.0,
            "elapsed_hours": 1.0,
            "remaining_hours": 23.0,
            "required_velocity_usd_hr": 0.21,
            "actual_velocity_usd_hr": 0.0,
            "eta_hours": 17.5,
            "eta_display": "17h 30m",
            "pacing_status": "PACING_ACTIVE",
            "pacing_message": "24-Hour Sprint active. Targeting ~$0.85/winner.",
            "feasibility": default_feasibility,
            "last_updated": int(time.time() * 1000)
        }

    def set_target_state(self, target_equity=None, is_armed=None, status=None, strategy_mode=None, time_horizon_hours=None, start_equity=None, start_time=None):
        """Updates target mode state in Supabase and SQLite with pacing parameters."""
        now = time.time()
        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                table_payload = {}
                if target_equity is not None:
                    table_payload["target_equity"] = float(target_equity)
                if is_armed is not None:
                    table_payload["is_armed"] = bool(is_armed)
                if status is not None:
                    table_payload["status"] = str(status)
                if strategy_mode is not None:
                    table_payload["strategy_mode"] = str(strategy_mode)
                if table_payload:
                    supabase_patch("target_mode_state", {"id": "eq.1"}, table_payload)
                    if "_target_state_cache" in globals() and _target_state_cache.get("data"):
                        _target_state_cache["data"].update(table_payload)

                # Store pacing parameters in Supabase KV store
                from .supabase_client import supabase_kv_set, supabase_kv_get
                pacing_kv = supabase_kv_get("target_pacing_state") or {}
                if time_horizon_hours is not None:
                    pacing_kv["time_horizon_hours"] = float(time_horizon_hours)
                if start_equity is not None:
                    pacing_kv["start_equity"] = float(start_equity)
                elif target_equity is not None:
                    # initialize start equity
                    current_eq = (_target_state_cache.get("last_patched_eq") if "_target_state_cache" in globals() else None) or float(target_equity) - 5.0
                    pacing_kv["start_equity"] = current_eq
                if start_time is not None or target_equity is not None:
                    pacing_kv["start_time"] = int(start_time or now)
                
                supabase_kv_set("target_pacing_state", pacing_kv)
                if "_target_state_cache" in globals() and _target_state_cache.get("data"):
                    _target_state_cache["data"].update(pacing_kv)
            except Exception as e:
                print(f"[Supabase set_target_state failed]: {e}")

        # 2. SQLite Fallback
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    updates = []
                    params = []
                    if target_equity is not None:
                        updates.append("target_equity = ?")
                        params.append(float(target_equity))
                    if is_armed is not None:
                        updates.append("is_armed = ?")
                        params.append(1 if is_armed else 0)
                    if status is not None:
                        updates.append("status = ?")
                        params.append(str(status))
                    if updates:
                        updates.append("last_updated = ?")
                        params.append(int(time.time() * 1000))
                        params.append(1)
                        sql = f"UPDATE ai_target_state SET {', '.join(updates)} WHERE id = ?"
                        cur.execute(sql, params)
                        conn.commit()
            except Exception:
                pass

    # ── Trade Episode Recording & Learning ────────────────────────────────
    def record_trade_close(self, trade_data, micro_data=None):
        """
        Records a completed trade into Supabase trade_episodes table,
        and triggers a DeepSeek post-mortem reflection to extract adaptive rules.
        """
        sym = trade_data.get("symbol")
        direction = trade_data.get("direction") or trade_data.get("side") or "BUY"
        entry = float(trade_data.get("entry_price") or trade_data.get("entry") or 0.0)
        exit_p = float(trade_data.get("exit_price") or trade_data.get("exit") or 0.0)
        size = float(trade_data.get("size") or trade_data.get("qty") or 0.0)
        pnl_net = float(trade_data.get("pnl_net") or trade_data.get("pnl") or 0.0)
        pnl_pct = float(trade_data.get("pnl_pct") or 0.0)
        mfe_pct = float(trade_data.get("mfe_pct") or 0.0)
        mae_pct = float(trade_data.get("mae_pct") or 0.0)
        exit_reason = str(trade_data.get("exit_reason") or "MARKET")
        micro_obj = micro_data or {}

        status = "WIN" if pnl_net > 0 else ("LOSS" if pnl_net < 0 else "BREAKEVEN")
        outcome_analysis = trade_data.get("outcome_analysis")
        if not outcome_analysis:
            if pnl_net > 0:
                outcome_analysis = f"Won: Settled with +{pnl_pct:.2f}% gain (${pnl_net:+.4f}) via {exit_reason}."
            else:
                outcome_analysis = f"Failed/Loss: Hit {exit_reason} with {pnl_pct:.2f}% loss (${pnl_net:+.4f})."

        episode_id = None

        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                res = supabase_post("trade_episodes", {
                    "symbol": sym,
                    "direction": direction,
                    "entry_price": entry,
                    "exit_price": exit_p,
                    "size": size,
                    "pnl_net": pnl_net,
                    "pnl_pct": pnl_pct,
                    "mfe_pct": mfe_pct,
                    "mae_pct": mae_pct,
                    "exit_reason": exit_reason,
                    "status": status,
                    "outcome_analysis": outcome_analysis,
                    "microstructure_json": micro_obj
                }, prefer="return=representation")
                if res and isinstance(res, list) and len(res) > 0:
                    episode_id = res[0].get("id")

                # Also update corresponding signal record if present
                supabase_patch("live_market_signals", {"symbol": f"eq.{sym}"}, {
                    "status": status,
                    "exit_price": exit_p,
                    "pnl_net": pnl_net,
                    "result_reason": outcome_analysis
                })
            except Exception as e:
                print(f"[Supabase record_trade_close failed]: {e}")

        # 2. SQLite Mirror
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO trade_episodes
                        (symbol, direction, entry_price, exit_price, size, pnl_net, pnl_pct, mfe_pct, mae_pct, exit_reason, microstructure_json, recorded_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (sym, direction, entry, exit_p, size, pnl_net, pnl_pct, mfe_pct, mae_pct, exit_reason, json.dumps(micro_obj), int(time.time() * 1000)))
                    if not episode_id:
                        episode_id = cur.lastrowid
                    conn.commit()
            except Exception as e:
                print(f"[SQLite record_trade_close mirror error]: {e}")

        # Trigger DeepSeek Reflection in a quick call
        reflection = self.generate_post_mortem_reflection(episode_id, trade_data, micro_obj)
        return {"episode_id": episode_id, "reflection": reflection}

    def generate_post_mortem_reflection(self, episode_id, trade_data, micro_data):
        """
        Conducts an authoritative 5-Point Forensic Root-Cause Post-Mortem on every completed trade:
        1. WHAT HAPPENED: Realized PnL, PnL %, MFE, MAE, Duration, Exit Reason.
        2. WHY IT HAPPENED (THE CULPRIT): Pinpoint the exact market failure or edge driver.
        3. HOW TO IDENTIFY: Concrete quantitative markers (wick %, volume ratio, displacement D, S/R).
        4. HOW TO EXECUTE: Actionable entry and execution playbook (candle close, limit vs market).
        5. HOW TO REDUCE RISK & DYNAMIC POSITIONING: Risk controls, dynamic sizing, when to exit early, and when to get profit.
        """
        sym = trade_data.get("symbol", "COIN")
        direction = trade_data.get("direction") or trade_data.get("side") or "BUY"
        pnl = float(trade_data.get("pnl_net") or trade_data.get("pnl") or 0.0)
        pnl_pct = float(trade_data.get("pnl_pct") or 0.0)
        mfe = float(trade_data.get("mfe_pct") or 0.0)
        mae = float(trade_data.get("mae_pct") or 0.0)
        exit_reason = str(trade_data.get("exit_reason", "MARKET"))
        micro = micro_data or {}
        outcome = "PROFITABLE_WIN" if pnl > 0 else "LOSS"

        u_wick = float(micro.get("upper_wick_pct_5m", 0.0))
        l_wick = float(micro.get("lower_wick_pct_5m", 0.0))
        vol_ratio = float(micro.get("vol_ratio_5m", 1.0))
        body_pct = float(micro.get("body_pct_5m", 0.0))
        displacement_d = float(micro.get("displacement_d", 0.35))

        # Heuristic Pattern & Culprit Classifier
        if direction.upper() in ("BUY", "LONG") and u_wick >= 35.0:
            deduced_pattern = "ABSORPTION_TRAP"
            default_culprit = f"Absorption Trap: Buyer breakout attempt absorbed into resting ask walls, generating {u_wick:.1f}% upper wick."
            default_identify = f"Upper wick >= 35% ({u_wick:.1f}%) with elevated volume ({vol_ratio:.1f}x) at local resistance."
            default_execute = "Wait for 5m candle close confirmation; never buy market orders into high upper wicks."
            default_risk = "Scratch position immediately if 2 consecutive 1m bars fail to reclaim the wick high; ratchet SL to breakeven at +1R."
        elif direction.upper() in ("SELL", "SHORT") and l_wick >= 30.0:
            deduced_pattern = "SUPPORT_FLOOR_DEFENSE"
            default_culprit = f"Support Floor Defense: Short executed into defended buyer liquidity floor, producing {l_wick:.1f}% lower wick."
            default_identify = f"Lower wick >= 25% ({l_wick:.1f}%) within 1.5% of 1h/15m support floor."
            default_execute = "Veto shorting into support wicks; wait for clean breakdown candle close and retest."
            default_risk = "Keep SL tightly above the breakdown pivot; exit immediately if price bounces above support."
        elif vol_ratio < 0.85 and displacement_d < 0.25:
            deduced_pattern = "CHOP_STAGNATION"
            default_culprit = f"Chop Stagnation Decay: Trade entered during low-displacement consolidation (D={displacement_d:.2f}), bleeding into fees."
            default_identify = f"Displacement efficiency D < 0.25 and volume ratio < 0.9x."
            default_execute = "Do not enter range-bound consolidation; wait for range expansion breakout."
            default_risk = "Enforce 15-minute stagnation time-stop; exit flat if MFE remains < +0.15%."
        elif u_wick < 25.0 and l_wick < 25.0 and vol_ratio >= 1.4:
            deduced_pattern = "MOMENTUM_EXPANSION"
            default_culprit = f"Momentum Expansion: Genuine directional displacement (vol {vol_ratio:.1f}x, body {body_pct:.1f}%)."
            default_identify = f"Body > 50%, volume >= 1.4x, wicks < 25%."
            default_execute = "Enter on first pullback to 5m EMA9; ride trend runner."
            default_risk = "Stage 1 exit: lock 50% at +1.2R, trail stop behind previous 5m candle low."
        elif pnl > 0:
            deduced_pattern = "PULLBACK_VALUE_RETEST"
            default_culprit = f"Pullback Value Retest: Order placed at key EMA value zone with macro trend confluence."
            default_identify = f"Price touches 15m EMA21 with low rejection wicks."
            default_execute = "Limit order at value zone rather than chasing market."
            default_risk = "Initial SL 0.3% below EMA; ratchet to +0.25% fee-proof green upon +1.0% gain."
        else:
            deduced_pattern = "FAILED_ACTIVATION_CUT"
            default_culprit = f"Failed Activation: Setup failed to generate forward momentum after entry."
            default_identify = f"MFE remained under +0.12% with adverse displacement."
            default_execute = "Execute only with confirmed orderbook imbalance and taker aggression."
            default_risk = "Enforce early activation timeout (3.5-12m); cut trade at -0.45% before full SL."

        prompt = f"""You are the Chief Quantitative Learning Officer at MASIS Institutional Trading.
A trade just closed on {sym}. Conduct an authoritative 5-Point Forensic Root-Cause Post-Mortem:
1. Trade Facts: {direction} on {sym} | PnL: ${pnl:+.4f} ({pnl_pct:+.2f}%) | MFE: +{mfe:.2f}% | MAE: {mae:.2f}% | Exit: {exit_reason}
2. Microstructure: Upper Wick {u_wick}%, Lower Wick {l_wick}%, Vol Ratio {vol_ratio}x, Displacement D {displacement_d:.2f}

Respond strictly in valid JSON:
{{
  "culprit_category": "{deduced_pattern}",
  "why_it_happened": "Clear explanation of the market driver/trap that caused the outcome",
  "how_to_identify": "Specific quantitative indicators and thresholds to spot this in real time",
  "how_to_execute": "Exact operational execution instructions (entry type, confirmation candle, limit placement)",
  "how_to_reduce_risk": "Risk controls and positioning: optimal size calibration, when to exit early on stall, when to get profit",
  "actionable_rule": "One permanent institutional rule for future trades",
  "confidence": 0.90
}}"""

        culprit = default_culprit
        how_identify = default_identify
        how_execute = default_execute
        how_risk = default_risk
        actionable_rule = f"On {sym}, ensure 5m confirmation close and scale out 50% at +1.0R."
        confidence = 0.88
        pattern_name = deduced_pattern

        if DEEPSEEK_API_KEY:
            try:
                body = json.dumps({
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a senior quantitative crypto risk researcher. Output strict JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 400,
                    "temperature": 0.1
                }).encode("utf-8")

                req = urllib.request.Request(
                    DEEPSEEK_URL,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                        "User-Agent": "MASIS/3.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=6) as r:
                    res = json.loads(r.read().decode())
                    parsed = json.loads(res["choices"][0]["message"]["content"])
                    culprit = parsed.get("why_it_happened", default_culprit)
                    how_identify = parsed.get("how_to_identify", default_identify)
                    how_execute = parsed.get("how_to_execute", default_execute)
                    how_risk = parsed.get("how_to_reduce_risk", default_risk)
                    actionable_rule = parsed.get("actionable_rule", actionable_rule)
                    confidence = float(parsed.get("confidence", 0.90))
                    pattern_name = parsed.get("culprit_category", deduced_pattern)
            except Exception as e:
                pass

        full_reflection = (
            f"[CULPRIT: {pattern_name}] {culprit} | "
            f"IDENTIFY: {how_identify} | "
            f"EXECUTE: {how_execute} | "
            f"RISK & POSITIONING: {how_risk} | "
            f"RULE: {actionable_rule}"
        )
        outcome_analysis = f"{'Won' if pnl > 0 else 'Failed'}: {culprit} (Rule: {actionable_rule})"

        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                if episode_id:
                    supabase_patch("trade_episodes", {"id": f"eq.{episode_id}"}, {
                        "deepseek_reflection": full_reflection,
                        "outcome_analysis": outcome_analysis
                    })
                # Check if rule exists for this specific pattern_name
                existing = supabase_get("learned_rules", {"symbol": f"eq.{sym}", "pattern_name": f"eq.{pattern_name}", "limit": "1"})
                if existing and isinstance(existing, list) and len(existing) > 0:
                    r_id = existing[0].get("id")
                    cur_samples = int(existing[0].get("sample_count", 1)) + 1
                    cur_wr = float(existing[0].get("win_rate", 50.0))
                    new_wr = round(((cur_wr * (cur_samples - 1)) + (100.0 if pnl > 0 else 0.0)) / cur_samples, 1)
                    is_active = (cur_samples >= 15 and confidence >= 0.85)
                    supabase_patch("learned_rules", {"id": f"eq.{r_id}"}, {
                        "rule_summary": actionable_rule,
                        "sample_count": cur_samples,
                        "win_rate": new_wr,
                        "confidence": confidence,
                        "is_active": is_active
                    })
                else:
                    supabase_post("learned_rules", {
                        "symbol": sym,
                        "pattern_name": pattern_name,
                        "rule_summary": actionable_rule,
                        "sample_count": 1,
                        "win_rate": 100.0 if pnl > 0 else 0.0,
                        "confidence": confidence,
                        "is_active": False
                    })
            except Exception as e:
                print(f"[Supabase reflection update failed]: {e}")

        # 2. SQLite Mirror with Statistical Aggregation
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    if episode_id:
                        cur.execute("UPDATE trade_episodes SET deepseek_reflection = ? WHERE id = ?", (full_reflection, episode_id))
                    
                    cur.execute("SELECT id, sample_count, win_rate FROM learned_rules WHERE symbol = ? AND pattern_name = ?", (sym, pattern_name))
                    row = cur.fetchone()
                    if row:
                        r_id, s_cnt, wr = row["id"], row["sample_count"] or 1, row["win_rate"] or 50.0
                        new_cnt = s_cnt + 1
                        new_wr = round(((wr * s_cnt) + (100.0 if pnl > 0 else 0.0)) / new_cnt, 1)
                        cur.execute("""
                            UPDATE learned_rules 
                            SET rule_summary = ?, sample_count = ?, win_rate = ?, confidence = ?, last_updated = ?
                            WHERE id = ?
                        """, (actionable_rule, new_cnt, new_wr, confidence, int(time.time() * 1000), r_id))
                    else:
                        cur.execute("""
                            INSERT INTO learned_rules (symbol, pattern_name, rule_summary, sample_count, win_rate, confidence, last_updated)
                            VALUES (?, ?, ?, 1, ?, ?, ?)
                        """, (sym, pattern_name, actionable_rule, 100.0 if pnl > 0 else 0.0, confidence, int(time.time() * 1000)))
                    conn.commit()
            except Exception as e:
                print(f"[SQLite rule mirror error]: {e}")

        return {
            "culprit": culprit,
            "how_to_identify": how_identify,
            "how_to_execute": how_execute,
            "how_to_reduce_risk": how_risk,
            "pattern_name": pattern_name,
            "rule": actionable_rule,
            "reflection": full_reflection
        }

    def get_relevant_knowledge(self, symbol=None, limit=4):
        """
        Retrieves statistically validated learned rules to inject into DeepSeek's prompt context.
        Enforces N >= 15 sample size or clearly annotates single-trade observations as non-binding hypotheses.
        """
        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                params = {
                    "select": "symbol,pattern_name,rule_summary,confidence,sample_count,win_rate",
                    "order": "sample_count.desc,id.desc",
                    "limit": str(limit)
                }
                if symbol:
                    params["symbol"] = f"in.({symbol},ALL)"
                rows = supabase_get("learned_rules", params)
                if rows is not None and isinstance(rows, list):
                    validated = []
                    for r in rows:
                        n = r.get("sample_count", 1)
                        if n < 15:
                            r["rule_summary"] = f"[PRELIMINARY HYPOTHESIS: N={n} trades, {r.get('win_rate', 0):.0f}% WR - advisory only]: {r.get('rule_summary')}"
                        validated.append(r)
                    return validated
            except Exception as e:
                print(f"[Supabase get_relevant_knowledge failed]: {e}")

        # 2. SQLite Fallback
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    if symbol:
                        cur.execute("""
                            SELECT symbol, pattern_name, rule_summary, confidence, sample_count, win_rate
                            FROM learned_rules
                            WHERE symbol = ? OR symbol = 'ALL'
                            ORDER BY sample_count DESC, id DESC LIMIT ?
                        """, (symbol, limit))
                    else:
                        cur.execute("""
                            SELECT symbol, pattern_name, rule_summary, confidence, sample_count, win_rate
                            FROM learned_rules
                            ORDER BY sample_count DESC, id DESC LIMIT ?
                        """, (limit,))
                    rows = [dict(r) for r in cur.fetchall()]
                    validated = []
                    for r in rows:
                        n = r.get("sample_count", 1)
                        if n < 15:
                            r["rule_summary"] = f"[PRELIMINARY HYPOTHESIS: N={n} trades, {r.get('win_rate', 0):.0f}% WR - advisory only]: {r.get('rule_summary')}"
                        validated.append(r)
                    return validated
            except Exception:
                pass
        return []

    def get_knowledge_summary(self):
        """Returns comprehensive stats for the Market Prophet Dashboard card with 60s cache to minimize egress."""
        global _knowledge_summary_cache
        if "_knowledge_summary_cache" not in globals():
            _knowledge_summary_cache = {"data": None, "timestamp": 0}

        now = time.time()
        if _knowledge_summary_cache["data"] and (now - _knowledge_summary_cache["timestamp"] < 60):
            return _knowledge_summary_cache["data"]

        # Try local SQLite first for instant zero-egress count
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) as total_episodes, SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) as wins FROM trade_episodes")
                    row = cur.fetchone()
                    tot = row["total_episodes"] if row else 0
                    wins = row["wins"] or 0
                    win_rate = round((wins / tot) * 100, 1) if tot > 0 else 0.0

                    cur.execute("SELECT symbol, rule_summary, confidence, last_updated FROM learned_rules ORDER BY id DESC LIMIT 6")
                    rules = [dict(r) for r in cur.fetchall()]

                    cur.execute("SELECT symbol, direction, pnl_net, pnl_pct, exit_reason, deepseek_reflection, recorded_at FROM trade_episodes ORDER BY id DESC LIMIT 5")
                    episodes = [dict(r) for r in cur.fetchall()]

                    res = {
                        "total_episodes_learned": tot,
                        "learned_win_rate": win_rate,
                        "active_rules_count": len(rules),
                        "top_rules": rules,
                        "recent_episodes": episodes,
                        "database_source": "SQLite (Ultra-low Egress)"
                    }
                    _knowledge_summary_cache = {"data": res, "timestamp": now}
                    return res
            except Exception:
                pass

        # 1. Supabase Primary fallback
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                rules = supabase_get("learned_rules", {
                    "select": "symbol,rule_summary,confidence,created_at",
                    "order": "id.desc",
                    "limit": "6"
                }) or []

                episodes = supabase_get("trade_episodes", {
                    "select": "symbol,direction,pnl_net,pnl_pct,exit_reason,deepseek_reflection,created_at",
                    "order": "id.desc",
                    "limit": "5"
                }) or []

                res = {
                    "total_episodes_learned": len(episodes),
                    "learned_win_rate": 50.0,
                    "active_rules_count": len(rules),
                    "top_rules": rules,
                    "recent_episodes": episodes,
                    "database_source": "Supabase (Cloud)"
                }
                _knowledge_summary_cache = {"data": res, "timestamp": now}
                return res
            except Exception as e:
                print(f"[Supabase get_knowledge_summary failed]: {e}")

        # 2. SQLite Fallback
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) as total_episodes, SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) as wins FROM trade_episodes")
                    row = cur.fetchone()
                    tot = row["total_episodes"] if row else 0
                    wins = row["wins"] or 0
                    win_rate = round((wins / tot) * 100, 1) if tot > 0 else 0.0

                    cur.execute("SELECT symbol, rule_summary, confidence, last_updated FROM learned_rules ORDER BY id DESC LIMIT 6")
                    rules = [dict(r) for r in cur.fetchall()]

                    cur.execute("SELECT symbol, direction, pnl_net, pnl_pct, exit_reason, deepseek_reflection, recorded_at FROM trade_episodes ORDER BY id DESC LIMIT 5")
                    episodes = [dict(r) for r in cur.fetchall()]

                    return {
                        "total_episodes_learned": tot,
                        "learned_win_rate": win_rate,
                        "active_rules_count": len(rules),
                        "top_rules": rules,
                        "recent_episodes": episodes,
                        "database_source": "SQLite (Local Fallback)"
                    }
            except Exception:
                pass

        return {
            "total_episodes_learned": 0,
            "learned_win_rate": 0.0,
            "active_rules_count": 0,
            "top_rules": [],
            "recent_episodes": [],
            "database_source": "Empty"
        }


kb = MarketKnowledgeBase()

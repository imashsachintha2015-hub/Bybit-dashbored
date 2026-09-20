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

    # ── Target Mode Controls ─────────────────────────────────────────────
    def get_target_state(self, current_equity=None):
        """
        Reads target mode state from Supabase target_mode_state table.
        Auto-parks if current_equity >= target_equity.
        """
        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                rows = supabase_get("target_mode_state", {"id": "eq.1"})
                if rows and isinstance(rows, list) and len(rows) > 0:
                    row = rows[0]
                    target_eq = float(row.get("target_equity") or 15.0)
                    is_armed = bool(row.get("is_armed"))
                    status = str(row.get("status") or "ACTIVE")

                    progress_pct = 0.0
                    if current_equity is not None and target_eq > 0:
                        progress_pct = round(min(100.0, max(0.0, (current_equity / target_eq) * 100.0)), 1)

                    if current_equity is not None and is_armed:
                        if current_equity >= target_eq:
                            status = "TARGET_REACHED_PARKED"
                            is_armed = False
                            supabase_patch("target_mode_state", {"id": "eq.1"}, {
                                "status": status,
                                "is_armed": False,
                                "current_equity": current_equity,
                                "progress_pct": 100.0
                            })
                        elif status == "TARGET_REACHED_PARKED" and current_equity < target_eq:
                            status = "ACTIVE"
                            supabase_patch("target_mode_state", {"id": "eq.1"}, {
                                "status": status,
                                "current_equity": current_equity,
                                "progress_pct": progress_pct
                            })
                        else:
                            supabase_patch("target_mode_state", {"id": "eq.1"}, {
                                "current_equity": current_equity,
                                "progress_pct": progress_pct
                            })

                    return {
                        "target_equity": target_eq,
                        "target_profit": 5.5,
                        "is_armed": is_armed,
                        "status": status,
                        "current_equity": current_equity,
                        "progress_pct": progress_pct,
                        "last_updated": int(time.time() * 1000)
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
                        return {
                            "target_equity": target_eq,
                            "target_profit": r["target_profit"],
                            "is_armed": is_armed,
                            "status": status,
                            "current_equity": current_equity,
                            "progress_pct": progress_pct,
                            "last_updated": r["last_updated"]
                        }
            except Exception:
                pass

        return {
            "target_equity": 15.0,
            "target_profit": 5.5,
            "is_armed": True,
            "status": "ACTIVE",
            "current_equity": current_equity,
            "progress_pct": 0.0,
            "last_updated": int(time.time() * 1000)
        }

    def set_target_state(self, target_equity=None, is_armed=None, status=None):
        """Updates target mode state in Supabase and SQLite."""
        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                payload = {}
                if target_equity is not None:
                    payload["target_equity"] = float(target_equity)
                if is_armed is not None:
                    payload["is_armed"] = bool(is_armed)
                if status is not None:
                    payload["status"] = str(status)
                if payload:
                    supabase_patch("target_mode_state", {"id": "eq.1"}, payload)
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
                    "microstructure_json": micro_obj
                }, prefer="return=representation")
                if res and isinstance(res, list) and len(res) > 0:
                    episode_id = res[0].get("id")
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
        """Asks DeepSeek to analyze why trade won or lost, and synthesizes a permanent rule."""
        sym = trade_data.get("symbol", "COIN")
        pnl = float(trade_data.get("pnl_net") or trade_data.get("pnl") or 0.0)
        pnl_pct = float(trade_data.get("pnl_pct") or 0.0)
        mfe = float(trade_data.get("mfe_pct") or 0.0)
        outcome = "PROFITABLE_WIN" if pnl > 0 else "LOSS"

        prompt = f"""You are the Chief Quantitative Learning Officer at MASIS Trading.
A demo micro-scalp trade just closed. Analyze the outcome and extract an institutional lesson:
- Coin: {sym}
- Outcome: {outcome} (${pnl:+.4f} USDT, {pnl_pct:+.2f}%)
- Max Favorable Excursion (Highest Gain Reached): +{mfe:.2f}%
- Exit Reason: {trade_data.get('exit_reason', 'N/A')}
- 5m Upper Wick: {micro_data.get('upper_wick_pct_5m', 0)}%
- 5m Volume Ratio: {micro_data.get('vol_ratio_5m', 1.0)}x

Synthesize one permanent rule for the knowledge base so the agent acts as an adaptive market prophet.
Respond strictly in JSON:
{{
  "lesson": "One concise sentence summarizing the root cause of the win/loss",
  "actionable_rule": "A specific, measurable rule for future trades on this coin/setup",
  "confidence": 0.88
}}"""

        rule_summary = ""
        lesson_text = ""
        confidence = 0.85

        if DEEPSEEK_API_KEY:
            try:
                body = json.dumps({
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are an institutional crypto quantitative researcher. Output strict JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 200,
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
                    lesson_text = parsed.get("lesson", "")
                    rule_summary = parsed.get("actionable_rule", "")
                    confidence = float(parsed.get("confidence", 0.85))
            except Exception as e:
                lesson_text = f"Empirical reflection: MFE reached +{mfe:.2f}%. Fee-adjusted profit claiming preserves edge."
                rule_summary = f"On {sym}, lock Stage 1 profit at +0.32% to prevent retracement."

        if not lesson_text:
            lesson_text = f"Trade exited with {pnl_pct:+.2f}%. Bank profit at +0.35%."
            rule_summary = f"On {sym}, ensure 50% partial take profit when gain reaches +0.35%."

        full_reflection = f"{lesson_text} | Rule: {rule_summary}"

        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                if episode_id:
                    supabase_patch("trade_episodes", {"id": f"eq.{episode_id}"}, {
                        "deepseek_reflection": full_reflection
                    })
                supabase_post("learned_rules", {
                    "symbol": sym,
                    "pattern_name": "TREND_PULLBACK",
                    "rule_summary": rule_summary,
                    "sample_count": 1,
                    "win_rate": 100.0 if pnl > 0 else 0.0,
                    "confidence": confidence,
                    "is_active": True
                })
            except Exception as e:
                print(f"[Supabase reflection update failed]: {e}")

        # 2. SQLite Mirror
        conn = self._get_sqlite_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    if episode_id:
                        cur.execute("UPDATE trade_episodes SET deepseek_reflection = ? WHERE id = ?", (full_reflection, episode_id))
                    cur.execute("""
                        INSERT INTO learned_rules (symbol, pattern_name, rule_summary, confidence, last_updated)
                        VALUES (?, ?, ?, ?, ?)
                    """, (sym, "TREND_PULLBACK", rule_summary, confidence, int(time.time() * 1000)))
                    conn.commit()
            except Exception:
                pass

        return {"lesson": lesson_text, "rule": rule_summary}

    def get_relevant_knowledge(self, symbol=None, limit=4):
        """Retrieves past learned rules to inject into DeepSeek's prompt context."""
        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                params = {
                    "select": "symbol,pattern_name,rule_summary,confidence",
                    "order": "id.desc",
                    "limit": str(limit)
                }
                if symbol:
                    params["symbol"] = f"in.({symbol},ALL)"
                rows = supabase_get("learned_rules", params)
                if rows is not None and isinstance(rows, list):
                    return rows
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
                            SELECT symbol, pattern_name, rule_summary, confidence
                            FROM learned_rules
                            WHERE symbol = ? OR symbol = 'ALL'
                            ORDER BY id DESC LIMIT ?
                        """, (symbol, limit))
                    else:
                        cur.execute("""
                            SELECT symbol, pattern_name, rule_summary, confidence
                            FROM learned_rules
                            ORDER BY id DESC LIMIT ?
                        """, (limit,))
                    return [dict(r) for r in cur.fetchall()]
            except Exception:
                pass
        return []

    def get_knowledge_summary(self):
        """Returns comprehensive stats for the Market Prophet Dashboard card."""
        # 1. Supabase Primary
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                # Fetch recent rules
                rules = supabase_get("learned_rules", {
                    "select": "symbol,rule_summary,confidence,created_at",
                    "order": "id.desc",
                    "limit": "6"
                }) or []

                # Fetch recent episodes
                episodes = supabase_get("trade_episodes", {
                    "select": "symbol,direction,pnl_net,pnl_pct,exit_reason,deepseek_reflection,created_at",
                    "order": "id.desc",
                    "limit": "5"
                }) or []

                # Count total episodes
                all_eps = supabase_get("trade_episodes", {"select": "pnl_net"}) or []
                tot = len(all_eps)
                wins = sum(1 for e in all_eps if float(e.get("pnl_net") or 0.0) > 0)
                win_rate = round((wins / tot) * 100, 1) if tot > 0 else 0.0

                return {
                    "total_episodes_learned": tot,
                    "learned_win_rate": win_rate,
                    "active_rules_count": len(rules),
                    "top_rules": rules,
                    "recent_episodes": episodes,
                    "database_source": "Supabase (Cloud)"
                }
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

"""
Migration Script: Local SQLite & JSON to Supabase Cloud Database.
Migrates:
1. market_knowledge.db trade_episodes -> public.trade_episodes
2. market_knowledge.db learned_rules -> public.learned_rules
3. market_knowledge.db ai_target_state -> public.target_mode_state
4. trade_stats.json -> public.app_state (key='trade_stats')
5. auto_trade_state -> public.app_state (key='auto_trade_state')
"""

import os
import json
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend_lib.supabase_client import (
    supabase_get,
    supabase_post,
    supabase_patch,
    supabase_kv_set,
    supabase_kv_get
)

DB_PATH = "market_knowledge.db"
STATS_PATH = "trade_stats.json"

def migrate_target_state():
    print("\n--- Migrating Target State ---")
    if not os.path.exists(DB_PATH):
        print("No market_knowledge.db found")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM ai_target_state WHERE id = 1")
    row = cur.fetchone()
    if row:
        target_eq = float(row["target_equity"] or 15.0)
        target_payload = {
            "id": 1,
            "target_equity": target_eq,
            "current_equity": 9.55,
            "progress_pct": round((9.55 / target_eq) * 100.0, 1),
            "is_armed": bool(row["is_armed"]),
            "status": str(row["status"] or "ACTIVE")
        }
        res = supabase_post("target_mode_state", target_payload, prefer="resolution=merge-duplicates,return=representation")
        print("Target state migrated:", res)
    conn.close()

def migrate_learned_rules():
    print("\n--- Migrating Learned Rules ---")
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM learned_rules ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    print(f"Found {len(rows)} learned rules in local sqlite.")
    batch = []
    for r in rows:
        batch.append({
            "id": r["id"],
            "symbol": r["symbol"],
            "pattern_name": r["pattern_name"] or "TREND_PULLBACK",
            "rule_summary": r["rule_summary"],
            "sample_count": r["sample_count"] or 1,
            "win_rate": float(r["win_rate"] or 0.0),
            "confidence": float(r["confidence"] or 0.85),
            "is_active": True
        })
        if len(batch) >= 25:
            res = supabase_post("learned_rules", batch, prefer="resolution=merge-duplicates,return=minimal")
            print(f"Uploaded batch of {len(batch)} rules...")
            batch = []
    if batch:
        supabase_post("learned_rules", batch, prefer="resolution=merge-duplicates,return=minimal")
        print(f"Uploaded final batch of {len(batch)} rules.")

def migrate_trade_episodes():
    print("\n--- Migrating Trade Episodes ---")
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM trade_episodes ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    print(f"Found {len(rows)} trade episodes in local sqlite.")
    batch = []
    for r in rows:
        micro_raw = r["microstructure_json"] or "{}"
        try:
            micro_obj = json.loads(micro_raw)
        except Exception:
            micro_obj = {}

        batch.append({
            "id": r["id"],
            "symbol": r["symbol"],
            "direction": r["direction"],
            "entry_price": float(r["entry_price"] or 0.0),
            "exit_price": float(r["exit_price"] or 0.0),
            "size": float(r["size"] or 0.0),
            "pnl_net": float(r["pnl_net"] or 0.0),
            "pnl_pct": float(r["pnl_pct"] or 0.0),
            "mfe_pct": float(r["mfe_pct"] or 0.0),
            "mae_pct": float(r["mae_pct"] or 0.0),
            "exit_reason": r["exit_reason"] or "N/A",
            "microstructure_json": micro_obj,
            "deepseek_reflection": r["deepseek_reflection"] or ""
        })
        if len(batch) >= 20:
            res = supabase_post("trade_episodes", batch, prefer="resolution=merge-duplicates,return=minimal")
            print(f"Uploaded batch of {len(batch)} episodes...")
            batch = []
    if batch:
        supabase_post("trade_episodes", batch, prefer="resolution=merge-duplicates,return=minimal")
        print(f"Uploaded final batch of {len(batch)} episodes.")

def migrate_trade_stats():
    print("\n--- Migrating trade_stats.json into Supabase app_state ---")
    if os.path.exists(STATS_PATH):
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            stats = json.load(f)
        supabase_kv_set("trade_stats", stats)
        print(f"Uploaded trade_stats with {len(stats.get('trade_history', []))} trades to Supabase app_state.")

    # Also default auto_trade_state if not exists
    existing = supabase_kv_get("auto_trade_state", None)
    if not existing:
        from backend_lib.auto_trade_state import DEFAULT_STATE
        supabase_kv_set("auto_trade_state", DEFAULT_STATE)
        print("Initialized auto_trade_state in Supabase app_state.")

if __name__ == "__main__":
    migrate_target_state()
    migrate_learned_rules()
    migrate_trade_episodes()
    migrate_trade_stats()
    print("\n[Migration Completed Successfully!]")

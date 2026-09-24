"""
CME-X4 Shadow Mode Database & Persistence Layer.
Records every candidate, rejection, state transition, and simulated execution.
Zero real order execution -- purely observational and forensic.
"""

import os
import json
import sqlite3
import time
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "cme_x4_shadow.db")
SNAPSHOT_PATH = os.path.join(ROOT_DIR, "scratch", "cme_x4_shadow_live.json")
os.makedirs(os.path.join(ROOT_DIR, "scratch"), exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 1. Candidates table: records every candidate that enters the pipeline
    c.execute("""
    CREATE TABLE IF NOT EXISTS shadow_candidates (
        candidate_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        state TEXT NOT NULL,
        market_state_verdict TEXT,
        mtf_coherence REAL,
        me14 REAL,
        loss_veto_verdict TEXT,
        loss_probability REAL,
        veto_threshold REAL,
        supervisor_verdict TEXT,
        supervisor_updated_at TEXT,
        supervisor_rationale TEXT,
        risk_verdict TEXT,
        risk_updated_at TEXT,
        entry_type TEXT,
        entry_price REAL,
        stop_floor_pct REAL,
        stop_price REAL,
        tp1_pct REAL,
        tp1_price REAL,
        protected_stop_pct REAL,
        protected_stop_price REAL,
        final_tp_price REAL,
        sigma REAL,
        reversal_eligible INTEGER,
        reversal_triggered INTEGER,
        reversal_reason TEXT,
        rejection_stage TEXT,
        rejection_reason TEXT,
        raw_payload TEXT
    )
    """)
    
    # 2. Simulated trades & outcomes table
    c.execute("""
    CREATE TABLE IF NOT EXISTS shadow_outcomes (
        trade_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        is_reversal INTEGER DEFAULT 0,
        entry_time TEXT NOT NULL,
        entry_price REAL NOT NULL,
        simulated_fill_price REAL NOT NULL,
        slippage_bps REAL NOT NULL,
        fees_bps REAL NOT NULL,
        current_mfe_pct REAL DEFAULT 0.0,
        current_mae_pct REAL DEFAULT 0.0,
        partial_harvest_filled INTEGER DEFAULT 0,
        partial_harvest_price REAL,
        stop_advanced_to_be INTEGER DEFAULT 0,
        exit_time TEXT,
        exit_price REAL,
        exit_reason TEXT,
        realized_r REAL,
        is_win INTEGER,
        duration_sec INTEGER,
        status TEXT NOT NULL,
        FOREIGN KEY (candidate_id) REFERENCES shadow_candidates (candidate_id)
    )
    """)
    
    conn.commit()
    conn.close()

init_db()

class ShadowDB:
    def __init__(self):
        init_db()

    def _get_conn(self):
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert_candidate(self, cand_dict):
        conn = self._get_conn()
        c = conn.cursor()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        c.execute("""
        INSERT INTO shadow_candidates (
            candidate_id, symbol, direction, created_at, updated_at, state,
            market_state_verdict, mtf_coherence, me14, loss_veto_verdict,
            loss_probability, veto_threshold, supervisor_verdict, supervisor_updated_at,
            supervisor_rationale, risk_verdict, risk_updated_at, entry_type,
            entry_price, stop_floor_pct, stop_price, tp1_pct, tp1_price,
            protected_stop_pct, protected_stop_price, final_tp_price, sigma,
            reversal_eligible, reversal_triggered, reversal_reason, rejection_stage,
            rejection_reason, raw_payload
        ) VALUES (
            :candidate_id, :symbol, :direction, :created_at, :updated_at, :state,
            :market_state_verdict, :mtf_coherence, :me14, :loss_veto_verdict,
            :loss_probability, :veto_threshold, :supervisor_verdict, :supervisor_updated_at,
            :supervisor_rationale, :risk_verdict, :risk_updated_at, :entry_type,
            :entry_price, :stop_floor_pct, :stop_price, :tp1_pct, :tp1_price,
            :protected_stop_pct, :protected_stop_price, :final_tp_price, :sigma,
            :reversal_eligible, :reversal_triggered, :reversal_reason, :rejection_stage,
            :rejection_reason, :raw_payload
        ) ON CONFLICT(candidate_id) DO UPDATE SET
            updated_at = :updated_at,
            state = :state,
            market_state_verdict = COALESCE(:market_state_verdict, market_state_verdict),
            mtf_coherence = COALESCE(:mtf_coherence, mtf_coherence),
            me14 = COALESCE(:me14, me14),
            loss_veto_verdict = COALESCE(:loss_veto_verdict, loss_veto_verdict),
            loss_probability = COALESCE(:loss_probability, loss_probability),
            veto_threshold = COALESCE(:veto_threshold, veto_threshold),
            supervisor_verdict = COALESCE(:supervisor_verdict, supervisor_verdict),
            supervisor_updated_at = COALESCE(:supervisor_updated_at, supervisor_updated_at),
            supervisor_rationale = COALESCE(:supervisor_rationale, supervisor_rationale),
            risk_verdict = COALESCE(:risk_verdict, risk_verdict),
            risk_updated_at = COALESCE(:risk_updated_at, risk_updated_at),
            entry_type = COALESCE(:entry_type, entry_type),
            entry_price = COALESCE(:entry_price, entry_price),
            stop_floor_pct = COALESCE(:stop_floor_pct, stop_floor_pct),
            stop_price = COALESCE(:stop_price, stop_price),
            tp1_pct = COALESCE(:tp1_pct, tp1_pct),
            tp1_price = COALESCE(:tp1_price, tp1_price),
            protected_stop_pct = COALESCE(:protected_stop_pct, protected_stop_pct),
            protected_stop_price = COALESCE(:protected_stop_price, protected_stop_price),
            final_tp_price = COALESCE(:final_tp_price, final_tp_price),
            sigma = COALESCE(:sigma, sigma),
            reversal_eligible = COALESCE(:reversal_eligible, reversal_eligible),
            reversal_triggered = COALESCE(:reversal_triggered, reversal_triggered),
            reversal_reason = COALESCE(:reversal_reason, reversal_reason),
            rejection_stage = COALESCE(:rejection_stage, rejection_stage),
            rejection_reason = COALESCE(:rejection_reason, rejection_reason),
            raw_payload = COALESCE(:raw_payload, raw_payload)
        """, cand_dict)
        conn.commit()
        conn.close()
        self.export_live_snapshot()

    def upsert_outcome(self, outcome_dict):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
        INSERT INTO shadow_outcomes (
            trade_id, candidate_id, symbol, direction, is_reversal, entry_time,
            entry_price, simulated_fill_price, slippage_bps, fees_bps,
            current_mfe_pct, current_mae_pct, partial_harvest_filled,
            partial_harvest_price, stop_advanced_to_be, exit_time, exit_price,
            exit_reason, realized_r, is_win, duration_sec, status
        ) VALUES (
            :trade_id, :candidate_id, :symbol, :direction, :is_reversal, :entry_time,
            :entry_price, :simulated_fill_price, :slippage_bps, :fees_bps,
            :current_mfe_pct, :current_mae_pct, :partial_harvest_filled,
            :partial_harvest_price, :stop_advanced_to_be, :exit_time, :exit_price,
            :exit_reason, :realized_r, :is_win, :duration_sec, :status
        ) ON CONFLICT(trade_id) DO UPDATE SET
            current_mfe_pct = :current_mfe_pct,
            current_mae_pct = :current_mae_pct,
            partial_harvest_filled = :partial_harvest_filled,
            partial_harvest_price = :partial_harvest_price,
            stop_advanced_to_be = :stop_advanced_to_be,
            exit_time = :exit_time,
            exit_price = :exit_price,
            exit_reason = :exit_reason,
            realized_r = :realized_r,
            is_win = :is_win,
            duration_sec = :duration_sec,
            status = :status
        """, outcome_dict)
        conn.commit()
        conn.close()
        self.export_live_snapshot()

    def get_recent_candidates(self, limit=50):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM shadow_candidates ORDER BY updated_at DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    def get_shadow_performance(self):
        conn = self._get_conn()
        c = conn.cursor()
        
        # Outcomes summary
        c.execute("SELECT COUNT(*) as total, SUM(CASE WHEN is_win=1 THEN 1 ELSE 0 END) as wins, SUM(realized_r) as total_r, AVG(realized_r) as ev_r, AVG(slippage_bps) as avg_slip FROM shadow_outcomes WHERE status='CLOSED'")
        row = dict(c.fetchone() or {})
        
        tot = row.get("total") or 0
        wins = row.get("wins") or 0
        wr = round((wins / tot * 100.0), 1) if tot > 0 else 0.0
        ev = round(row.get("ev_r") or 0.0, 3)
        tot_r = round(row.get("total_r") or 0.0, 1)
        avg_slip = round(row.get("avg_slip") or 4.0, 1)
        
        # Veto & pipeline counts
        c.execute("SELECT COUNT(*) FROM shadow_candidates")
        total_candidates = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM shadow_candidates WHERE market_state_verdict != 'PASS'")
        market_state_veto_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM shadow_candidates WHERE loss_veto_verdict='VETO'")
        vetoed_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM shadow_candidates WHERE supervisor_verdict='VETO'")
        supervisor_veto_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM shadow_candidates WHERE risk_verdict='REJECT'")
        risk_reject_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM shadow_outcomes WHERE partial_harvest_filled=1")
        harvest_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM shadow_outcomes WHERE is_reversal=1")
        reversal_count = c.fetchone()[0]

        # Total rejected candidates across all stages
        c.execute("SELECT COUNT(*) FROM shadow_candidates WHERE state LIKE 'VETO%' OR state LIKE '%REJECT%' OR state LIKE '%FAIL%'")
        total_rejected = c.fetchone()[0]

        # Check start time and days elapsed
        c.execute("SELECT MIN(created_at) FROM shadow_candidates")
        first_row = c.fetchone()[0]
        days_elapsed = 0.0
        if first_row:
            try:
                first_dt = datetime.strptime(first_row, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                days_elapsed = (datetime.now(timezone.utc) - first_dt).total_seconds() / 86400.0
            except Exception:
                pass

        conn.close()
        
        rej_rate = round((total_rejected / max(1, total_candidates)) * 100.0, 1)

        # Graduation evaluation
        target_trades = 100
        target_candidates = 500
        target_days = 5.0
        target_ev = 0.08
        target_wr = 60.0

        trades_ok = tot >= target_trades
        candidates_ok = total_candidates >= target_candidates
        days_ok = days_elapsed >= target_days
        ev_ok = (ev >= target_ev) if tot >= 20 else False
        wr_ok = (wr >= target_wr) if tot >= 20 else False

        criteria_met = sum([1 for ok in [trades_ok, candidates_ok, days_ok, ev_ok, wr_ok] if ok])
        is_graduated = bool(trades_ok and candidates_ok and days_ok and ev_ok and wr_ok)

        # Progress percentages (capped at 100)
        p_trades = min(100.0, (tot / target_trades) * 100.0)
        p_cand = min(100.0, (total_candidates / target_candidates) * 100.0)
        p_days = min(100.0, (days_elapsed / target_days) * 100.0)
        overall_progress = round((p_trades * 0.50 + p_cand * 0.25 + p_days * 0.25), 1)
        
        return {
            "mode": "V4_SHADOW_ENGINE",
            "active": True,
            "total_candidates": total_candidates,
            "simulated_trades_closed": tot,
            "wins": wins,
            "losses": tot - wins,
            "win_rate": wr,
            "net_ev_r": ev,
            "total_realized_r": tot_r,
            "average_slippage_bps": avg_slip,
            "vetoed_by_market_state": market_state_veto_count,
            "vetoed_by_loss_gate": vetoed_count,
            "vetoed_by_supervisor": supervisor_veto_count,
            "rejected_by_risk_engine": risk_reject_count,
            "total_rejection_rate_pct": rej_rate,
            "harvest_triggers": harvest_count,
            "reversal_executions": reversal_count,
            "circuit_breakers_active": True,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "graduation": {
                "is_graduated": is_graduated,
                "status": "READY_FOR_PAPER_EXECUTION" if is_graduated else "COLLECTING_LIVE_DATA",
                "overall_progress_pct": overall_progress,
                "criteria_met_count": criteria_met,
                "total_criteria": 5,
                "checklist": {
                    "trades": {"current": tot, "target": target_trades, "passed": trades_ok, "pct": round(p_trades, 1)},
                    "candidates": {"current": total_candidates, "target": target_candidates, "passed": candidates_ok, "pct": round(p_cand, 1)},
                    "days": {"current": round(days_elapsed, 2), "target": target_days, "passed": days_ok, "pct": round(p_days, 1)},
                    "ev": {"current": ev, "target": target_ev, "passed": ev_ok},
                    "win_rate": {"current": wr, "target": target_wr, "passed": wr_ok}
                }
            }
        }

    def export_live_snapshot(self):
        try:
            candidates = self.get_recent_candidates(limit=30)
            perf = self.get_shadow_performance()
            snapshot = {
                "performance": perf,
                "candidates": candidates,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            }
            tmp = SNAPSHOT_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
            os.replace(tmp, SNAPSHOT_PATH)
        except Exception:
            pass

shadow_db = ShadowDB()

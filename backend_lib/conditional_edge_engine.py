"""
Conditional Edge Engine (CEE)
Replaces simple scalar scores with empirical statistical outcome distributions.
Extracts multi-dimensional market state vectors and searches historical episodes for:
- P(win)
- P(TP1 / +2.20%)
- P(TP2 / +4.00%)
- P(Stop / -1.50%)
- Median MFE & MAE
- Expected Net R (E[R])
"""

import os
import sys
import json
import time
import math
import sqlite3
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'market_knowledge.db')
SL_TZ = timezone(timedelta(hours=5, minutes=30))

def get_market_session(dt=None):
    """Determines active major market session in UTC."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    hour = dt.hour
    if 0 <= hour < 7:
        return "ASIA"
    elif 7 <= hour < 12:
        return "LONDON"
    elif 12 <= hour < 17:
        return "NY_OVERLAP"
    elif 17 <= hour < 21:
        return "NY_AFTERNOON"
    else:
        return "PACIFIC_TRANSITION"

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]['high']
        l = candles[i]['low']
        prev_c = candles[i-1]['close']
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    return sum(trs[-period:]) / period

class ConditionalEdgeEngine:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trade_state_vectors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        regime TEXT,
                        trend_strength REAL,
                        volatility_atr_pct REAL,
                        session TEXT,
                        btc_regime TEXT,
                        btc_5m_pct REAL,
                        runway_to_res_pct REAL,
                        dist_to_ema21_pct REAL,
                        bounce_rate_at_level REAL,
                        winner_avg_mfe REAL,
                        pnl_net REAL,
                        pnl_pct REAL,
                        mfe_pct REAL,
                        mae_pct REAL,
                        realized_r REAL,
                        exit_reason TEXT,
                        recorded_at INTEGER NOT NULL
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_tsv_sym ON trade_state_vectors(symbol)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_tsv_regime ON trade_state_vectors(regime)")
                conn.commit()
        except Exception as e:
            print(f"[CEE Init Error]: {e}")

    def extract_state_vector(self, candidate, sr_data=None, btc_macro=None, candlestick_reaction=None, track_record=None):
        """Constructs a 12-dimensional state vector from market telemetry."""
        sym = candidate.get('symbol', 'UNKNOWN')
        direction = candidate.get('direction', 'BUY')
        cur_price = candidate.get('price', 0.0)
        
        # S/R Runway & Confluence
        sr = sr_data or {}
        runway_pct = sr.get('runway_to_res_pct', 3.0)
        dist_ema21 = sr.get('dist_to_sup_pct', 0.8)
        
        # BTC Macro
        btc = btc_macro or {}
        btc_reg = btc.get('regime', 'CONSOLIDATING')
        btc_5m = btc.get('btc_chg_5m', 0.0)
        
        # Historical Level Reaction
        cr = candlestick_reaction or {}
        bounce_rate = cr.get('bounce_rate_pct', 50.0)
        
        # Coin Track Record
        tr = track_record or {}
        win_mfe = tr.get('win_mfe_avg', 0.75)
        
        session = get_market_session()
        
        # Determine regime
        regime = "VALUE_PULLBACK"
        if runway_pct >= 4.0 and btc_5m > 0.10:
            regime = "TREND_RUNNER"
        elif runway_pct < 2.0:
            regime = "RESISTANCE_COMPRESSION"
        elif abs(btc_5m) < 0.05:
            regime = "RANGE_CONSOLIDATION"

        vector = {
            "symbol": sym,
            "direction": direction,
            "entry_price": cur_price,
            "regime": regime,
            "session": session,
            "btc_regime": btc_reg,
            "btc_5m_pct": round(btc_5m, 3),
            "runway_to_res_pct": round(runway_pct, 2),
            "dist_to_ema21_pct": round(dist_ema21, 2),
            "bounce_rate_at_level": round(bounce_rate, 1),
            "winner_avg_mfe": round(win_mfe, 2),
            "recorded_at": int(time.time())
        }
        return vector

    def compute_conditional_edge(self, state_vector):
        """
        Queries historical trade episodes to find comparable states and computes
        empirical distribution: P(win), P(TP1), P(TP2), Median MFE, Expected R.
        """
        sym = state_vector.get('symbol')
        cur_p = state_vector.get('entry_price', 1.0)
        regime = state_vector.get('regime', 'VALUE_PULLBACK')
        
        comparable_trades = []
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                
                # 1. Query same coin episodes
                cur.execute("""
                    SELECT id, symbol, direction, entry_price, exit_price, pnl_net, pnl_pct, mfe_pct, mae_pct, exit_reason
                    FROM trade_episodes
                    WHERE symbol = ?
                    ORDER BY id DESC
                """, (sym,))
                rows = cur.fetchall()
                for r in rows:
                    pnl = r['pnl_net'] or 0.0
                    pnl_p = r['pnl_pct'] or 0.0
                    mfe = r['mfe_pct'] or 0.0
                    mae = r['mae_pct'] or 0.0
                    
                    # Convert to R-multiple assuming 1.50% standard risk unit
                    risk_pct = 1.50
                    # Clip realized_r to realistic bounds [-1.2R, +5.0R] so outliers don't skew expectancy
                    raw_r = pnl_p / risk_pct
                    realized_r = round(max(-1.25, min(5.0, raw_r)), 2)
                    mfe_r = round(max(0.0, min(6.0, mfe / risk_pct)), 2)
                    mae_r = round(max(-2.0, min(0.0, mae / risk_pct)), 2)
                    
                    comparable_trades.append({
                        "id": r['id'],
                        "pnl_net": pnl,
                        "pnl_pct": pnl_p,
                        "mfe_pct": mfe,
                        "mae_pct": mae,
                        "realized_r": realized_r,
                        "mfe_r": mfe_r,
                        "mae_r": mae_r,
                        "exit_reason": r['exit_reason']
                    })
        except Exception as e:
            print(f"[CEE Query Error]: {e}")

        tot = len(comparable_trades)
        if tot == 0:
            return {
                "comparable_sample_size": 0,
                "win_probability_pct": 50.0,
                "tp1_probability_pct": 35.0,
                "tp2_probability_pct": 20.0,
                "stop_probability_pct": 50.0,
                "median_mfe_pct": 0.80,
                "median_mae_pct": -0.40,
                "median_mfe_r": 0.53,
                "median_mae_r": -0.27,
                "expected_r": 0.05,
                "confidence": "LOW (Cold start: No prior episodes on coin)",
                "edge_verdict": "NEUTRAL"
            }

        wins = [t for t in comparable_trades if t['pnl_net'] > 0]
        losses = [t for t in comparable_trades if t['pnl_net'] <= 0]
        
        win_prob = round((len(wins) / tot) * 100.0, 1)
        stop_prob = round(100.0 - win_prob, 1)
        
        # P(TP1 / +2.20%) and P(TP2 / +4.00%) based on MFE
        tp1_hits = [t for t in comparable_trades if t['mfe_pct'] >= 2.20]
        tp2_hits = [t for t in comparable_trades if t['mfe_pct'] >= 4.00]
        
        p_tp1 = round((len(tp1_hits) / tot) * 100.0, 1)
        p_tp2 = round((len(tp2_hits) / tot) * 100.0, 1)
        
        # Median MFE and MAE
        mfes = sorted(t['mfe_pct'] for t in comparable_trades)
        maes = sorted(t['mae_pct'] for t in comparable_trades)
        median_mfe = round(mfes[len(mfes) // 2], 2)
        median_mae = round(maes[len(maes) // 2], 2)
        
        # Expected R: Deduct Bybit Taker Fees & Slippage (0.150% roundtrip)
        risk_pct = 1.50
        friction_r = round(0.150 / risk_pct, 3)  # ~0.10R friction per roundtrip trade
        
        r_list = [t['realized_r'] for t in comparable_trades]
        win_rs = [t['realized_r'] for t in wins]
        loss_rs = [abs(t['realized_r']) for t in losses]
        
        avg_win_r = sum(win_rs) / len(win_rs) if win_rs else 1.5
        avg_loss_r = sum(loss_rs) / len(loss_rs) if loss_rs else 1.0
        
        gross_expected_r = round(((win_prob / 100.0) * avg_win_r) - ((stop_prob / 100.0) * avg_loss_r), 3)
        net_expected_r = round(gross_expected_r - friction_r, 3)
        
        confidence = "HIGH" if tot >= 40 else ("MODERATE" if tot >= 15 else "LOW")
        
        # Institutional EV Hurdle: Minimum +0.35R Net after fees
        MIN_EV_HURDLE = 0.35
        if net_expected_r >= MIN_EV_HURDLE and win_prob >= 35.0:
            edge_verdict = "POSITIVE_ASYMMETRIC_EDGE"
            ev_approved = True
        elif net_expected_r >= 0.15:
            edge_verdict = "MARGINAL_EDGE_BELOW_HURDLE"
            ev_approved = False
        elif net_expected_r > 0.0:
            edge_verdict = "SUBPAR_EV_FEE_BLEED_RISK"
            ev_approved = False
        else:
            edge_verdict = "NEGATIVE_EDGE_CHURN_RISK"
            ev_approved = False

        return {
            "comparable_sample_size": tot,
            "win_probability_pct": win_prob,
            "tp1_probability_pct": p_tp1,
            "tp2_probability_pct": p_tp2,
            "stop_probability_pct": stop_prob,
            "median_mfe_pct": median_mfe,
            "median_mae_pct": median_mae,
            "median_mfe_r": round(median_mfe / 1.5, 2),
            "median_mae_r": round(median_mae / 1.5, 2),
            "gross_expected_r": gross_expected_r,
            "fee_friction_r": friction_r,
            "expected_r": net_expected_r,
            "net_expected_r": net_expected_r,
            "ev_hurdle_met": ev_approved,
            "min_ev_hurdle": MIN_EV_HURDLE,
            "avg_win_r": round(avg_win_r, 2),
            "avg_loss_r": round(avg_loss_r, 2),
            "confidence": f"{confidence} (N={tot} episodes)",
            "edge_verdict": edge_verdict
        }

    def record_completed_trade(self, state_vector, outcome):
        """Stores a completed trade state vector with realized MFE, MAE, and R."""
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                pnl_p = outcome.get('pnl_pct', 0.0)
                risk_pct = 1.50
                realized_r = round(pnl_p / risk_pct, 2)
                
                cur.execute("""
                    INSERT INTO trade_state_vectors (
                        symbol, direction, entry_price, regime, trend_strength,
                        volatility_atr_pct, session, btc_regime, btc_5m_pct,
                        runway_to_res_pct, dist_to_ema21_pct, bounce_rate_at_level,
                        winner_avg_mfe, pnl_net, pnl_pct, mfe_pct, mae_pct,
                        realized_r, exit_reason, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    state_vector.get('symbol'),
                    state_vector.get('direction', 'BUY'),
                    state_vector.get('entry_price', 0.0),
                    state_vector.get('regime'),
                    0.0,
                    0.0,
                    state_vector.get('session'),
                    state_vector.get('btc_regime'),
                    state_vector.get('btc_5m_pct'),
                    state_vector.get('runway_to_res_pct'),
                    state_vector.get('dist_to_ema21_pct'),
                    state_vector.get('bounce_rate_at_level'),
                    state_vector.get('winner_avg_mfe'),
                    outcome.get('pnl_net', 0.0),
                    pnl_p,
                    outcome.get('mfe_pct', 0.0),
                    outcome.get('mae_pct', 0.0),
                    realized_r,
                    outcome.get('exit_reason', 'EXIT'),
                    int(time.time())
                ))
                conn.commit()
        except Exception as e:
            print(f"[CEE Record Error]: {e}")

cee = ConditionalEdgeEngine()

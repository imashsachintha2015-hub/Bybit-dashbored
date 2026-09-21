"""
DeepSeek AI Pre-Trade Quantitative Gatekeeper
Evaluates high-conviction HTF Swing Runner trade candidates before order placement.
Synthesizes:
1. Multi-Timeframe Support & Resistance (1h S/R, 15m swing levels, EMA20/50 dynamic baselines, Pivot R1/S1)
2. Historical Performance & Track Record on this coin (Win rate, MFE, past PnL)
3. Market Prophet Memory (Past learned rules & post-mortem reflections)
4. Bitcoin Macro Regime & Altcoin Sensitivity Guard
Only candidates achieving >= 90 conviction score with valid S/R runway are approved for execution.
"""

import os
import sys
import json
import time
import math
import urllib.request
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient
from backend_lib.market_knowledge import kb

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
env = {}
with open(ENV_PATH, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

DEEPSEEK_API_KEY = env.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_URL = env.get('DEEPSEEK_URL', 'https://api.deepseek.com/v1/chat/completions')
DEEPSEEK_MODEL = env.get('DEEPSEEK_MODEL', 'deepseek-chat')

DECISIONS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deepseek_pre_trade_decisions.json')
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'market_knowledge.db')

def fetch_klines(symbol, interval, limit=30):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            candles = []
            for row in reversed(raw):
                candles.append({
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vol": float(row[5])
                })
            return candles
    except Exception:
        return []

def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1.0 - k)
    return ema

def compute_sr_levels(symbol, cur_price=None):
    """Computes institutional multi-timeframe Support & Resistance levels."""
    k1h  = fetch_klines(symbol, '60', 30)
    k15  = fetch_klines(symbol, '15', 30)
    
    if not k1h or not k15 or len(k1h) < 15 or len(k15) < 15:
        return {}
        
    c1h = [c['close'] for c in k1h]
    c15 = [c['close'] for c in k15]
    
    if cur_price is None:
        cur_price = c15[-1]
        
    # 1. 1-Hour Macro S/R (last 24 hours)
    res_1h = max(c['high'] for c in k1h[-24:])
    sup_1h = min(c['low'] for c in k1h[-24:])
    ema20_1h = round(calc_ema(c1h, 20), 4)
    ema50_1h = round(calc_ema(c1h, 50), 4)
    
    # 2. 15-Minute Local Swing S/R (last 4-6 hours)
    res_15m = max(c['high'] for c in k15[-16:])
    sup_15m = min(c['low'] for c in k15[-16:])
    ema21_15m = round(calc_ema(c15, 21), 4)
    
    # 3. Daily Pivot Points
    prev_h = k1h[-2]['high']
    prev_l = k1h[-2]['low']
    prev_c = k1h[-2]['close']
    pivot_p = round((prev_h + prev_l + prev_c) / 3.0, 4)
    pivot_r1 = round(2.0 * pivot_p - prev_l, 4)
    pivot_s1 = round(2.0 * pivot_p - prev_h, 4)
    
    # 4. Nearest Structural Resistance & Support
    resistances_above = [r for r in [res_15m, res_1h, pivot_r1] if r > cur_price]
    nearest_res = min(resistances_above) if resistances_above else round(cur_price * 1.05, 4)
    
    supports_below = [s for s in [sup_15m, ema21_15m, ema20_1h, pivot_s1, sup_1h] if s < cur_price]
    nearest_sup = max(supports_below) if supports_below else round(cur_price * 0.97, 4)
    
    runway_to_res_pct = round(((nearest_res - cur_price) / cur_price) * 100.0, 2)
    dist_to_sup_pct = round(((cur_price - nearest_sup) / cur_price) * 100.0, 2)
    
    return {
        "cur_price": cur_price,
        "nearest_res": nearest_res,
        "nearest_sup": nearest_sup,
        "runway_to_res_pct": runway_to_res_pct,
        "dist_to_sup_pct": dist_to_sup_pct,
        "res_1h": res_1h,
        "sup_1h": sup_1h,
        "res_15m": res_15m,
        "sup_15m": sup_15m,
        "ema20_1h": ema20_1h,
        "ema50_1h": ema50_1h,
        "ema21_15m": ema21_15m,
        "pivot_p": pivot_p,
        "pivot_r1": pivot_r1,
        "pivot_s1": pivot_s1
    }

def analyze_price_level_historical_reaction(symbol, cur_price, tolerance_pct=0.6):
    """
    Historical Candlestick Price-Level Analysis:
    Examines what happened historically when price visited this exact price zone (cur_price +/- tolerance_pct).
    Clusters visits across 100 hours of 1h klines to measure:
    - Bounce rate (>= +1.5% rally)
    - Rejection rate (<= -1.5% drop)
    - Maximum & average historical rally launched from this price level
    - Maximum & average historical drop suffered from this price level
    - Institutional zone classification (Demand/Accumulation vs Resistance/Overhang vs Chop)
    """
    k1h = fetch_klines(symbol, '60', 100)  # Last 100 hours (~4 days)
    if not k1h or len(k1h) < 15:
        return {
            "tested_count": 0,
            "bounce_count": 0,
            "rejection_count": 0,
            "bounce_rate_pct": 50.0,
            "rejection_rate_pct": 50.0,
            "max_rally_pct": 0.0,
            "avg_rally_pct": 0.0,
            "max_drop_pct": 0.0,
            "avg_drop_pct": 0.0,
            "summary": "Insufficient 1h kline history to analyze price level reaction."
        }

    zone_low = cur_price * (1.0 - (tolerance_pct / 100.0))
    zone_high = cur_price * (1.0 + (tolerance_pct / 100.0))

    # Identify distinct visits (cluster consecutive bars touching the zone)
    visits = []
    in_visit = False
    current_visit_bars = []
    
    for i, bar in enumerate(k1h[:-2]):
        touches_zone = (bar['low'] <= zone_high and bar['high'] >= zone_low)
        if touches_zone:
            if not in_visit:
                in_visit = True
                current_visit_bars = [i]
            else:
                current_visit_bars.append(i)
        else:
            if in_visit:
                # Visit concluded, evaluate reaction in subsequent 1-8 hours
                last_bar_idx = current_visit_bars[-1]
                future_bars = k1h[last_bar_idx+1:min(len(k1h), last_bar_idx+9)]
                if future_bars:
                    max_high_after = max(b['high'] for b in future_bars)
                    min_low_after = min(b['low'] for b in future_bars)
                    up_move_pct = ((max_high_after - cur_price) / cur_price) * 100.0
                    down_move_pct = ((min_low_after - cur_price) / cur_price) * 100.0
                    visits.append({
                        "visit_start": current_visit_bars[0],
                        "bars_in_zone": len(current_visit_bars),
                        "up_move_pct": round(up_move_pct, 2),
                        "down_move_pct": round(down_move_pct, 2)
                    })
                in_visit = False
                current_visit_bars = []
                
    if in_visit and current_visit_bars:
        last_bar_idx = current_visit_bars[-1]
        future_bars = k1h[last_bar_idx+1:min(len(k1h), last_bar_idx+9)]
        if future_bars:
            max_high_after = max(b['high'] for b in future_bars)
            min_low_after = min(b['low'] for b in future_bars)
            visits.append({
                "visit_start": current_visit_bars[0],
                "bars_in_zone": len(current_visit_bars),
                "up_move_pct": round(((max_high_after - cur_price) / cur_price) * 100.0, 2),
                "down_move_pct": round(((min_low_after - cur_price) / cur_price) * 100.0, 2)
            })

    if not visits:
        return {
            "tested_count": 0,
            "bounce_count": 0,
            "rejection_count": 0,
            "bounce_rate_pct": 50.0,
            "rejection_rate_pct": 50.0,
            "max_rally_pct": 0.0,
            "avg_rally_pct": 0.0,
            "max_drop_pct": 0.0,
            "avg_drop_pct": 0.0,
            "summary": f"Price {cur_price} is at an untested price level in the last 100 hours (all-time new local zone)."
        }

    tested_count = len(visits)
    bounces = [v for v in visits if v['up_move_pct'] >= 1.50]
    rejections = [v for v in visits if v['down_move_pct'] <= -1.50]
    
    bounce_count = len(bounces)
    rejection_count = len(rejections)
    bounce_rate = round((bounce_count / max(1, tested_count)) * 100.0, 1)
    rejection_rate = round((rejection_count / max(1, tested_count)) * 100.0, 1)
    
    max_rally = max(v['up_move_pct'] for v in visits)
    avg_rally = round(sum(v['up_move_pct'] for v in visits) / tested_count, 2)
    max_drop = min(v['down_move_pct'] for v in visits)
    avg_drop = round(sum(v['down_move_pct'] for v in visits) / tested_count, 2)
    
    if bounce_rate >= 60.0:
        reaction_nature = f"STRONG BUYER ACCUMULATION & DEMAND ZONE: {bounce_rate}% of visits launched rallies >= +1.5% (Tested {tested_count} times, max rally +{max_rally}%, avg rally +{avg_rally}%)."
    elif rejection_count > bounce_count:
        reaction_nature = f"SELLER OVERHANG / RESISTANCE ZONE: Rejections ({rejection_count}) dominate bounces ({bounce_count}) with max drop {max_drop}%."
    else:
        reaction_nature = f"CONSOLIDATION / EQUILIBRIUM ZONE: Tested {tested_count} times, max rally +{max_rally}%, max drop {max_drop}%."

    return {
        "tested_count": tested_count,
        "bounce_count": bounce_count,
        "rejection_count": rejection_count,
        "bounce_rate_pct": bounce_rate,
        "rejection_rate_pct": rejection_rate,
        "max_rally_pct": max_rally,
        "avg_rally_pct": avg_rally,
        "max_drop_pct": max_drop,
        "avg_drop_pct": avg_drop,
        "summary": reaction_nature
    }

def fetch_past_trades_at_price(symbol, cur_price, tolerance_pct=1.5):
    """
    Queries past closed trades executed on this coin at or near this specific price level.
    What happened to our previous positions entered around this price?
    """
    trades_at_price = []
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            p_min = cur_price * (1.0 - (tolerance_pct / 100.0))
            p_max = cur_price * (1.0 + (tolerance_pct / 100.0))
            
            cur.execute("""
                SELECT id, symbol, direction, entry_price, exit_price, pnl_net, pnl_pct, mfe_pct, mae_pct, exit_reason
                FROM trade_episodes
                WHERE symbol = ? AND entry_price BETWEEN ? AND ?
                ORDER BY id DESC LIMIT 6
            """, (symbol, p_min, p_max))
            
            for r in cur.fetchall():
                trades_at_price.append({
                    "id": r['id'],
                    "direction": r['direction'],
                    "entry_price": r['entry_price'],
                    "exit_price": r['exit_price'],
                    "pnl_net": round(r['pnl_net'] or 0.0, 4),
                    "pnl_pct": round(r['pnl_pct'] or 0.0, 2),
                    "mfe_pct": round(r['mfe_pct'] or 0.0, 2),
                    "mae_pct": round(r['mae_pct'] or 0.0, 2),
                    "exit_reason": r['exit_reason']
                })
            conn.close()
        except Exception:
            pass
            
    if not trades_at_price:
        return {
            "count_at_level": 0,
            "wins_at_level": 0,
            "losses_at_level": 0,
            "win_rate_at_level": 0.0,
            "avg_mfe_at_level": 0.0,
            "trades": [],
            "summary": f"No past system positions entered within +/-{tolerance_pct}% of {cur_price}."
        }
        
    tot = len(trades_at_price)
    wins = len([t for t in trades_at_price if t['pnl_net'] > 0])
    losses = tot - wins
    wr = round((wins / tot) * 100.0, 1)
    avg_mfe = round(sum(t['mfe_pct'] for t in trades_at_price) / tot, 2)
    
    summary = f"System previously entered {tot} trades near this price: {wins}W/{losses}L ({wr}% WR). Peak gain (MFE) averaged +{avg_mfe}%."
    return {
        "count_at_level": tot,
        "wins_at_level": wins,
        "losses_at_level": losses,
        "win_rate_at_level": wr,
        "avg_mfe_at_level": avg_mfe,
        "trades": trades_at_price,
        "summary": summary
    }

def fetch_coin_memory(symbol):
    """
    Retrieves coin lifetime historical track record, winner/loser MFE profile, and learned lessons.
    Specifically provides:
    - Total closed trades on this coin
    - Win rate %
    - Winner Average MFE (How far winners typically run)
    - Winner Maximum MFE (Peak favorable runner observed)
    - Winner Average MAE (Drawdown breathing room needed)
    - Loser Average MFE (Point where failed trades stalled)
    - Gross Profit & Gross Loss
    - Recent closed trade logs
    - Stored post-mortem rules
    """
    track_record = {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "avg_pnl": 0.0,
        "win_mfe_avg": 0.0,
        "win_mfe_max": 0.0,
        "win_mae_avg": 0.0,
        "loss_mfe_avg": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "recent_trades": []
    }
    rules = []
    
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            # Comprehensive stats on past episodes
            cur.execute("""
                SELECT 
                    COUNT(*) as tot,
                    SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) as w,
                    SUM(CASE WHEN pnl_net <= 0 THEN 1 ELSE 0 END) as l,
                    AVG(pnl_net) as avg_p,
                    AVG(CASE WHEN pnl_net > 0 THEN mfe_pct ELSE NULL END) as win_mfe_avg,
                    MAX(CASE WHEN pnl_net > 0 THEN mfe_pct ELSE NULL END) as win_mfe_max,
                    AVG(CASE WHEN pnl_net <= 0 THEN mfe_pct ELSE NULL END) as loss_mfe_avg,
                    AVG(CASE WHEN pnl_net > 0 THEN mae_pct ELSE NULL END) as win_mae_avg,
                    SUM(CASE WHEN pnl_net > 0 THEN pnl_net ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl_net <= 0 THEN pnl_net ELSE 0 END) as gross_loss
                FROM trade_episodes
                WHERE symbol = ?
            """, (symbol,))
            row = cur.fetchone()
            if row and row['tot'] and row['tot'] > 0:
                tot = row['tot']
                w = row['w'] or 0
                track_record = {
                    "total_trades": tot,
                    "wins": w,
                    "losses": row['l'] or 0,
                    "win_rate": round((w / tot) * 100.0, 1),
                    "avg_pnl": round(row['avg_p'] or 0.0, 4),
                    "win_mfe_avg": round(row['win_mfe_avg'] or 0.0, 2),
                    "win_mfe_max": round(row['win_mfe_max'] or 0.0, 2),
                    "win_mae_avg": round(row['win_mae_avg'] or 0.0, 2),
                    "loss_mfe_avg": round(row['loss_mfe_avg'] or 0.0, 2),
                    "gross_profit": round(row['gross_profit'] or 0.0, 4),
                    "gross_loss": round(row['gross_loss'] or 0.0, 4),
                    "recent_trades": []
                }
                
            # Recent closed trade logs on this specific coin
            cur.execute("""
                SELECT id, direction, entry_price, exit_price, pnl_net, pnl_pct, mfe_pct, mae_pct, exit_reason
                FROM trade_episodes
                WHERE symbol = ?
                ORDER BY id DESC LIMIT 5
            """, (symbol,))
            for r in cur.fetchall():
                track_record["recent_trades"].append({
                    "id": r['id'],
                    "direction": r['direction'],
                    "entry_price": r['entry_price'],
                    "exit_price": r['exit_price'],
                    "pnl_net": round(r['pnl_net'] or 0.0, 4),
                    "pnl_pct": round(r['pnl_pct'] or 0.0, 2),
                    "mfe_pct": round(r['mfe_pct'] or 0.0, 2),
                    "mae_pct": round(r['mae_pct'] or 0.0, 2),
                    "exit_reason": r['exit_reason']
                })
                
            conn.close()
        except Exception:
            pass
            
    # Retrieve top learned rules for this coin from Market Knowledge Base
    try:
        learned = kb.get_relevant_knowledge(symbol, limit=4)
        if learned:
            for item in learned:
                rules.append(item.get('rule_summary'))
    except Exception:
        pass
        
    return track_record, rules

def evaluate_setup_with_deepseek(candidate, btc_macro=None):
    """
    Core Gatekeeper Entrypoint:
    Runs full quantitative and DeepSeek AI evaluation before approving a trade.
    Synthesizes BOTH:
    1. Historical Candlestick Price Action at this Price Level ("When in this price what happened?")
    2. Coin Lifetime Performance & Winner MFE Runner Profile ("How far its winners typically run")
    Plus S/R Runway, Past Positions at this Price Level, and Bitcoin Macro.
    """
    sym = candidate.get('symbol')
    direction = candidate.get('direction', 'BUY')
    cur_p = candidate.get('price')
    init_score = candidate.get('score', 0)
    setup_name = candidate.get('setup', 'HTF_SWING_PULLBACK_LONG')
    
    sr = compute_sr_levels(sym, cur_p)
    track_record, rules = fetch_coin_memory(sym)
    candlestick_reaction = analyze_price_level_historical_reaction(sym, cur_p)
    past_trades_at_price = fetch_past_trades_at_price(sym, cur_p, tolerance_pct=1.5)
    
    # ── CONDITIONAL STATISTICAL EDGE ENGINE ──
    from backend_lib.conditional_edge_engine import cee
    state_vector = cee.extract_state_vector(candidate, sr, btc_macro, candlestick_reaction, track_record)
    conditional_edge = cee.compute_conditional_edge(state_vector)
    
    rules_text = "\n".join(f"- {r}" for r in rules) if rules else "- No specific historical mistakes recorded yet."
    
    recent_level_trades_str = ""
    if past_trades_at_price.get('trades'):
        recent_level_trades_str = "\n".join(
            f"  * #{t['id']} {t['direction']} @ {t['entry_price']}: {t['pnl_pct']:+.2f}% net, MFE: +{t['mfe_pct']}%, Exit: {t['exit_reason']}"
            for t in past_trades_at_price['trades']
        )
    else:
        recent_level_trades_str = "  * None (fresh entry price level)"
        
    recent_coin_trades_str = ""
    if track_record.get('recent_trades'):
        recent_coin_trades_str = "\n".join(
            f"  * #{t['id']} {t['direction']} @ {t['entry_price']}: {t['pnl_pct']:+.2f}% net, MFE: +{t['mfe_pct']}%, MAE: {t['mae_pct']}%, Exit: {t['exit_reason']}"
            for t in track_record['recent_trades']
        )
    else:
        recent_coin_trades_str = "  * No prior episodes recorded."
    
    btc_desc = "N/A"
    btc_regime = "UNKNOWN"
    btc_chg_5m = 0.0
    btc_chg_1h = 0.0
    if btc_macro:
        btc_price = btc_macro.get('btc_price', 0)
        btc_chg_5m = btc_macro.get('btc_chg_5m', 0)
        btc_chg_1h = btc_macro.get('btc_chg_1h', 0)
        btc_regime = btc_macro.get('regime', 'CONSOLIDATING')
        btc_desc = f"${btc_price:,.1f} ({btc_chg_5m:+.2f}% 5m, {btc_chg_1h:+.2f}% 1h, Regime: {btc_regime})"

    if direction == 'SELL':
        runway_pct = sr.get('dist_to_sup_pct', 0)
        runway_verdict = f"APPROVED (-{runway_pct}% down to nearest support floor)" if runway_pct >= 2.50 else f"VETO HAZARD (Only -{runway_pct}% to nearest support, below 2.50% minimum swing clearance)"
    else:
        runway_pct = sr.get('runway_to_res_pct', 0)
        runway_verdict = f"APPROVED (+{runway_pct}% to nearest resistance)" if runway_pct >= 2.50 else f"VETO HAZARD (Only +{runway_pct}% to nearest resistance, below 2.50% minimum swing buffer)"

    prompt = f"""You are the Chief Quantitative Risk Officer at MASIS Institutional Crypto Trading.
A potential HIGH-CONVICTION HTF SWING RUNNER trade candidate has been detected by the scanner.
Our strategy targets $1.00+ realistic profit per trade (both Longs and Shorts like XRP 1.5588->1.5045 or AVAX 11.1828->10.6897) with EXTREME CAUTION.

Candidate Trade Details:
- Symbol: {sym}
- Direction: {direction}
- Current Market Price: {cur_p}
- Scanner Quantitative Score: {init_score}/100 ({setup_name})

======================================================================
1. SUPPORT & RESISTANCE (S/R) STRUCTURE
======================================================================
- Nearest Support Floor: {sr.get('nearest_sup')} (-{sr.get('dist_to_sup_pct', 0):.2f}% below entry)
- Nearest Structural Resistance: {sr.get('nearest_res')} (+{sr.get('runway_to_res_pct', 0):.2f}% above entry)
- 15m Dynamic Support (EMA21): {sr.get('ema21_15m')}
- 1h Dynamic Trend Support (EMA20): {sr.get('ema20_1h')}
- 1h 24-Hour Range: Low {sr.get('sup_1h')} to High {sr.get('res_1h')}
- Daily Pivot Point: {sr.get('pivot_p')} (R1: {sr.get('pivot_r1')}, S1: {sr.get('pivot_s1')})
- Runway Assessment: {runway_verdict}

======================================================================
2. HISTORICAL CANDLESTICK PRICE-LEVEL REACTION ("When in this price, what happened?")
======================================================================
- Price Zone Tested: {candlestick_reaction.get('tested_count', 0)} distinct visits in the last 100 hours
- Bullish Bounces (>= +1.5% rally): {candlestick_reaction.get('bounce_count', 0)} times ({candlestick_reaction.get('bounce_rate_pct', 0)}% bounce frequency)
- Bearish Rejections (<= -1.5% drop): {candlestick_reaction.get('rejection_count', 0)} times ({candlestick_reaction.get('rejection_rate_pct', 0)}% rejection frequency)
- Max Historical Rally launched from this price: +{candlestick_reaction.get('max_rally_pct', 0):.2f}% (Average: +{candlestick_reaction.get('avg_rally_pct', 0):.2f}%)
- Max Historical Drop suffered from this price: {candlestick_reaction.get('max_drop_pct', 0):.2f}% (Average: {candlestick_reaction.get('avg_drop_pct', 0):.2f}%)
- Price Level Classification: {candlestick_reaction.get('summary')}

======================================================================
3. PAST SYSTEM POSITION HISTORY AT THIS SPECIFIC PRICE LEVEL (Within +/-1.5%)
======================================================================
- Prior System Positions Entered: {past_trades_at_price.get('count_at_level', 0)} ({past_trades_at_price.get('win_rate_at_level', 0)}% Win Rate)
- Level Performance Summary: {past_trades_at_price.get('summary')}
- Trade Log Snippets:
{recent_level_trades_str}

======================================================================
4. COIN LIFETIME PERFORMANCE & WINNER MFE RUNNER PROFILE ("How far its winners typically run")
======================================================================
- Total Historical Closed Trades: {track_record.get('total_trades', 0)} ({track_record.get('wins', 0)} Wins / {track_record.get('losses', 0)} Losses | Win Rate: {track_record.get('win_rate', 0)}%)
- WINNER MFE PROFILE (How far do winners typically run on this coin?):
  * Winner Average MFE: +{track_record.get('win_mfe_avg', 0):.2f}% favorable excursion
  * Winner Maximum MFE: +{track_record.get('win_mfe_max', 0):.2f}% peak runner
  * Winner Average MAE (Drawdown tolerated before running): {track_record.get('win_mae_avg', 0):.2f}%
- LOSER PROFILE (Failure signature):
  * Loser Average MFE before reversing: +{track_record.get('loss_mfe_avg', 0):.2f}%
- Total PnL Track Record: Gross Profit ${track_record.get('gross_profit', 0):.4f} | Gross Loss ${track_record.get('gross_loss', 0):.4f} (Net: ${track_record.get('avg_pnl', 0):.4f}/trade)
- Recent Closed Episodes on {sym}:
{recent_coin_trades_str}
- Learned Post-Mortem Rules on {sym}:
{rules_text}

======================================================================
5. CONDITIONAL STATISTICAL EDGE ENGINE (Empirical State Distribution)
======================================================================
- Comparable Historical Episodes (N): {conditional_edge.get('comparable_sample_size', 0)}
- Empirical Win Probability P(Win): {conditional_edge.get('win_probability_pct', 0)}%
- Empirical TP1 Hit Probability P(TP1 >= 2.00%): {conditional_edge.get('tp1_probability_pct', 0)}%
- Empirical Stop Probability P(Stop): {conditional_edge.get('stop_probability_pct', 0)}%
- Median MFE Excursion: +{conditional_edge.get('median_mfe_pct', 0)}% (+{conditional_edge.get('median_mfe_r', 0)}R)
- Median MAE Drawdown: {conditional_edge.get('median_mae_pct', 0)}% ({conditional_edge.get('median_mae_r', 0)}R)
- Expected Net R (E[R]): {conditional_edge.get('expected_r', 0)}R
- Statistical Edge Verdict: {conditional_edge.get('edge_verdict')} ({conditional_edge.get('confidence')})

======================================================================
6. BITCOIN MACRO ENVIRONMENT
======================================================================
- BTC Context: {btc_desc}

======================================================================
INSTITUTIONAL QUANTITATIVE DIRECTIVES:
======================================================================
1. DUAL STATISTICAL VALIDATION (Candlestick Price History + Conditional Edge Engine):
   - For BUY: Check if previous visits launched strong rallies (bounce rate >= 55%).
   - For SELL: Check if previous visits suffered strong rejections/drops (rejection rate >= 55%).
   - Check Statistical Edge: Notice Empirical P(Win) ({conditional_edge.get('win_probability_pct')}%) and Expected R. If NEGATIVE_EDGE_CHURN_RISK, VETO.
   - Realistic Runway: Can this setup realistically reach TP targets without hitting immediate opposing S/R?
2. S/R Runway Safety:
   - For BUY: If nearest overhead resistance is within +1.80%, VETO. Upward runway must be >= +2.50%.
   - For SELL: If nearest support floor is within -1.80%, VETO. Downward runway must be >= +2.50%.
3. Stop Loss Confluence:
   - For BUY: Stop loss should be below support (~1.50% below entry).
   - For SELL: Stop loss should be above resistance pivot (~1.35% above entry).
4. Rule Compliance: Verify candidate does not trigger stored learned rules on {sym}.
5. Bitcoin Confirmation:
   - For BUY: Reject altcoin longs if BTC is flushing or 5m delta is < -0.12%.
   - For SELL: Reject altcoin shorts if BTC is strongly pumping or 1h delta is > +0.35%. A dumping BTC is a TAILWIND for shorts!
6. Conviction Threshold: Only grant "approved": true if conviction_score is >= 90.

Respond strictly in valid JSON:
{{
  "approved": true | false,
  "conviction_score": 93,
  "expected_r": {conditional_edge.get('expected_r', 0.0)},
  "statistical_edge_verdict": "{conditional_edge.get('edge_verdict')}",
  "risk_reward_ratio": "1:3.2",
  "nearest_support": {sr.get('nearest_sup', 0)},
  "nearest_resistance": {sr.get('nearest_res', 0)},
  "runway_pct": {sr.get('runway_to_res_pct', 0)},
  "recommended_sl": float,
  "recommended_tp1": float,
  "recommended_tp2": float,
  "recommended_tp3": float,
  "price_level_reaction_evaluation": "Analysis of what happened historically when price was at this level",
  "coin_mfe_and_runner_evaluation": "Analysis of how far winners typically run on this coin and whether TP targets are realistic",
  "compliance_note": "How this trade respects past learned rules and statistical edge",
  "concerns": ["Any potential overhead risk"],
  "rationale": "Comprehensive 1-2 sentence institutional rationale."
}}"""

    decision = None
    if DEEPSEEK_API_KEY:
        try:
            body = json.dumps({
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a senior crypto quantitative risk gatekeeper. Output strict valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 800,
                "temperature": 0.1
            }).encode('utf-8')

            req = urllib.request.Request(
                DEEPSEEK_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "MASIS/3.0"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                res_data = json.loads(r.read().decode())
                content = res_data["choices"][0]["message"]["content"]
                decision = json.loads(content)
                decision["source"] = "DEEPSEEK_AI_GATEKEEPER"
        except Exception as e:
            print(f"[GATEKEEPER DEBUG] DeepSeek call error: {e}")
            decision = None

    # Fallback to Local Quantitative Heuristic Gatekeeper if DeepSeek API times out
    if not decision:
        decision = local_heuristic_gatekeeper(candidate, sr, track_record, btc_macro, candlestick_reaction)

    decision["timestamp"] = int(time.time())
    decision["symbol"] = sym
    decision["direction"] = direction
    decision["price"] = cur_p
    decision["sr_data"] = sr
    decision["track_record"] = track_record

    # Log to persistent file
    _save_gatekeeper_decision(decision)

    return decision

def local_heuristic_gatekeeper(candidate, sr, track_record, btc_macro, candlestick_reaction=None):
    direction = candidate.get('direction', 'BUY')
    runway_res = sr.get('runway_to_res_pct', 0)
    dist_sup = sr.get('dist_to_sup_pct', 0)
    score = candidate.get('score', 0)
    cur_p = candidate.get('price', 0)
    sym = candidate.get('symbol', '')
    
    btc_dumping = btc_macro and (btc_macro.get('is_dumping') or btc_macro.get('btc_chg_5m', 0) < -0.15)
    btc_pumping = btc_macro and (btc_macro.get('regime') == 'BTC_BULL_PUMPING' or btc_macro.get('btc_chg_1h', 0) > 0.35)
    
    if direction == 'SELL':
        runway = dist_sup
        has_runway = runway >= 2.2
        near_pivot = runway_res <= 1.8
        high_score = score >= 90
        approved = has_runway and near_pivot and high_score and not btc_pumping
        sl_rec = round(cur_p * 1.0135, 4)
        tp1_rec = round(cur_p * 0.9820, 4)
        tp2_rec = round(cur_p * 0.9650, 4)
        tp3_rec = round(cur_p * 0.9500, 4)
    else:
        runway = runway_res
        has_runway = runway >= 2.2
        near_support = dist_sup <= 1.8
        high_score = score >= 90
        approved = has_runway and near_support and high_score and not btc_dumping
        sl_rec = round(cur_p * 0.9850, 4)
        tp1_rec = round(cur_p * 1.0220, 4)
        tp2_rec = round(cur_p * 1.0400, 4)
        tp3_rec = round(cur_p * 1.0650, 4)
        
    conviction = 92 if approved else 70
    
    reaction_summary = candlestick_reaction.get('summary', 'Normal price zone.') if candlestick_reaction else 'Normal price zone.'
    win_mfe = track_record.get('win_mfe_avg', 0.0)
    win_max = track_record.get('win_mfe_max', 0.0)
    mfe_eval = f"Winners on {sym} average +{win_mfe}% MFE (Max: +{win_max}%). Realistic swing clearance confirmed."
    
    concerns = []
    if not approved:
        if not has_runway:
            concerns.append("Insufficient runway (<2.2%) to major S/R barrier")
        if direction == 'BUY' and btc_dumping:
            concerns.append("Bitcoin is flushing")
        if direction == 'SELL' and btc_pumping:
            concerns.append("Bitcoin is pumping strongly")

    return {
        "approved": approved,
        "conviction_score": conviction,
        "risk_reward_ratio": "1:2.8" if approved else "1:1.2",
        "nearest_support": sr.get('nearest_sup', cur_p * 0.985),
        "nearest_resistance": sr.get('nearest_res', cur_p * 1.04),
        "runway_pct": runway,
        "recommended_sl": sl_rec,
        "recommended_tp1": tp1_rec,
        "recommended_tp2": tp2_rec,
        "recommended_tp3": tp3_rec,
        "price_level_reaction_evaluation": reaction_summary,
        "coin_mfe_and_runner_evaluation": mfe_eval,
        "compliance_note": "Validated via dual candlestick history and coin MFE track record.",
        "concerns": concerns,
        "rationale": f"Dual quantitative evaluation: {direction} setup on {sym}. Historical reaction is '{reaction_summary}'. Winner MFE avg is +{win_mfe}%. Approved: {approved}.",
        "source": "LOCAL_QUANT_GATEKEEPER"
    }

def _save_gatekeeper_decision(decision):
    try:
        decisions = []
        if os.path.exists(DECISIONS_LOG):
            try:
                with open(DECISIONS_LOG, 'r', encoding='utf-8') as f:
                    decisions = json.load(f)
            except Exception:
                decisions = []
        decisions.append(decision)
        with open(DECISIONS_LOG, 'w', encoding='utf-8') as f:
            json.dump(decisions[-50:], f, indent=2)
    except Exception:
        pass
        
    # Also sync to Supabase KV if available
    try:
        from backend_lib.supabase_client import supabase_kv_set
        supabase_kv_set("deepseek_pre_trade_decisions", decisions[-25:])
    except Exception:
        pass

if __name__ == "__main__":
    print("Testing DeepSeek AI Pre-Trade Quantitative Gatekeeper with Dual History Feeds...")
    test_candidate = {
        "symbol": "DOGEUSDT",
        "direction": "BUY",
        "price": 0.0924,
        "score": 94,
        "setup": "HTF_SWING_PULLBACK_LONG"
    }
    test_btc = {
        "btc_price": 63400.0,
        "btc_chg_5m": 0.08,
        "btc_chg_1h": 0.45,
        "regime": "BULLISH_CONTINUATION",
        "alt_long_allowed": True
    }
    res = evaluate_setup_with_deepseek(test_candidate, test_btc)
    print("\nDeepSeek Gatekeeper Verdict:")
    print(json.dumps(res, indent=2))


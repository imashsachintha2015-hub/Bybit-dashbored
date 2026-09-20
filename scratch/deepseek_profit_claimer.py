"""
DeepSeek-Powered Smart Staged Profit Claimer
Analyzes real-time candle microstructure (wicks, momentum, volume, depth)
using DeepSeek AI to decide whether to take profit in stages or keep runners.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime

# Load environment
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
env = {}
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip("'\"")

DEEPSEEK_API_KEY = env.get('DEEPSEEK_API_KEY')
DEEPSEEK_URL = env.get('DEEPSEEK_URL', 'https://api.deepseek.com/v1/chat/completions')
DEEPSEEK_MODEL = env.get('DEEPSEEK_MODEL', 'deepseek-chat')

# Coin lot size specifications for partial closes
COIN_SPECS = {
    'SOLUSDT':  {'min_qty': 0.01, 'qty_step': 0.01, 'price_dec': 2},
    'AVAXUSDT': {'min_qty': 0.1,  'qty_step': 0.1,  'price_dec': 3},
    'NEARUSDT': {'min_qty': 0.1,  'qty_step': 0.1,  'price_dec': 3},
    'LINKUSDT': {'min_qty': 0.1,  'qty_step': 0.1,  'price_dec': 3},
    'DOGEUSDT': {'min_qty': 1.0,  'qty_step': 1.0,  'price_dec': 5},
    'SUIUSDT':  {'min_qty': 1.0,  'qty_step': 1.0,  'price_dec': 4},
    'ADAUSDT':  {'min_qty': 1.0,  'qty_step': 1.0,  'price_dec': 4},
    'BTCUSDT':  {'min_qty': 0.001,'qty_step': 0.001,'price_dec': 2},
    'ETHUSDT':  {'min_qty': 0.01, 'qty_step': 0.01, 'price_dec': 2},
}

DECISION_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deepseek_claimer_decisions.json')

def round_qty(symbol, qty):
    spec = COIN_SPECS.get(symbol, {'qty_step': 0.01, 'min_qty': 0.01})
    step = spec['qty_step']
    decimals = len(str(step).split('.')[1]) if '.' in str(step) else 0
    rounded = round(round(qty / step) * step, decimals)
    return max(rounded, spec['min_qty'])

def round_price(symbol, price):
    spec = COIN_SPECS.get(symbol, {'price_dec': 2})
    return round(price, spec['price_dec'])

def fetch_klines(symbol, interval='5', limit=15):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            candles = []
            for row in reversed(raw):
                candles.append({
                    "start": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vol": float(row[5])
                })
            return candles
    except Exception:
        return []

def extract_microstructure(candles_5m, candles_1m, side='Buy'):
    """Extracts wicks, momentum, and volume ratio for DeepSeek analysis."""
    if not candles_5m or len(candles_5m) < 3:
        return {}
    
    last_5m = candles_5m[-1]
    prev_5m = candles_5m[-2]
    
    range_5m = max(0.0001, last_5m['high'] - last_5m['low'])
    upper_wick_5m = last_5m['high'] - max(last_5m['open'], last_5m['close'])
    lower_wick_5m = min(last_5m['open'], last_5m['close']) - last_5m['low']
    body_5m = abs(last_5m['close'] - last_5m['open'])
    
    u_wick_pct_5m = round((upper_wick_5m / range_5m) * 100, 1)
    l_wick_pct_5m = round((lower_wick_5m / range_5m) * 100, 1)
    body_pct_5m = round((body_5m / range_5m) * 100, 1)
    
    avg_vol_5m = sum(c['vol'] for c in candles_5m[-8:-1]) / max(1, len(candles_5m[-8:-1]))
    vol_ratio_5m = round(last_5m['vol'] / max(0.001, avg_vol_5m), 2)
    
    # 1m candle momentum (last 3 1m bars)
    consecutive_green_1m = 0
    consecutive_red_1m = 0
    if candles_1m and len(candles_1m) >= 3:
        for c in reversed(candles_1m[-4:]):
            if c['close'] > c['open']:
                if consecutive_red_1m == 0:
                    consecutive_green_1m += 1
            else:
                if consecutive_green_1m == 0:
                    consecutive_red_1m += 1
                    
    return {
        "upper_wick_pct_5m": u_wick_pct_5m,
        "lower_wick_pct_5m": l_wick_pct_5m,
        "body_pct_5m": body_pct_5m,
        "vol_ratio_5m": vol_ratio_5m,
        "consecutive_green_1m": consecutive_green_1m,
        "consecutive_red_1m": consecutive_red_1m,
        "candle_color_5m": "GREEN" if last_5m['close'] >= last_5m['open'] else "RED"
    }

def consult_deepseek_claimer(position, micro):
    """
    Asks DeepSeek whether to take staged profit or let runners ride.
    """
    sym = position['symbol']
    side = position['side']
    entry = position['entry']
    mark = position['mark']
    unpnl = position['unpnl']
    size = position['size']
    gain_pct = ((mark - entry) / entry) * 100.0 if side.upper() == 'BUY' else ((entry - mark) / entry) * 100.0

    # Retrieve past learned rules from SQLite Market Knowledge Base
    try:
        from backend_lib.market_knowledge import kb
        past_rules = kb.get_relevant_knowledge(sym, limit=3)
        rules_text = "\n".join(f"- {r.get('rule_summary')}" for r in past_rules) if past_rules else "- Focus on preserving green profit once +0.32% reached."
    except Exception:
        rules_text = "- Focus on preserving green profit once +0.32% reached."

    prompt = f"""You are the Chief Quantitative Scalper at MASIS Trading.
An active demo position is currently in profit. Decide whether to:
1. "TAKE_PARTIAL" (take 50% profit immediately to lock real cash, ratchet remaining stop to entry + 0.12% to cover fees, and let rest run)
2. "TAKE_ALL" (close 100% at market because momentum is stalling, upper wick rejection or volume exhaustion indicates imminent reversal)
3. "KEEP_RUNNER" (hold 100% position, ratchet stop to entry + 0.12% risk-free, because volume is surging and price is breaking out)

Current Position Details:
- Symbol: {sym}
- Direction: {side.upper()}
- Entry Price: {entry}
- Current Mark Price: {mark}
- Unrealized Gain: +{gain_pct:.2f}% (${unpnl:+.4f} USDT)
- Position Size: {size} units

Live Microstructure Data:
- 5m Upper Wick: {micro.get('upper_wick_pct_5m', 0)}% of bar range
- 5m Lower Wick: {micro.get('lower_wick_pct_5m', 0)}% of bar range
- 5m Candle Body: {micro.get('body_pct_5m', 0)}% ({micro.get('candle_color_5m', 'N/A')})
- 5m Volume vs Average: {micro.get('vol_ratio_5m', 1.0)}x normal
- 1m Consecutive Green Bars: {micro.get('consecutive_green_1m', 0)}
- 1m Consecutive Red Bars: {micro.get('consecutive_red_1m', 0)}

Market Prophet Memory (Past Learned Lessons on {sym}):
{rules_text}

Institutional Rules:
- If upper wick is >= 35% on a LONG, sellers are actively rejecting the high. Recommend TAKE_PARTIAL or TAKE_ALL.
- If volume is < 0.8x on the move up, lack of buying volume into resistance indicates reversal risk. Recommend TAKE_PARTIAL.
- If volume is >= 1.4x and candle body is > 55% green, strong momentum breakout. Recommend KEEP_RUNNER.
- When in doubt, PRESERVE PROFIT via TAKE_PARTIAL. A guaranteed +0.35% win is vastly superior to letting it reverse into a loss.

Respond strictly in valid JSON:
{{
  "verdict": "TAKE_PARTIAL" | "TAKE_ALL" | "KEEP_RUNNER",
  "confidence": 0.85,
  "rationale": "One-sentence concise institutional explanation",
  "close_pct": 50,
  "ratchet_sl_offset_pct": 0.12,
  "stage2_target_offset_pct": 0.85
}}"""

    if not DEEPSEEK_API_KEY:
        return local_heuristic_claimer(gain_pct, micro, side)

    body = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "You are a quantitative crypto execution engine. Output strict JSON only."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 250,
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

    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode())
            content = res["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            parsed["source"] = "DEEPSEEK_AI"
            return parsed
    except Exception as e:
        # Fallback instantly to local quantitative heuristics so execution is never blocked
        fallback = local_heuristic_claimer(gain_pct, micro, side)
        fallback["source"] = f"LOCAL_FALLBACK ({str(e)[:40]})"
        return fallback

def local_heuristic_claimer(gain_pct, micro, side):
    u_wick = micro.get('upper_wick_pct_5m', 0)
    vol_ratio = micro.get('vol_ratio_5m', 1.0)
    
    if side.upper() == 'BUY':
        if u_wick >= 40.0:
            return {
                "verdict": "TAKE_ALL",
                "confidence": 0.82,
                "rationale": f"High upper rejection wick ({u_wick}%) indicates local resistance stall; taking full profit.",
                "close_pct": 100,
                "ratchet_sl_offset_pct": 0.12,
                "stage2_target_offset_pct": 0.80
            }
        elif vol_ratio >= 1.5 and u_wick < 25.0:
            return {
                "verdict": "KEEP_RUNNER",
                "confidence": 0.85,
                "rationale": f"Volume breakout ({vol_ratio}x) with solid body; trailing stop to fee-cushion +0.12% and letting runner ride.",
                "close_pct": 0,
                "ratchet_sl_offset_pct": 0.12,
                "stage2_target_offset_pct": 1.00
            }
        else:
            return {
                "verdict": "TAKE_PARTIAL",
                "confidence": 0.90,
                "rationale": "Standard Stage-1 milestone: secure 50% cash profit and lock remaining 50% at risk-free +0.12%.",
                "close_pct": 50,
                "ratchet_sl_offset_pct": 0.12,
                "stage2_target_offset_pct": 0.85
            }
    else:
        # Sell / Short side
        l_wick = micro.get('lower_wick_pct_5m', 0)
        if l_wick >= 40.0:
            return {
                "verdict": "TAKE_ALL",
                "confidence": 0.82,
                "rationale": f"Lower rejection wick ({l_wick}%) indicates buyer absorption; banking full short profit.",
                "close_pct": 100,
                "ratchet_sl_offset_pct": 0.12,
                "stage2_target_offset_pct": 0.80
            }
        else:
            return {
                "verdict": "TAKE_PARTIAL",
                "confidence": 0.90,
                "rationale": "Secure 50% profit and trail remaining stop to entry - 0.12% risk-free.",
                "close_pct": 50,
                "ratchet_sl_offset_pct": 0.12,
                "stage2_target_offset_pct": 0.85
            }

class SmartProfitClaimer:
    def __init__(self, client):
        self.client = client
        self.claimed_stages = {}  # {pos_key: {'stage1_done': bool, 'stage2_done': bool, 'entry': float}}
        self.history = []
        self._load_history()

    def _load_history(self):
        # 1. Try Supabase Cloud Database
        try:
            from backend_lib.supabase_client import supabase_kv_get
            cloud_history = supabase_kv_get("deepseek_claimer_decisions", None)
            if cloud_history and isinstance(cloud_history, list):
                self.history = cloud_history
                return
        except Exception:
            pass

        # 2. Local file fallback
        if os.path.exists(DECISION_LOG_FILE):
            try:
                with open(DECISION_LOG_FILE, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
            except Exception:
                self.history = []

    def _save_history(self):
        try:
            with open(DECISION_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.history[-100:], f, indent=2)
        except Exception:
            pass
        try:
            from backend_lib.supabase_client import supabase_kv_set
            supabase_kv_set("deepseek_claimer_decisions", self.history[-100:])
        except Exception:
            pass

    def evaluate_and_claim(self, position):
        """
        Main entrypoint: called whenever an active position has positive gain.
        Checks if position qualifies for Stage 1 (+0.32% to +0.45%) or Stage 2 (+0.75%+).
        """
        sym = position['symbol']
        side = position['side']
        entry = position['entry']
        mark = position['mark']
        size = position['size']
        key = f"{sym}_{side}"
        
        gain_pct = ((mark - entry) / entry) * 100.0 if side.upper() == 'BUY' else ((entry - mark) / entry) * 100.0
        
        state = self.claimed_stages.get(key, {'stage1_done': False, 'stage2_done': False, 'entry': entry})
        
        # STAGE 1: Hit +0.32% or higher
        if gain_pct >= 0.32 and not state['stage1_done']:
            # Pull microstructure
            k5 = fetch_klines(sym, '5', 12)
            k1 = fetch_klines(sym, '1', 12)
            micro = extract_microstructure(k5, k1, side)
            
            # Consult DeepSeek AI
            decision = consult_deepseek_claimer(position, micro)
            verdict = decision.get('verdict', 'TAKE_PARTIAL')
            rationale = decision.get('rationale', '')
            conf = decision.get('confidence', 0.8)
            source = decision.get('source', 'DEEPSEEK_AI')
            
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = {
                "timestamp": ts_str,
                "symbol": sym,
                "side": side,
                "gain_pct": round(gain_pct, 2),
                "unpnl": position['unpnl'],
                "verdict": verdict,
                "confidence": conf,
                "source": source,
                "rationale": rationale,
                "stage": 1
            }
            def safe_p(text):
                try:
                    print(text)
                except UnicodeEncodeError:
                    print(text.encode('ascii', errors='replace').decode('ascii'))

            safe_p(f"\n[DEEPSEEK PROFIT CLAIMER] {sym} +{gain_pct:.2f}% -> VERDICT: {verdict} ({source})")
            safe_p(f"   Rationale: {rationale}")
            
            # Execute Staged Plan
            if verdict == "TAKE_ALL":
                # Close 100% at market
                close_res = self.client.close_position('linear', sym, side, size)
                log_entry["action_taken"] = f"CLOSED_100% ({size} units)"
                log_entry["api_response"] = close_res.get('retMsg', '')
                state['stage1_done'] = True
                state['stage2_done'] = True
                safe_p(f"   Action: Closed 100% position at market IOC!")
            
            elif verdict == "TAKE_PARTIAL":
                # Bank 50%
                half_qty = round_qty(sym, size * 0.5)
                if half_qty > 0 and half_qty < size:
                    close_res = self.client.close_position('linear', sym, side, half_qty)
                    log_entry["action_taken"] = f"BANKED_50% ({half_qty} units closed, remaining {size - half_qty})"
                    safe_p(f"   Action: Banked 50% ({half_qty} units) into cash balance!")
                else:
                    log_entry["action_taken"] = f"CANNOT_SPLIT (size {size} too small, maintaining full)"
                
                # Move broker-side SL to Entry + 0.12% (guaranteed green exit covering Bybit 0.11% taker fees)
                sl_mult = 1.0012 if side.upper() == 'BUY' else 0.9988
                be_stop = round_price(sym, entry * sl_mult)
                stop_res = self.client.set_trading_stop('linear', sym, stop_loss=str(be_stop))
                log_entry["ratchet_sl"] = be_stop
                state['stage1_done'] = True
                safe_p(f"   Action: Ratcheted broker-side SL to {be_stop} (+0.12% fee-cushioned green stop)!")
            
            elif verdict == "KEEP_RUNNER":
                # Don't close, but lock in risk-free stop at Entry + 0.12%
                sl_mult = 1.0012 if side.upper() == 'BUY' else 0.9988
                be_stop = round_price(sym, entry * sl_mult)
                stop_res = self.client.set_trading_stop('linear', sym, stop_loss=str(be_stop))
                log_entry["action_taken"] = f"RUNNER_KEPT (SL ratcheted to {be_stop})"
                state['stage1_done'] = True
                safe_p(f"   Action: Keeping 100% runner! Ratcheted SL to {be_stop} (risk-free).")
                
            self.claimed_stages[key] = state
            self.history.append(log_entry)
            self._save_history()
            return log_entry
            
        # STAGE 2: Hit +0.75% or higher
        elif gain_pct >= 0.75 and state['stage1_done'] and not state['stage2_done']:
            try:
                print(f"\n[STAGE 2 TARGET HIT] {sym} reached +{gain_pct:.2f}%! Locking final runner.")
            except UnicodeEncodeError:
                pass
            close_res = self.client.close_position('linear', sym, side, size)
            state['stage2_done'] = True
            self.claimed_stages[key] = state
            log_entry = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": sym,
                "side": side,
                "gain_pct": round(gain_pct, 2),
                "unpnl": position['unpnl'],
                "verdict": "STAGE_2_COMPLETE",
                "action_taken": f"CLOSED_RUNNER ({size} units)",
                "rationale": "Target 2 +0.75% milestone achieved. Banking maximum profit.",
                "stage": 2
            }
            self.history.append(log_entry)
            self._save_history()
            return log_entry
            
        return None

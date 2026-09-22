"""
Smart Micro-Scalp Execution Engine with DeepSeek Staged Profit Claimer
Target: Grow account from ~$9.50 to $15.00 gross USDT.
Sizing: $1.00 - $1.20 margin per trade ($10 - $12 notional @ 10x leverage).
Max Concurrent: 1-2 trades (maintains $7.50+ / 80% free cash reserve).
Profit Claimer: DeepSeek staged exits (TAKE_PARTIAL, TAKE_ALL, KEEP_RUNNER).
"""

import os
import sys
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient
from backend_lib.market_knowledge import kb
from daemons.deepseek_profit_claimer import SmartProfitClaimer, round_price, round_qty, COIN_SPECS, fetch_klines, extract_microstructure
from daemons.deepseek_pre_trade_gatekeeper import evaluate_setup_with_deepseek

# Load environment
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
env = {}
with open(ENV_PATH, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])
claimer = SmartProfitClaimer(client)

STATE_FILE   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scratch', 'live_market_state.json')
EXECUTOR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'smart_executor.log')

MAX_CONCURRENT_TRADES = 1  # Sequential execution for maximum safety
NOTIONAL_PER_TRADE = 11.0  # ~$1.10 margin @ 10x leverage

# Cache for tracking trade lifecycles and MFE/MAE
tracked_trades = {}  # symbol -> {entry, side, size, max_gain, min_gain, opened_at}
symbol_cooldowns = {}  # symbol -> cooldown_until_timestamp (prevents instant re-entry churn)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts}] {safe}"
    print(out, flush=True)
    with open(EXECUTOR_LOG, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")

def get_account_state():
    wb = client.get_wallet_balance()
    coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
    usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
    eq = float(usdt.get('equity', 0))
    
    pos_res = client.get_positions()
    raw_pos = pos_res.get('result', {}).get('list', []) if pos_res.get('retCode') == 0 else []
    active = []
    for p in raw_pos:
        sz = float(p.get('size', 0))
        if sz > 0:
            active.append({
                "symbol": p.get('symbol'),
                "side": p.get('side'),
                "size": sz,
                "entry": float(p.get('avgPrice')),
                "mark": float(p.get('markPrice')),
                "unpnl": float(p.get('unrealisedPnl')),
                "sl": float(p.get('stopLoss') or 0),
                "tp": float(p.get('takeProfit') or 0)
            })
    return eq, active

def calculate_trade_qty(symbol, price):
    raw_qty = NOTIONAL_PER_TRADE / price
    qty = round_qty(symbol, raw_qty)
    min_q = COIN_SPECS.get(symbol, {}).get('min_qty', 0)
    if qty < min_q:
        qty = min_q
    return qty

def check_and_learn_closed_trades(current_active_symbols):
    """Detects when a tracked position has closed, records to SQLite, and triggers DeepSeek learning."""
    global tracked_trades
    closed_syms = [s for s in tracked_trades.keys() if s not in current_active_symbols]
    for sym in closed_syms:
        trade_info = tracked_trades.pop(sym, None)
        if not trade_info:
            continue
        try:
            # Query Bybit closed PnL for exact settled numbers
            closed_res = client.get_closed_pnl(limit=5)
            closed_list = closed_res.get('result', {}).get('list', [])
            matched = next((c for c in closed_list if c.get('symbol') == sym), None)
            
            pnl_net = float(matched.get('closedPnl', 0)) if matched else 0.0
            exit_p = float(matched.get('avgExitPrice', 0)) if matched else 0.0
            entry_p = float(matched.get('avgEntryPrice', trade_info.get('entry', 0))) if matched else trade_info.get('entry', 0)
            pnl_pct = ((exit_p - entry_p) / entry_p) * 100.0 if trade_info.get('side') == 'Buy' else ((entry_p - exit_p) / entry_p) * 100.0
            
            k5 = fetch_klines(sym, '5', 10)
            k1 = fetch_klines(sym, '1', 10)
            micro = extract_microstructure(k5, k1, trade_info.get('side', 'Buy'))
            
            record_payload = {
                "symbol": sym,
                "direction": trade_info.get('side', 'BUY'),
                "entry_price": entry_p,
                "exit_price": exit_p,
                "size": trade_info.get('size', 0),
                "pnl_net": pnl_net,
                "pnl_pct": pnl_pct,
                "mfe_pct": trade_info.get('max_gain', 0),
                "mae_pct": trade_info.get('min_gain', 0),
                "exit_reason": "TAKE_PROFIT_OR_CLAIM" if pnl_net > 0 else "STOP_LOSS"
            }
            log(f"🧠 [LEARNING TRIGGERED] Trade {sym} closed ({pnl_pct:+.2f}%, ${pnl_net:+.4f})! Generating DeepSeek reflection...")
            learn_res = kb.record_trade_close(record_payload, micro)
            log(f"   Stored Rule: {learn_res.get('reflection', {}).get('rule')}")

            # Authoritative SQLite Measurement Journal logging
            try:
                from backend_lib.measurement_journal import mj
                mj.record_completed_trade(record_payload)
            except Exception as e_mj:
                log(f"[MEASUREMENT JOURNAL ERROR] {e_mj}")

            # Cooldown guard: Put symbol on cooldown so it NEVER immediately re-orders!
            cooldown_sec = 900 if pnl_net <= 0 else 300  # 15m cooldown on loss/breakeven, 5m on win
            symbol_cooldowns[sym] = time.time() + cooldown_sec
            log(f"⏳ [COOLDOWN SET] {sym} placed on {cooldown_sec // 60}m cooldown until {datetime.fromtimestamp(symbol_cooldowns[sym]).strftime('%H:%M:%S')}")
        except Exception as e:
            log(f"[LEARN NOTICE] Error processing closed trade {sym}: {e}")

def run_single_cycle():
    eq, active = get_account_state()
    
    # 1. Check Target Mode state from SQLite Knowledge Base
    target_state = kb.get_target_state(current_equity=eq)
    target_equity = target_state.get('target_equity', 15.0)
    is_armed = target_state.get('is_armed', True)
    status = target_state.get('status', 'ACTIVE')

    # Circuit Breaker: Target Hit
    if eq >= target_equity or status == 'TARGET_REACHED_PARKED':
        log(f"🎯 [TARGET REACHED & PARKED] Current Equity: ${eq:.4f} >= ${target_equity:.2f} USDT (Progress: {target_state.get('progress_pct')}%)! AI Engine Standing By.")
        return True

    # Check if paused
    if not is_armed:
        log(f"⏸️ [ANALYZE MODE STANDBY] Waiting for user arming in dashboard (Target: ${target_equity:.2f})...")
        return True
        
    active_syms = [p['symbol'] for p in active]
    check_and_learn_closed_trades(active_syms)
        
    from daemons.btc_macro_monitor import fetch_btc_macro
    btc_macro = fetch_btc_macro()

    # 2. Manage all active positions with DeepSeek Smart Profit Claimer
    if active:
        for pos in active:
            sym = pos['symbol']
            side = pos['side']
            entry = pos['entry']
            mark = pos['mark']
            unpnl = pos['unpnl']
            gain_pct = ((mark - entry) / entry) * 100.0 if side.upper() == 'BUY' else ((entry - mark) / entry) * 100.0
            
            # Update MFE / MAE tracking
            if sym not in tracked_trades:
                tracked_trades[sym] = {'entry': entry, 'side': side, 'size': pos['size'], 'max_gain': gain_pct, 'min_gain': gain_pct, 'opened_at': time.time()}
            else:
                tracked_trades[sym]['max_gain'] = max(tracked_trades[sym]['max_gain'], gain_pct)
                tracked_trades[sym]['min_gain'] = min(tracked_trades[sym]['min_gain'], gain_pct)
            
            log(f"📊 [MONITOR] {sym} {side} | Mark: {mark} (Entry: {entry}) | Gain: {gain_pct:+.2f}% (${unpnl:+.4f}) | Peak Gain: {tracked_trades[sym]['max_gain']:+.2f}%")
            
            # ── REAL-TIME BITCOIN MACRO & ALTCOIN SENSITIVITY DEFENSE ──
            # Altcoins are heavily correlated and drop 1.5x-3x faster than BTC when BTC flushes.
            
            # Tier 1: Severe / Flash BTC Flush (Plunge <= -0.35% 1m or <= -0.50% 5m)
            if btc_macro and btc_macro.get('is_severe_flush'):
                if gain_pct >= 0.05:
                    log(f"🚨 [EMERGENCY BTC FLUSH EXIT] Bitcoin crashing ({btc_macro.get('btc_chg_1m')}% 1m, {btc_macro.get('btc_chg_5m')}% 5m)! Closing {sym} @ +{gain_pct:.2f}% (${unpnl:+.4f}) to preserve capital...")
                    client.close_position('linear', sym, side, pos['size'])
                    continue
                elif -0.60 <= gain_pct < 0.05:
                    log(f"🚨 [EMERGENCY BTC FLUSH CUT] Bitcoin crashing! Cutting {sym} @ {gain_pct:.2f}% (${unpnl:+.4f}) to avoid altcoin cascade...")
                    client.close_position('linear', sym, side, pos['size'])
                    continue
            
            # Tier 2: Bitcoin Dumping Caution (Drop <= -0.18% 1m or <= -0.25% 5m)
            elif btc_macro and btc_macro.get('is_dumping'):
                if gain_pct >= 0.30:
                    log(f"🛡️ [BTC DUMP DEFENSE] Bitcoin dropping ({btc_macro.get('btc_chg_5m')}%)! Banking profit on {sym} (+{gain_pct:.2f}%, ${unpnl:+.4f}) at market...")
                    client.close_position('linear', sym, side, pos['size'])
                    continue
                elif 0.0 <= gain_pct < 0.30 and pos.get('sl', 0) < entry:
                    be_sl = round_price(sym, entry * 1.0012)
                    client.set_trading_stop('linear', sym, stop_loss=str(be_sl))
                    log(f"🛡️ [BTC DUMP CAUTION] Ratcheted SL for {sym} to Break-Even ({be_sl}) as BTC weakens.")

            # Tier 3: Strategy Mode Specific Profit Lock & Staged Exits
            strategy_mode = target_state.get('strategy_mode', 'SWING_RUNNER')
            
            if strategy_mode == "SWING_RUNNER":
                # ── HTF SWING RUNNER MODE: $1.00+ Realistic Profit Milestones ──
                # Give the trade room to breathe! Only ratchet SL once safely up +1.20%
                is_long = (side.upper() == 'BUY')
                
                if is_long:
                    needs_be = (pos.get('sl', 0) < entry * 1.002)
                    be_sl = round_price(sym, entry * 1.0025)      # +0.25% guarantees fees covered
                    sl_stage1 = round_price(sym, entry * 1.0040)  # +0.40% guaranteed floor
                    sl_stage2 = round_price(sym, entry * 1.0200)  # +2.00% guaranteed floor
                else: # SHORT
                    needs_be = (pos.get('sl', 0) == 0 or pos.get('sl', 0) > entry * 0.998)
                    be_sl = round_price(sym, entry * 0.9975)      # +0.25% fee-proof green
                    sl_stage1 = round_price(sym, entry * 0.9960)  # +0.40% guaranteed floor
                    sl_stage2 = round_price(sym, entry * 0.9800)  # +2.00% guaranteed floor

                if gain_pct >= 1.20 and needs_be:
                    client.set_trading_stop('linear', sym, stop_loss=str(be_sl))
                    log(f"🔒 [SWING RISK-FREE LOCK] {sym} reached +{gain_pct:.2f}%! Ratcheted SL to {be_sl} (+0.25% fee-proof green).")

                # Milestone 1: +2.20% Gain (~$0.26 profit on $12 notional) -> Bank initial cash
                if gain_pct >= 2.20 and tracked_trades[sym].get('stage', 0) < 1:
                    tracked_trades[sym]['stage'] = 1
                    client.set_trading_stop('linear', sym, stop_loss=str(sl_stage1))
                    log(f"🎯 [SWING MILESTONE 1 (+2.20%)] {sym} reached +{gain_pct:.2f}% (${unpnl:+.4f})! Ratcheted SL to {sl_stage1} (+0.40% green floor).")
                    claimer.evaluate_and_claim(pos, is_swing_mode=True)

                # Milestone 2: +4.00% Gain (~$0.48 profit) -> Ratchet SL to +2.00%
                elif gain_pct >= 4.00 and tracked_trades[sym].get('stage', 0) < 2:
                    tracked_trades[sym]['stage'] = 2
                    client.set_trading_stop('linear', sym, stop_loss=str(sl_stage2))
                    log(f"🚀 [SWING MILESTONE 2 (+4.00%)] {sym} reached +{gain_pct:.2f}% (${unpnl:+.4f})! Ratcheted SL to {sl_stage2} (+2.00% green floor).")
                    claimer.evaluate_and_claim(pos, is_swing_mode=True)

                # Milestone 3: +6.50%+ Full Runner Target ($0.80 - $1.20+ Cash Profit)
                elif gain_pct >= 6.50:
                    log(f"🏆 [SWING RUNNER GOAL REACHED] {sym} reached +{gain_pct:.2f}% (${unpnl:+.4f} USDT)! Closing 100% to lock target profit...")
                    client.close_position('linear', sym, side, pos['size'])
                    continue

                # ── ANTI-STAGNATION TIME-STOP ──
                # Give HTF Swings 6.0 hours of breathing room so multi-hour macro moves (like AVAX $0.50 drop) mature
                opened_at = tracked_trades[sym].get('opened_at', time.time())
                duration_hrs = (time.time() - opened_at) / 3600.0
                peak_g = tracked_trades[sym].get('max_gain', 0.0)
                if duration_hrs >= 6.0:
                    if peak_g < 0.40 and -0.60 <= gain_pct <= 0.20:
                        log(f"⏳ [STAGNATION TIME-STOP] {sym} open for {duration_hrs:.1f}h without momentum expansion (Peak: +{peak_g:.2f}%, Now: {gain_pct:+.2f}%). Scratching position to maintain target sprint pace...")
                        client.close_position('linear', sym, side, pos['size'])
                        symbol_cooldowns[sym] = time.time() + 900
                        continue

            else:
                # ── MICRO SCALP MODE (Legacy fallback) ──
                if gain_pct >= 0.18 and pos.get('sl', 0) < entry:
                    be_sl = round_price(sym, entry * 1.0012)
                    client.set_trading_stop('linear', sym, stop_loss=str(be_sl))
                    log(f"🔒 [PROFIT LOCK] {sym} reached +{gain_pct:.2f}%! Ratcheted broker-side SL to {be_sl} (risk-free).")

                if gain_pct >= 0.32:
                    claimer.evaluate_and_claim(pos)
                
        # If we have reached max concurrent positions, don't open new ones
        if len(active) >= MAX_CONCURRENT_TRADES:
            return True
            
    # 3. If under max concurrent trades, inspect market state for Top Setup
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                market = json.load(f)
        except Exception:
            market = {}
            
        top = market.get('top_recommendation')
        active_syms = [p['symbol'] for p in active]
        strategy_mode = target_state.get('strategy_mode', 'SWING_RUNNER')
        
        if top and top.get('recommended') and top.get('symbol') not in active_syms:
            sym = top['symbol']
            direction = top['direction']
            score = top['score']
            cur_price = top['price']
            
            # Check symbol cooldown to prevent churning the same coin in the next second
            now_ts = time.time()
            if now_ts < symbol_cooldowns.get(sym, 0):
                rem = int(symbol_cooldowns[sym] - now_ts)
                if int(now_ts) % 30 < 6:
                    log(f"⏳ [COOLDOWN ACTIVE] {sym} is cooling down ({rem}s remaining). Skipping re-entry.")
                return True

            # BTC DUMP VETO for Longs / BTC PUMP VETO for Shorts:
            if btc_macro:
                if not btc_macro.get('alt_long_allowed', True) and direction == 'BUY':
                    log(f"🛑 [BTC DUMP VETO] Long entry for {sym} blocked: Bitcoin is flushing ({btc_macro.get('btc_chg_5m')}%, {btc_macro.get('regime')})!")
                    return True
                if btc_macro.get('regime') == 'BTC_BULL_PUMPING' and direction == 'SELL':
                    log(f"🛑 [BTC PUMP VETO] Short entry for {sym} blocked: Bitcoin is pumping aggressively ({btc_macro.get('btc_chg_1h')}%)!")
                    return True

            # Entry threshold: 78 for SWING_RUNNER, 75 for MICRO_SCALP
            min_entry_score = 78 if strategy_mode == "SWING_RUNNER" else 75

            if score >= min_entry_score and direction in ['BUY', 'SELL']:
                log(f"🧠 [DEEPSEEK REVIEW] Evaluating {sym} {direction} candidate through DeepSeek Pre-Trade Gatekeeper...")
                gatekeeper_dec = evaluate_setup_with_deepseek(top, btc_macro)
                
                is_approved = gatekeeper_dec.get('approved', False)
                conv_score = gatekeeper_dec.get('conviction_score', 0)
                rationale = gatekeeper_dec.get('rationale', '')
                runway = gatekeeper_dec.get('runway_pct', 0)
                sr_sup = gatekeeper_dec.get('nearest_support')
                sr_res = gatekeeper_dec.get('nearest_resistance')
                price_react = gatekeeper_dec.get('price_level_reaction_evaluation', '')
                mfe_eval = gatekeeper_dec.get('coin_mfe_and_runner_evaluation', '')
                
                req_conv = 78 if strategy_mode == "SWING_RUNNER" else 75
                if not is_approved or conv_score < req_conv:
                    concerns = ", ".join(gatekeeper_dec.get('concerns', ['Low conviction']))
                    log(f"🛑 [DEEPSEEK GATEKEEPER VETO] {sym} {direction} trade rejected (Score: {conv_score}/100, Required: {req_conv})!")
                    log(f"   Price History Reaction: {price_react}")
                    log(f"   MFE Runner Profile: {mfe_eval}")
                    log(f"   Concerns: {concerns} | Runway: {runway}% | {rationale}")
                    symbol_cooldowns[sym] = time.time() + 300  # 5m cooldown on vetoed setup
                    return True
                
                log(f"🌟 [DEEPSEEK APPROVED] {sym} {direction} cleared by Gatekeeper (Score: {conv_score}/100, R:R: {gatekeeper_dec.get('risk_reward_ratio')})!")
                log(f"   S/R Context: Sup {sr_sup} | Res {sr_res} (Runway: {runway}%) | Compliance: {gatekeeper_dec.get('compliance_note')}")
                log(f"   Price History Reaction: {price_react}")
                log(f"   MFE Runner Profile: {mfe_eval}")
                log(f"   DeepSeek Rationale: {rationale}")

                notional_target = 12.5 if strategy_mode == "SWING_RUNNER" else 11.0
                qty = calculate_trade_qty(sym, cur_price)
                if qty <= 0:
                    return True
                    
                # Calculate initial Stop Loss and Take Profit brackets direction-aware
                if direction == 'BUY':
                    if strategy_mode == "SWING_RUNNER":
                        sl_price = gatekeeper_dec.get('recommended_sl') or round_price(sym, cur_price * 0.9850)
                        tp_price = gatekeeper_dec.get('recommended_tp3') or round_price(sym, cur_price * 1.0650)
                        sl_label = "-1.50%"
                        tp_label = "+6.50% ($1+ Runner)"
                    else:
                        sl_price = round_price(sym, cur_price * 0.9925)
                        tp_price = round_price(sym, cur_price * 1.0100)
                        sl_label = "-0.75%"
                        tp_label = "+1.00%"
                else: # SELL (Short)
                    if strategy_mode == "SWING_RUNNER":
                        sl_price = gatekeeper_dec.get('recommended_sl') or round_price(sym, cur_price * 1.0135)
                        tp_price = gatekeeper_dec.get('recommended_tp3') or round_price(sym, cur_price * 0.9500)
                        sl_label = "+1.35% (Pivot Stop)"
                        tp_label = "-5.00% (Short Runner)"
                    else:
                        sl_price = round_price(sym, cur_price * 1.0075)
                        tp_price = round_price(sym, cur_price * 0.9900)
                        sl_label = "+0.75%"
                        tp_label = "-1.00%"
                
                log(f"🚀 [ENTER TRADE ({strategy_mode})] Top Setup Found: {sym} {direction} (Score: {score}/100 - {top['setup']})")
                log(f"   Size: {qty} units (~${qty * cur_price:.2f} notional @ 10x) | SL: {sl_price} ({sl_label}) | TP: {tp_price} ({tp_label})")
                
                # Ensure 10x leverage
                client.set_leverage(sym, 10)
                
                order_side = 'Buy' if direction == 'BUY' else 'Sell'
                order_res = client.place_order(
                    category='linear',
                    symbol=sym,
                    side=order_side,
                    order_type='Market',
                    qty=qty,
                    sl=sl_price,
                    tp=tp_price
                )
                
                if order_res.get('retCode') == 0:
                    log(f"✅ Order Placed Successfully! OrderId: {order_res.get('result', {}).get('orderId')}")
                    try:
                        from backend_lib.measurement_journal import mj
                        mj.log_decision(
                            top,
                            "EXECUTED",
                            f"Live order placed: Qty {qty} @ {cur_price} | SL {sl_price}, TP {tp_price}",
                            extra={
                                "target_runway_pct": runway,
                                "stop_dist_pct": 1.5 if strategy_mode == "SWING_RUNNER" else 0.75,
                                "calibrated_win_prob": conv_score,
                                "expected_r": gatekeeper_dec.get('expected_r', 0.0)
                            }
                        )
                    except Exception as e_mj:
                        log(f"[MEASUREMENT JOURNAL ERROR] {e_mj}")

                    try:
                        from backend_lib.supabase_client import supabase_patch
                        supabase_patch("live_market_signals", {"symbol": f"eq.{sym}", "status": "eq.SUGGESTED"}, {
                            "was_traded": True,
                            "status": "EXECUTED",
                            "entry_price": cur_price,
                            "result_reason": f"Executed live on Bybit demo @ {cur_price} with SL {sl_price} (-0.75%), TP {tp_price} (+0.90%)"
                        })
                    except Exception:
                        pass
                else:
                    log(f"❌ Order Failed: {order_res.get('retMsg')}")
                    try:
                        from backend_lib.supabase_client import supabase_patch
                        supabase_patch("live_market_signals", {"symbol": f"eq.{sym}", "status": "eq.SUGGESTED"}, {
                            "status": "FAILED_EXECUTION",
                            "result_reason": f"Order rejected by broker: {order_res.get('retMsg')}"
                        })
                    except Exception:
                        pass
                    
    return True

def main():
    target_info = kb.get_target_state()
    tgt = target_info.get('target_equity', 15.0)
    log("========================================================================")
    log("  SMART MICRO-SCALP GROWTH EXECUTOR (POWERED BY DEEPSEEK PROFIT CLAIMER)")
    log(f"  Dynamic Target: ${tgt:.2f} USDT | Notional/Trade: ${NOTIONAL_PER_TRADE:.2f}")
    log("========================================================================")
    
    while True:
        try:
            keep_running = run_single_cycle()
            if not keep_running:
                break
        except Exception as e:
            log(f"[EXECUTOR ERROR] {e}")
            
        time.sleep(5)

if __name__ == '__main__':
    main()

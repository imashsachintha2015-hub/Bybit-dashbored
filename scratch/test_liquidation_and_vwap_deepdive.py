import urllib.request
import json
import ssl
import time
import math

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Test on 8 top liquid perpetuals to get robust sample size
SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'AVAXUSDT']
MAKER_FEE = 0.0002   # 0.02% maker
TAKER_FEE = 0.00055  # 0.055% taker

def fetch_klines(symbol, interval, limit=1500):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
        data = json.loads(resp.read().decode())
        raw = data.get('result', {}).get('list', [])
        candles = []
        for r in reversed(raw):
            candles.append({
                'time': int(r[0]),
                'open': float(r[1]),
                'high': float(r[2]),
                'low': float(r[3]),
                'close': float(r[4]),
                'volume': float(r[5])
            })
        return candles

def calc_sma(values, period):
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(values[i])
        else:
            out.append(sum(values[i - period + 1 : i + 1]) / period)
    return out

def calc_ema(values, period):
    if len(values) < period:
        return [values[-1]] * len(values) if values else []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(values[i] * k + out[-1] * (1 - k))
    return out

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out = [50.0] * period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        g = max(diff, 0.0)
        l = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - (100.0 / (1.0 + rs)))
    while len(out) < len(closes):
        out.insert(0, 50.0)
    return out

def calc_vwap(candles, window=288):
    vwaps = []
    stdevs = []
    for i in range(len(candles)):
        start_idx = max(0, i - window + 1)
        slice_ = candles[start_idx : i + 1]
        pv_sum = sum(((c['high'] + c['low'] + c['close']) / 3.0) * c['volume'] for c in slice_)
        v_sum = sum(c['volume'] for c in slice_)
        if v_sum == 0:
            vwap = candles[i]['close']
        else:
            vwap = pv_sum / v_sum
        vwaps.append(vwap)
        if v_sum > 0:
            weighted_var = sum(c['volume'] * (((c['high'] + c['low'] + c['close']) / 3.0 - vwap) ** 2) for c in slice_) / v_sum
            stdevs.append(math.sqrt(weighted_var))
        else:
            stdevs.append(0.0)
    return vwaps, stdevs

def simulate_trade(candles, entry_idx, side, entry_price, tp1_pct, sl_pct, be_trigger_pct=0.20, runner_pct=0.10, is_maker=True, max_bars=50):
    pos_size = 100.0  # $100 notional ($10 margin @ 10x)
    fee_entry = (pos_size * MAKER_FEE) if is_maker else (pos_size * TAKER_FEE)
    
    tp1_price = entry_price * (1 + tp1_pct / 100) if side == 'LONG' else entry_price * (1 - tp1_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100) if side == 'LONG' else entry_price * (1 + sl_pct / 100)
    be_price = entry_price * (1 + be_trigger_pct / 100) if side == 'LONG' else entry_price * (1 - be_trigger_pct / 100)
    
    be_active = False
    current_sl = sl_price
    
    for i in range(entry_idx + 1, min(len(candles), entry_idx + max_bars + 1)):
        c = candles[i]
        
        if side == 'LONG':
            if not be_active and c['high'] >= be_price:
                be_active = True
                current_sl = entry_price
                
            if c['high'] >= tp1_price:
                bank_pnl = (0.90 * pos_size) * (tp1_pct / 100)
                runner_exit = c['close']
                runner_pnl = (runner_pct * pos_size) * ((runner_exit - entry_price) / entry_price)
                fee_exit = pos_size * MAKER_FEE
                net_pnl = bank_pnl + runner_pnl - fee_entry - fee_exit
                return {'result': 'WIN', 'pnl': net_pnl, 'bars': i - entry_idx}
                
            if c['low'] <= current_sl:
                if be_active:
                    fee_exit = pos_size * TAKER_FEE
                    net_pnl = 0.0 - fee_entry - fee_exit
                    return {'result': 'BE', 'pnl': net_pnl, 'bars': i - entry_idx}
                else:
                    loss_pct = (entry_price - current_sl) / entry_price
                    gross_loss = -pos_size * loss_pct
                    fee_exit = pos_size * TAKER_FEE
                    net_pnl = gross_loss - fee_entry - fee_exit
                    return {'result': 'LOSS', 'pnl': net_pnl, 'bars': i - entry_idx}
        else:
            if not be_active and c['low'] <= be_price:
                be_active = True
                current_sl = entry_price
                
            if c['low'] <= tp1_price:
                bank_pnl = (0.90 * pos_size) * (tp1_pct / 100)
                runner_exit = c['close']
                runner_pnl = (runner_pct * pos_size) * ((entry_price - runner_exit) / entry_price)
                fee_exit = pos_size * MAKER_FEE
                net_pnl = bank_pnl + runner_pnl - fee_entry - fee_exit
                return {'result': 'WIN', 'pnl': net_pnl, 'bars': i - entry_idx}
                
            if c['high'] >= current_sl:
                if be_active:
                    fee_exit = pos_size * TAKER_FEE
                    net_pnl = 0.0 - fee_entry - fee_exit
                    return {'result': 'BE', 'pnl': net_pnl, 'bars': i - entry_idx}
                else:
                    loss_pct = (current_sl - entry_price) / entry_price
                    gross_loss = -pos_size * loss_pct
                    fee_exit = pos_size * TAKER_FEE
                    net_pnl = gross_loss - fee_entry - fee_exit
                    return {'result': 'LOSS', 'pnl': net_pnl, 'bars': i - entry_idx}
                    
    c_last = candles[min(len(candles)-1, entry_idx + max_bars)]
    diff_pct = (c_last['close'] - entry_price) / entry_price if side == 'LONG' else (entry_price - c_last['close']) / entry_price
    pnl = pos_size * diff_pct - fee_entry - (pos_size * TAKER_FEE)
    res = 'WIN' if pnl > 0 else ('BE' if abs(diff_pct) < 0.001 else 'LOSS')
    return {'result': res, 'pnl': pnl, 'bars': max_bars}

def run_deepdive():
    print("================================================================================")
    print("      DEEP DIVE: LIQUIDATION CLIMAX & VWAP BAND REVERSION OPTIMIZATION")
    print("================================================================================")
    
    market_data = {}
    for s in SYMBOLS:
        print(f"Fetching {s} 5m klines from Bybit (1500 bars)...")
        try:
            k5 = fetch_klines(s, '5', 1500)
            market_data[s] = k5
            time.sleep(0.2)
        except Exception as e:
            print(f"Error fetching {s}: {e}")
            
    # Test matrix of variations:
    # 1. Liq Climax 2.5x vol, 45% wick, TP 0.50%
    # 2. Liq Climax 3.0x vol, 50% wick, TP 0.50%
    # 3. Liq Climax 3.0x vol, 50% wick, TP 0.60%, Trend Filtered (200 EMA)
    # 4. Liq Climax + RSI Extreme (<30 / >70)
    # 5. VWAP 1.8-Sigma Reversion (TP 0.50%, SL 0.60%, BE 0.20%)
    # 6. VWAP 2.0-Sigma Reversion (TP 0.60%, SL 0.70%, BE 0.20%)
    # 7. VWAP 2.2-Sigma Reversion (TP 0.70%, SL 0.80%, BE 0.25%)
    # 8. Hybrid: VWAP 2.0-Sigma + Volume Climax
    
    tests = {
        "A. Liq Climax (2.5x Vol, 45% Wick, TP 0.50%)": [],
        "B. Liq Climax (3.0x Vol, 50% Wick, TP 0.55%)": [],
        "C. Liq Climax Trend-Filtered (with 200 EMA)": [],
        "D. Liq Climax + RSI Extreme (<32 / >68)": [],
        "E. VWAP 1.8-Sigma Snapback (TP 0.50%, BE 0.18%)": [],
        "F. VWAP 2.0-Sigma Snapback (TP 0.60%, BE 0.20%)": [],
        "G. VWAP 2.2-Sigma Snapback (TP 0.70%, BE 0.22%)": [],
        "H. Hybrid: VWAP 2.0-Sigma + Vol Climax": []
    }
    
    for s, k5 in market_data.items():
        closes = [c['close'] for c in k5]
        volumes = [c['volume'] for c in k5]
        vol_sma20 = calc_sma(volumes, 20)
        ema200 = calc_ema(closes, 200)
        rsi14 = calc_rsi(closes, 14)
        vwap, vwap_stdev = calc_vwap(k5, 288)
        
        for i in range(288, len(k5) - 30):
            c = k5[i]
            prev_c = k5[i-1]
            bar_range = c['high'] - c['low']
            if bar_range == 0 or vol_sma20[i] == 0: continue
            
            vol_ratio = c['volume'] / vol_sma20[i]
            lower_wick = min(c['open'], c['close']) - c['low']
            upper_wick = c['high'] - max(c['open'], c['close'])
            lower_wick_ratio = lower_wick / bar_range
            upper_wick_ratio = upper_wick / bar_range
            
            # Setup A: 2.5x Vol, 45% Wick
            if vol_ratio >= 2.5:
                if lower_wick_ratio >= 0.45 and c['close'] > c['open']:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18, is_maker=True)
                    tests["A. Liq Climax (2.5x Vol, 45% Wick, TP 0.50%)"].append(res)
                elif upper_wick_ratio >= 0.45 and c['close'] < c['open']:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18, is_maker=True)
                    tests["A. Liq Climax (2.5x Vol, 45% Wick, TP 0.50%)"].append(res)
                    
            # Setup B: 3.0x Vol, 50% Wick
            if vol_ratio >= 3.0:
                if lower_wick_ratio >= 0.50 and c['close'] > c['open']:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.55, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["B. Liq Climax (3.0x Vol, 50% Wick, TP 0.55%)"].append(res)
                elif upper_wick_ratio >= 0.50 and c['close'] < c['open']:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.55, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["B. Liq Climax (3.0x Vol, 50% Wick, TP 0.55%)"].append(res)

            # Setup C: Liq Climax Trend-Filtered (with 200 EMA)
            if vol_ratio >= 2.5:
                if lower_wick_ratio >= 0.45 and c['close'] > ema200[i] and c['close'] > c['open']:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.55, sl_pct=0.60, be_trigger_pct=0.18, is_maker=True)
                    tests["C. Liq Climax Trend-Filtered (with 200 EMA)"].append(res)
                elif upper_wick_ratio >= 0.45 and c['close'] < ema200[i] and c['close'] < c['open']:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.55, sl_pct=0.60, be_trigger_pct=0.18, is_maker=True)
                    tests["C. Liq Climax Trend-Filtered (with 200 EMA)"].append(res)

            # Setup D: Liq Climax + RSI Extreme
            if vol_ratio >= 2.5:
                if lower_wick_ratio >= 0.45 and rsi14[i] <= 32:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["D. Liq Climax + RSI Extreme (<32 / >68)"].append(res)
                elif upper_wick_ratio >= 0.45 and rsi14[i] >= 68:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["D. Liq Climax + RSI Extreme (<32 / >68)"].append(res)

            # VWAP calculations
            if vwap_stdev[i] > 0 and vwap_stdev[i-1] > 0:
                dist = (c['close'] - vwap[i]) / vwap_stdev[i]
                prev_dist = (prev_c['close'] - vwap[i-1]) / vwap_stdev[i-1]
                
                # E: VWAP 1.8-Sigma Snapback
                if prev_dist < -1.8 and dist >= -1.8 and rsi14[i] < 40:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.50, sl_pct=0.55, be_trigger_pct=0.18, is_maker=True)
                    tests["E. VWAP 1.8-Sigma Snapback (TP 0.50%, BE 0.18%)"].append(res)
                elif prev_dist > 1.8 and dist <= 1.8 and rsi14[i] > 60:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.50, sl_pct=0.55, be_trigger_pct=0.18, is_maker=True)
                    tests["E. VWAP 1.8-Sigma Snapback (TP 0.50%, BE 0.18%)"].append(res)
                    
                # F: VWAP 2.0-Sigma Snapback
                if prev_dist < -2.0 and dist >= -2.0:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["F. VWAP 2.0-Sigma Snapback (TP 0.60%, BE 0.20%)"].append(res)
                elif prev_dist > 2.0 and dist <= 2.0:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["F. VWAP 2.0-Sigma Snapback (TP 0.60%, BE 0.20%)"].append(res)
                    
                # G: VWAP 2.2-Sigma Snapback
                if prev_dist < -2.2 and dist >= -2.2:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.70, sl_pct=0.75, be_trigger_pct=0.22, is_maker=True)
                    tests["G. VWAP 2.2-Sigma Snapback (TP 0.70%, BE 0.22%)"].append(res)
                elif prev_dist > 2.2 and dist <= 2.2:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.70, sl_pct=0.75, be_trigger_pct=0.22, is_maker=True)
                    tests["G. VWAP 2.2-Sigma Snapback (TP 0.70%, BE 0.22%)"].append(res)
                    
                # H: Hybrid: VWAP 2.0-Sigma + Vol Climax (Confluence)
                if prev_dist < -2.0 and dist >= -2.0 and vol_ratio >= 1.8:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["H. Hybrid: VWAP 2.0-Sigma + Vol Climax"].append(res)
                elif prev_dist > 2.0 and dist <= 2.0 and vol_ratio >= 1.8:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    tests["H. Hybrid: VWAP 2.0-Sigma + Vol Climax"].append(res)

    print("\n" + "=" * 98)
    print(f"{'Strategy Architecture':<52} | {'Trades':<6} | {'Win %':<6} | {'No-Loss %':<9} | {'PF':<5} | {'Net PnL':<8}")
    print("=" * 98)
    
    summary = {}
    for name, trade_list in tests.items():
        n = len(trade_list)
        if n == 0:
            print(f"{name:<52} | 0      | N/A    | N/A       | N/A   | $0.00")
            summary[name] = {'trades': 0}
            continue
        wins = [t for t in trade_list if t['result'] == 'WIN']
        bes = [t for t in trade_list if t['result'] == 'BE']
        losses = [t for t in trade_list if t['result'] == 'LOSS']
        
        win_rate = (len(wins) / n) * 100
        no_loss_rate = ((len(wins) + len(bes)) / n) * 100
        gross_profit = sum(t['pnl'] for t in wins if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in losses if t['pnl'] < 0)) + abs(sum(t['pnl'] for t in bes if t['pnl'] < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
        total_pnl = sum(t['pnl'] for t in trade_list)
        proj_100 = (total_pnl / n) * 100
        
        summary[name] = {
            'trades': n,
            'wins': len(wins),
            'be': len(bes),
            'losses': len(losses),
            'win_rate': win_rate,
            'no_loss_rate': no_loss_rate,
            'profit_factor': pf,
            'total_net_pnl': total_pnl,
            'proj_100_pnl': proj_100
        }
        
        print(f"{name:<52} | {n:<6} | {win_rate:5.1f}% | {no_loss_rate:8.1f}% | {pf:5.2f} | ${total_pnl:6.2f}")
        
    print("=" * 98)
    with open('scratch/deepdive_results.json', 'w') as f:
        json.dump(summary, f, indent=2)

if __name__ == '__main__':
    run_deepdive()

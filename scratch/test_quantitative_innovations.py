import urllib.request
import json
import ssl
import time
import math
from datetime import datetime, timezone

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']
MAKER_FEE = 0.0002   # 0.02% maker
TAKER_FEE = 0.00055  # 0.055% taker

def fetch_klines(symbol, interval, limit=1200):
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

def calc_ema(values, period):
    if len(values) < period:
        return [values[-1]] * len(values) if values else []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(values[i] * k + out[-1] * (1 - k))
    return out

def calc_sma(values, period):
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(values[i])
        else:
            out.append(sum(values[i - period + 1 : i + 1]) / period)
    return out

def calc_stdev(values, period):
    smas = calc_sma(values, period)
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(0.0)
        else:
            slice_ = values[i - period + 1 : i + 1]
            m = smas[i]
            var = sum((x - m) ** 2 for x in slice_) / period
            out.append(math.sqrt(var))
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

def calc_atr(candles, period=14):
    trs = [candles[0]['high'] - candles[0]['low']]
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr = max(c['high'] - c['low'], abs(c['high'] - p['close']), abs(c['low'] - p['close']))
        trs.append(tr)
    atr = [trs[0]]
    k = 1.0 / period
    for i in range(1, len(trs)):
        atr.append(atr[-1] * (1 - k) + trs[i] * k)
    return atr

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

def calc_stoch_rsi(closes, period=14, stoch_period=14, smooth_k=3, smooth_d=3):
    rsi = calc_rsi(closes, period)
    stoch = []
    for i in range(len(rsi)):
        if i < stoch_period - 1:
            stoch.append(50.0)
        else:
            window = rsi[i - stoch_period + 1 : i + 1]
            min_r = min(window)
            max_r = max(window)
            if max_r - min_r == 0:
                stoch.append(50.0)
            else:
                stoch.append(((rsi[i] - min_r) / (max_r - min_r)) * 100.0)
    k = calc_sma(stoch, smooth_k)
    d = calc_sma(k, smooth_d)
    return k, d

def simulate_trade(candles, entry_idx, side, entry_price, tp1_pct, sl_pct, be_trigger_pct=0.20, runner_pct=0.10, is_maker=True, max_bars=60):
    pos_size = 100.0  # $100 notional ($10 margin at 10x)
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

def test_suite():
    print("================================================================================")
    print("      TESTING 8 NOVEL QUANTITATIVE STRATEGIES ON LIVE BYBIT PERPETUALS")
    print("================================================================================")
    
    market_data = {}
    for s in SYMBOLS:
        print(f"Fetching {s} 5m and 15m klines from Bybit...")
        k5 = fetch_klines(s, '5', 1200)
        k15 = fetch_klines(s, '15', 1200)
        market_data[s] = {'5m': k5, '15m': k15}
        time.sleep(0.3)
        
    strategies = {
        "1. VWAP 2.5-Sigma Mean Reversion (Institutional Value)": [],
        "2. Liquidation Cascade Exhaustion (Volume Climax Hammer)": [],
        "3. ICT Asian Range Sweep & London/NY Expansion": [],
        "4. 15m Supertrend + Pullback Confluence": [],
        "5. Bollinger Squeeze Volatility Expansion + Momentum": [],
        "6. 80-20 Institutional Day Reversal (Raschke Rule)": [],
        "7. Trend EMA Ribbon + Stochastic Divergence Scalp": [],
        "8. Passive Maker Grid-Spread Scalp (Ranging Filter)": []
    }
    
    for s in SYMBOLS:
        k5 = market_data[s]['5m']
        k15 = market_data[s]['15m']
        
        c5_closes = [c['close'] for c in k5]
        c5_volumes = [c['volume'] for c in k5]
        ema9 = calc_ema(c5_closes, 9)
        ema21 = calc_ema(c5_closes, 21)
        ema50 = calc_ema(c5_closes, 50)
        ema200 = calc_ema(c5_closes, 200)
        sma20 = calc_sma(c5_closes, 20)
        stdev20 = calc_stdev(c5_closes, 20)
        rsi14 = calc_rsi(c5_closes, 14)
        atr14 = calc_atr(k5, 14)
        vol_sma20 = calc_sma(c5_volumes, 20)
        vwap, vwap_stdev = calc_vwap(k5, 288)
        stoch_k, stoch_d = calc_stoch_rsi(c5_closes, 14, 14, 3, 3)
        
        # 1. VWAP 2.5-Sigma Mean Reversion
        for i in range(288, len(k5) - 30):
            if vwap_stdev[i] == 0: continue
            dist = (k5[i]['close'] - vwap[i]) / vwap_stdev[i]
            prev_dist = (k5[i-1]['close'] - vwap[i-1]) / vwap_stdev[i-1]
            
            if prev_dist < -2.2 and dist >= -2.0 and rsi14[i] < 35:
                res = simulate_trade(k5, i, 'LONG', k5[i]['close'], tp1_pct=0.60, sl_pct=0.70, be_trigger_pct=0.20, is_maker=True)
                strategies["1. VWAP 2.5-Sigma Mean Reversion (Institutional Value)"].append(res)
            elif prev_dist > 2.2 and dist <= 2.0 and rsi14[i] > 65:
                res = simulate_trade(k5, i, 'SHORT', k5[i]['close'], tp1_pct=0.60, sl_pct=0.70, be_trigger_pct=0.20, is_maker=True)
                strategies["1. VWAP 2.5-Sigma Mean Reversion (Institutional Value)"].append(res)
                
        # 2. Liquidation Cascade Exhaustion (Volume Climax Hammer)
        for i in range(50, len(k5) - 30):
            c = k5[i]
            bar_range = c['high'] - c['low']
            if bar_range == 0 or vol_sma20[i] == 0: continue
            
            vol_ratio = c['volume'] / vol_sma20[i]
            if vol_ratio > 3.0:
                lower_wick = min(c['open'], c['close']) - c['low']
                upper_wick = c['high'] - max(c['open'], c['close'])
                
                if lower_wick / bar_range >= 0.50 and c['close'] > c['open'] and rsi14[i] < 45:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.55, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    strategies["2. Liquidation Cascade Exhaustion (Volume Climax Hammer)"].append(res)
                elif upper_wick / bar_range >= 0.50 and c['close'] < c['open'] and rsi14[i] > 55:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.55, sl_pct=0.65, be_trigger_pct=0.20, is_maker=True)
                    strategies["2. Liquidation Cascade Exhaustion (Volume Climax Hammer)"].append(res)

        # 3. ICT Asian Range Sweep & London/NY Expansion
        for i in range(200, len(k5) - 30):
            dt = datetime.fromtimestamp(k5[i]['time'] / 1000, tz=timezone.utc)
            if 8 <= dt.hour <= 16:
                asian_bars = []
                for b_idx in range(i - 1, max(0, i - 150), -1):
                    b_dt = datetime.fromtimestamp(k5[b_idx]['time'] / 1000, tz=timezone.utc)
                    if b_dt.day == dt.day and 0 <= b_dt.hour < 8:
                        asian_bars.append(k5[b_idx])
                    elif b_dt.day != dt.day:
                        break
                if len(asian_bars) >= 40:
                    asian_high = max(b['high'] for b in asian_bars)
                    asian_low = min(b['low'] for b in asian_bars)
                    prev_c = k5[i-1]
                    curr_c = k5[i]
                    if prev_c['low'] < asian_low and curr_c['close'] > asian_low and (asian_low - prev_c['low']) / asian_low < 0.008:
                        res = simulate_trade(k5, i, 'LONG', curr_c['close'], tp1_pct=0.65, sl_pct=0.50, be_trigger_pct=0.25, is_maker=True)
                        strategies["3. ICT Asian Range Sweep & London/NY Expansion"].append(res)
                    elif prev_c['high'] > asian_high and curr_c['close'] < asian_high and (prev_c['high'] - asian_high) / asian_high < 0.008:
                        res = simulate_trade(k5, i, 'SHORT', curr_c['close'], tp1_pct=0.65, sl_pct=0.50, be_trigger_pct=0.25, is_maker=True)
                        strategies["3. ICT Asian Range Sweep & London/NY Expansion"].append(res)

        # 4. 15m Supertrend + Pullback Confluence
        c15_closes = [c['close'] for c in k15]
        ema15_21 = calc_ema(c15_closes, 21)
        ema15_50 = calc_ema(c15_closes, 50)
        atr15 = calc_atr(k15, 10)
        
        for i in range(50, len(k15) - 30):
            c = k15[i]
            prev = k15[i-1]
            if ema15_21[i] > ema15_50[i]:
                if prev['low'] <= ema15_21[i] and c['close'] > ema15_21[i] and c['close'] > c['open']:
                    tp_pct = (1.5 * atr15[i] / c['close']) * 100
                    sl_pct = (1.0 * atr15[i] / c['close']) * 100
                    res = simulate_trade(k15, i, 'LONG', c['close'], tp1_pct=tp_pct, sl_pct=sl_pct, be_trigger_pct=tp_pct*0.5, is_maker=True, max_bars=40)
                    strategies["4. 15m Supertrend + Pullback Confluence"].append(res)
            elif ema15_21[i] < ema15_50[i]:
                if prev['high'] >= ema15_21[i] and c['close'] < ema15_21[i] and c['close'] < c['open']:
                    tp_pct = (1.5 * atr15[i] / c['close']) * 100
                    sl_pct = (1.0 * atr15[i] / c['close']) * 100
                    res = simulate_trade(k15, i, 'SHORT', c['close'], tp1_pct=tp_pct, sl_pct=sl_pct, be_trigger_pct=tp_pct*0.5, is_maker=True, max_bars=40)
                    strategies["4. 15m Supertrend + Pullback Confluence"].append(res)

        # 5. Bollinger Squeeze Volatility Expansion + Momentum
        bandwidths = [(2 * 2.0 * stdev20[j] / sma20[j]) if sma20[j] > 0 else 0 for j in range(len(k5))]
        for i in range(50, len(k5) - 30):
            bw_slice = bandwidths[i-50 : i]
            min_bw = min(bw_slice)
            if bandwidths[i-1] <= min_bw * 1.15 and bandwidths[i] > bandwidths[i-1] * 1.10:
                c = k5[i]
                upper = sma20[i] + 2.0 * stdev20[i]
                lower = sma20[i] - 2.0 * stdev20[i]
                if c['close'] > upper and rsi14[i] > 60:
                    res = simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.75, sl_pct=0.55, be_trigger_pct=0.25, is_maker=False)
                    strategies["5. Bollinger Squeeze Volatility Expansion + Momentum"].append(res)
                elif c['close'] < lower and rsi14[i] < 40:
                    res = simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.75, sl_pct=0.55, be_trigger_pct=0.25, is_maker=False)
                    strategies["5. Bollinger Squeeze Volatility Expansion + Momentum"].append(res)

        # 6. 80-20 Institutional Day/Swing Reversal (Raschke Rule)
        for i in range(20, len(k15) - 30):
            prev = k15[i-1]
            rng = prev['high'] - prev['low']
            if rng == 0: continue
            if (prev['open'] - prev['low']) / rng <= 0.20 and (prev['high'] - prev['close']) / rng <= 0.20:
                curr = k15[i]
                if curr['high'] > prev['high'] and curr['close'] < prev['high']:
                    res = simulate_trade(k15, i, 'SHORT', curr['close'], tp1_pct=0.70, sl_pct=0.50, be_trigger_pct=0.25, is_maker=True, max_bars=30)
                    strategies["6. 80-20 Institutional Day Reversal (Raschke Rule)"].append(res)
            elif (prev['high'] - prev['open']) / rng <= 0.20 and (prev['close'] - prev['low']) / rng <= 0.20:
                curr = k15[i]
                if curr['low'] < prev['low'] and curr['close'] > prev['low']:
                    res = simulate_trade(k15, i, 'LONG', curr['close'], tp1_pct=0.70, sl_pct=0.50, be_trigger_pct=0.25, is_maker=True, max_bars=30)
                    strategies["6. 80-20 Institutional Day Reversal (Raschke Rule)"].append(res)

        # 7. Trend EMA Ribbon + Stochastic Divergence Scalp
        for i in range(50, len(k5) - 30):
            if ema9[i] > ema21[i] > ema50[i]:
                if stoch_k[i-1] < 20 and stoch_k[i] > stoch_d[i] and stoch_k[i-1] <= stoch_d[i-1]:
                    res = simulate_trade(k5, i, 'LONG', k5[i]['close'], tp1_pct=0.50, sl_pct=0.45, be_trigger_pct=0.20, is_maker=True)
                    strategies["7. Trend EMA Ribbon + Stochastic Divergence Scalp"].append(res)
            elif ema9[i] < ema21[i] < ema50[i]:
                if stoch_k[i-1] > 80 and stoch_k[i] < stoch_d[i] and stoch_k[i-1] >= stoch_d[i-1]:
                    res = simulate_trade(k5, i, 'SHORT', k5[i]['close'], tp1_pct=0.50, sl_pct=0.45, be_trigger_pct=0.20, is_maker=True)
                    strategies["7. Trend EMA Ribbon + Stochastic Divergence Scalp"].append(res)

        # 8. Passive Maker Grid-Spread Scalp (Ranging Filter)
        atr_sma = calc_sma(atr14, 50)
        for i in range(50, len(k5) - 30):
            if atr14[i] < atr_sma[i] * 0.85:
                if k5[i]['close'] < sma20[i] - 1.0 * stdev20[i]:
                    res = simulate_trade(k5, i, 'LONG', k5[i]['close'], tp1_pct=0.35, sl_pct=0.40, be_trigger_pct=0.18, is_maker=True)
                    strategies["8. Passive Maker Grid-Spread Scalp (Ranging Filter)"].append(res)
                elif k5[i]['close'] > sma20[i] + 1.0 * stdev20[i]:
                    res = simulate_trade(k5, i, 'SHORT', k5[i]['close'], tp1_pct=0.35, sl_pct=0.40, be_trigger_pct=0.18, is_maker=True)
                    strategies["8. Passive Maker Grid-Spread Scalp (Ranging Filter)"].append(res)

    summary_results = {}
    print("\n" + "=" * 95)
    print(f"{'Strategy Architecture':<52} | {'Trades':<6} | {'Win %':<6} | {'No-Loss %':<9} | {'PF':<5} | {'Net PnL':<8}")
    print("=" * 95)
    
    for name, trade_list in strategies.items():
        n = len(trade_list)
        if n == 0:
            print(f"{name:<52} | 0      | N/A    | N/A       | N/A   | $0.00")
            summary_results[name] = {'trades': 0}
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
        avg_bars = sum(t['bars'] for t in trade_list) / n
        
        summary_results[name] = {
            'trades': n,
            'wins': len(wins),
            'be': len(bes),
            'losses': len(losses),
            'win_rate': win_rate,
            'no_loss_rate': no_loss_rate,
            'profit_factor': pf,
            'total_net_pnl': total_pnl,
            'proj_100_pnl': proj_100,
            'avg_bars': avg_bars
        }
        
        print(f"{name:<52} | {n:<6} | {win_rate:5.1f}% | {no_loss_rate:8.1f}% | {pf:5.2f} | ${total_pnl:6.2f}")
        
    with open('scratch/quantitative_innovations_results.json', 'w') as f:
        json.dump(summary_results, f, indent=2)
        
    print("=" * 95)
    print("Saved results to scratch/quantitative_innovations_results.json")

if __name__ == '__main__':
    test_suite()

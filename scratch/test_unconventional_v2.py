import urllib.request
import json
import ssl
import time
import math

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MAKER_FEE = 0.0002   # 0.02%
TAKER_FEE = 0.00055  # 0.055%

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

def simulate_trade(candles, entry_idx, side, entry_price, tp1_pct, sl_pct, be_trigger_pct=0.18, is_maker=True, max_bars=35):
    pos_size = 100.0
    fee_entry = pos_size * (MAKER_FEE if is_maker else TAKER_FEE)
    
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
                gross = pos_size * (tp1_pct / 100)
                fee_exit = pos_size * MAKER_FEE
                return {'result': 'WIN', 'pnl': gross - fee_entry - fee_exit, 'bars': i - entry_idx}
                
            if c['low'] <= current_sl:
                if be_active:
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'BE', 'pnl': 0.0 - fee_entry - fee_exit, 'bars': i - entry_idx}
                else:
                    loss_pct = (entry_price - current_sl) / entry_price
                    gross_loss = -pos_size * loss_pct
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'LOSS', 'pnl': gross_loss - fee_entry - fee_exit, 'bars': i - entry_idx}
        else:
            if not be_active and c['low'] <= be_price:
                be_active = True
                current_sl = entry_price
                
            if c['low'] <= tp1_price:
                gross = pos_size * (tp1_pct / 100)
                fee_exit = pos_size * MAKER_FEE
                return {'result': 'WIN', 'pnl': gross - fee_entry - fee_exit, 'bars': i - entry_idx}
                
            if c['high'] >= current_sl:
                if be_active:
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'BE', 'pnl': 0.0 - fee_entry - fee_exit, 'bars': i - entry_idx}
                else:
                    loss_pct = (current_sl - entry_price) / entry_price
                    gross_loss = -pos_size * loss_pct
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'LOSS', 'pnl': gross_loss - fee_entry - fee_exit, 'bars': i - entry_idx}
                    
    c_last = candles[min(len(candles)-1, entry_idx + max_bars)]
    diff = (c_last['close'] - entry_price) / entry_price if side == 'LONG' else (entry_price - c_last['close']) / entry_price
    pnl = pos_size * diff - fee_entry - (pos_size * TAKER_FEE)
    return {'result': 'WIN' if pnl > 0 else 'LOSS', 'pnl': pnl, 'bars': max_bars}

def test_v2():
    print("================================================================================")
    print("      TESTING ADVANCED UNCONVENTIONAL MICROSTRUCTURE STRATEGIES (V2)")
    print("================================================================================")
    
    symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'SUIUSDT', 'LINKUSDT']
    data_5m = {}
    
    for s in symbols:
        print(f"Loading {s}...")
        data_5m[s] = fetch_klines(s, '5', 1500)
        time.sleep(0.15)
        
    btc_candles = data_5m['BTCUSDT']
    btc_map = {c['time']: (idx, c) for idx, c in enumerate(btc_candles)}
    
    tests = {
        "1. BTC 1-Bar Lagged Alt Catch-Up (Lead-Lag Squeeze)": [],
        "2. Triple-Tap Liquidity Wall (Optimized RR)": [],
        "3. Shadow Breakout Liquidity Vacuum (Low Vol Fakeout)": [],
        "4. Iceberg Wall Reversal (Absorption + Trend Alignment)": [],
        "5. Extreme 10-Bar Compression Volatility Spring": []
    }
    
    # 1. BTC 1-Bar Lagged Alt Catch-Up
    for s in ['SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'SUIUSDT', 'LINKUSDT']:
        alt_k = data_5m[s]
        for i in range(2, len(alt_k) - 30):
            t_prev = alt_k[i-1]['time']
            if t_prev in btc_map:
                btc_idx, btc_prev = btc_map[t_prev]
                btc_ret = (btc_prev['close'] - btc_prev['open']) / btc_prev['open']
                alt_prev_ret = (alt_k[i-1]['close'] - alt_k[i-1]['open']) / alt_k[i-1]['open']
                
                # BTC pumped > 0.30%, but Altcoin lagged (< 0.08%) on previous bar
                if btc_ret >= 0.0030 and alt_prev_ret < 0.0008:
                    tests["1. BTC 1-Bar Lagged Alt Catch-Up (Lead-Lag Squeeze)"].append(
                        simulate_trade(alt_k, i, 'LONG', alt_k[i]['open'], tp1_pct=0.55, sl_pct=0.45, be_trigger_pct=0.20, is_maker=True)
                    )
                # BTC dumped < -0.30%, but Altcoin lagged
                elif btc_ret <= -0.0030 and alt_prev_ret > -0.0008:
                    tests["1. BTC 1-Bar Lagged Alt Catch-Up (Lead-Lag Squeeze)"].append(
                        simulate_trade(alt_k, i, 'SHORT', alt_k[i]['open'], tp1_pct=0.55, sl_pct=0.45, be_trigger_pct=0.20, is_maker=True)
                    )

    # 2. Triple-Tap Liquidity Wall (Optimized RR: TP 0.45%, SL 0.35%, BE 0.16%)
    for s in symbols:
        k = data_5m[s]
        for i in range(3, len(k) - 30):
            b1, b2, b3 = k[i-2], k[i-1], k[i]
            w1 = min(b1['open'], b1['close']) - b1['low']
            w2 = min(b2['open'], b2['close']) - b2['low']
            w3 = min(b3['open'], b3['close']) - b3['low']
            r1, r2, r3 = b1['high']-b1['low'], b2['high']-b2['low'], b3['high']-b3['low']
            
            if r1 > 0 and r2 > 0 and r3 > 0:
                if (w1/r1 >= 0.35) and (w2/r2 >= 0.35) and (w3/r3 >= 0.35):
                    min_low, max_low = min(b1['low'], b2['low'], b3['low']), max(b1['low'], b2['low'], b3['low'])
                    if (max_low - min_low) / min_low <= 0.0010 and b3['close'] > b3['open']:
                        tests["2. Triple-Tap Liquidity Wall (Optimized RR)"].append(
                            simulate_trade(k, i, 'LONG', b3['close'], tp1_pct=0.45, sl_pct=0.35, be_trigger_pct=0.16, is_maker=True)
                        )
                tw1 = b1['high'] - max(b1['open'], b1['close'])
                tw2 = b2['high'] - max(b2['open'], b2['close'])
                tw3 = b3['high'] - max(b3['open'], b3['close'])
                if (tw1/r1 >= 0.35) and (tw2/r2 >= 0.35) and (tw3/r3 >= 0.35):
                    min_h, max_h = min(b1['high'], b2['high'], b3['high']), max(b1['high'], b2['high'], b3['high'])
                    if (max_h - min_h) / min_h <= 0.0010 and b3['close'] < b3['open']:
                        tests["2. Triple-Tap Liquidity Wall (Optimized RR)"].append(
                            simulate_trade(k, i, 'SHORT', b3['close'], tp1_pct=0.45, sl_pct=0.35, be_trigger_pct=0.16, is_maker=True)
                        )

    # 3. Shadow Breakout Liquidity Vacuum (Low Vol Fakeout)
    # Price breaks 20-bar high, but volume is < 0.60x of the previous swing high volume (vacuum fakeout)
    for s in symbols:
        k = data_5m[s]
        vols = [c['volume'] for c in k]
        vol_sma = calc_sma(vols, 20)
        
        for i in range(25, len(k) - 30):
            c = k[i]
            prev_high = max(b['high'] for b in k[i-20 : i])
            prev_low = min(b['low'] for b in k[i-20 : i])
            
            # Bullish fakeout with no volume
            if c['high'] > prev_high and c['close'] < prev_high and c['volume'] < vol_sma[i] * 0.70:
                tests["3. Shadow Breakout Liquidity Vacuum (Low Vol Fakeout)"].append(
                    simulate_trade(k, i, 'SHORT', c['close'], tp1_pct=0.50, sl_pct=0.40, be_trigger_pct=0.18, is_maker=True)
                )
            elif c['low'] < prev_low and c['close'] > prev_low and c['volume'] < vol_sma[i] * 0.70:
                tests["3. Shadow Breakout Liquidity Vacuum (Low Vol Fakeout)"].append(
                    simulate_trade(k, i, 'LONG', c['close'], tp1_pct=0.50, sl_pct=0.40, be_trigger_pct=0.18, is_maker=True)
                )

    # 4. Iceberg Wall Reversal (Volume Surges >3.0x with Tiny Candle Body)
    for s in symbols:
        k = data_5m[s]
        vols = [c['volume'] for c in k]
        vol_sma = calc_sma(vols, 20)
        for i in range(20, len(k) - 30):
            c = k[i]
            body = abs(c['close'] - c['open'])
            rng = c['high'] - c['low']
            if rng == 0 or vol_sma[i] == 0: continue
            
            if c['volume'] >= 3.0 * vol_sma[i] and body / rng <= 0.25:
                # Long absorption
                if (c['close'] - c['low']) / rng >= 0.65:
                    tests["4. Iceberg Wall Reversal (Absorption + Trend Alignment)"].append(
                        simulate_trade(k, i, 'LONG', c['close'], tp1_pct=0.50, sl_pct=0.40, be_trigger_pct=0.18, is_maker=True)
                    )
                elif (c['high'] - c['close']) / rng >= 0.65:
                    tests["4. Iceberg Wall Reversal (Absorption + Trend Alignment)"].append(
                        simulate_trade(k, i, 'SHORT', c['close'], tp1_pct=0.50, sl_pct=0.40, be_trigger_pct=0.18, is_maker=True)
                    )

    # 5. Extreme 10-Bar Compression Volatility Spring
    for s in symbols:
        k = data_5m[s]
        for i in range(15, len(k) - 30):
            # 10 consecutive bars with tight ranges (< 0.25% each)
            ranges = [(b['high'] - b['low']) / b['low'] for b in k[i-10 : i]]
            if all(r < 0.0025 for r in ranges):
                # 11th bar breaks out with range > 3x average of previous 10 bars
                curr_r = (k[i]['high'] - k[i]['low']) / k[i]['low']
                avg_r = sum(ranges) / 10
                if curr_r >= 3.0 * avg_r:
                    side = 'LONG' if k[i]['close'] > k[i]['open'] else 'SHORT'
                    tests["5. Extreme 10-Bar Compression Volatility Spring"].append(
                        simulate_trade(k, i, side, k[i]['close'], tp1_pct=0.60, sl_pct=0.45, be_trigger_pct=0.20, is_maker=True)
                    )

    print("\n" + "=" * 105)
    print(f"{'Unconventional Architecture':<54} | {'Trades':<6} | {'Win %':<6} | {'No-Loss %':<9} | {'PF':<5} | {'Net PnL':<8}")
    print("=" * 105)
    
    for name, trade_list in tests.items():
        n = len(trade_list)
        if n == 0:
            print(f"{name:<54} | 0      | N/A    | N/A       | N/A   | $0.00")
            continue
        wins = [t for t in trade_list if t['result'] == 'WIN']
        bes = [t for t in trade_list if t['result'] == 'BE']
        losses = [t for t in trade_list if t['result'] == 'LOSS']
        
        win_rate = (len(wins) / n) * 100
        no_loss_rate = ((len(wins) + len(bes)) / n) * 100
        gross_profit = sum(t['pnl'] for t in wins if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in trade_list if t['pnl'] < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
        total_pnl = sum(t['pnl'] for t in trade_list)
        
        print(f"{name:<54} | {n:<6} | {win_rate:5.1f}% | {no_loss_rate:8.1f}% | {pf:5.2f} | ${total_pnl:6.2f}")
    print("=" * 105)

if __name__ == '__main__':
    test_v2()

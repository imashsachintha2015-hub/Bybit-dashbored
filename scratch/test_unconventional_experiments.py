import urllib.request
import json
import ssl
import time
import math
from datetime import datetime, timezone

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

def simulate_trade(candles, entry_idx, side, entry_price, tp1_pct, sl_pct, be_trigger_pct=0.18, is_maker=True, max_bars=40):
    pos_size = 100.0  # $100 notional ($10 margin @ 10x)
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

def run_unconventional_tests():
    print("================================================================================")
    print("    TESTING 6 UNCONVENTIONAL, OUT-OF-THE-BOX STRATEGIES ON LIVE BYBIT DATA")
    print("================================================================================")
    
    symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'SUIUSDT']
    data_5m = {}
    
    for s in symbols:
        print(f"Fetching {s} 5m klines from Bybit...")
        data_5m[s] = fetch_klines(s, '5', 1500)
        time.sleep(0.2)
        
    btc_candles = data_5m['BTCUSDT']
    btc_times = {c['time']: c for c in btc_candles}
    
    strategies = {
        "1. BTC Lead-Lag Frontrunner (Altcoin Latency Squeeze)": [],
        "2. Funding Settlement Glitch (Mechanical 8H Snapback)": [],
        "3. Triple-Tap Liquidity Wall (3-Wick Cluster Rejection)": [],
        "4. Iceberg Absorption Wall (Volume Explodes, Range Contracts)": [],
        "5. Fractal Dimension Noise-to-Trend Spring (Chaos Filter)": [],
        "6. Exhaustion Sequential Run (5 Consecutive Climax Bars)": []
    }
    
    # -------------------------------------------------------------
    # 1. BTC Lead-Lag Frontrunner (Altcoin Latency Squeeze)
    # -------------------------------------------------------------
    # If BTC surges > 0.40% in a 5m bar, but an Altcoin (SOL, XRP, SUI) has only moved < 0.10%,
    # Buy the lagging Altcoin before market makers re-price the book!
    for s in ['ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'SUIUSDT']:
        alt_k = data_5m[s]
        for i in range(1, len(alt_k) - 30):
            t = alt_k[i]['time']
            if t in btc_times:
                # Find matching BTC bar index
                # check BTC return over current bar
                btc_curr = btc_times[t]
                btc_ret = (btc_curr['close'] - btc_curr['open']) / btc_curr['open']
                alt_ret = (alt_k[i]['close'] - alt_k[i]['open']) / alt_k[i]['open']
                
                # BTC Bullish Impulse, Alt lagging
                if btc_ret >= 0.0040 and alt_ret < 0.0015:
                    strategies["1. BTC Lead-Lag Frontrunner (Altcoin Latency Squeeze)"].append(
                        simulate_trade(alt_k, i, 'LONG', alt_k[i]['close'], tp1_pct=0.60, sl_pct=0.50, be_trigger_pct=0.20)
                    )
                # BTC Bearish Impulse, Alt lagging
                elif btc_ret <= -0.0040 and alt_ret > -0.0015:
                    strategies["1. BTC Lead-Lag Frontrunner (Altcoin Latency Squeeze)"].append(
                        simulate_trade(alt_k, i, 'SHORT', alt_k[i]['close'], tp1_pct=0.60, sl_pct=0.50, be_trigger_pct=0.20)
                    )

    # -------------------------------------------------------------
    # 2. Funding Settlement Glitch (Mechanical 8H Snapback)
    # -------------------------------------------------------------
    # Funding clears at 00:00, 08:00, 16:00 UTC.
    # In the bar right at 00:00, 08:00, 16:00, hedges unwind. If prior 30 mins (6 bars) dumped hard into funding,
    # enter LONG at funding print for the mechanical unwind bounce!
    for s in symbols:
        k = data_5m[s]
        for i in range(10, len(k) - 30):
            dt = datetime.fromtimestamp(k[i]['time'] / 1000, tz=timezone.utc)
            # Check if this bar opened at 00:00, 08:00, or 16:00 UTC
            if dt.hour in [0, 8, 16] and dt.minute == 0:
                # check trend of last 6 bars (30 mins before funding)
                prior_6_move = (k[i-1]['close'] - k[i-7]['open']) / k[i-7]['open']
                # Pre-funding dump -> post-funding relief bounce
                if prior_6_move <= -0.0060:
                    strategies["2. Funding Settlement Glitch (Mechanical 8H Snapback)"].append(
                        simulate_trade(k, i, 'LONG', k[i]['close'], tp1_pct=0.50, sl_pct=0.45, be_trigger_pct=0.18, max_bars=12)
                    )
                # Pre-funding pump -> post-funding cooling drop
                elif prior_6_move >= 0.0060:
                    strategies["2. Funding Settlement Glitch (Mechanical 8H Snapback)"].append(
                        simulate_trade(k, i, 'SHORT', k[i]['close'], tp1_pct=0.50, sl_pct=0.45, be_trigger_pct=0.18, max_bars=12)
                    )

    # -------------------------------------------------------------
    # 3. Triple-Tap Liquidity Wall (3-Wick Cluster Rejection)
    # -------------------------------------------------------------
    # 3 consecutive 5m bars all having wicks that touch within 0.08% of each other and reject
    for s in symbols:
        k = data_5m[s]
        for i in range(3, len(k) - 30):
            b1, b2, b3 = k[i-2], k[i-1], k[i]
            
            # Triple Bottom Wicks
            w1 = min(b1['open'], b1['close']) - b1['low']
            w2 = min(b2['open'], b2['close']) - b2['low']
            w3 = min(b3['open'], b3['close']) - b3['low']
            
            rng1 = b1['high'] - b1['low']
            rng2 = b2['high'] - b2['low']
            rng3 = b3['high'] - b3['low']
            
            if rng1 > 0 and rng2 > 0 and rng3 > 0:
                if (w1/rng1 >= 0.35) and (w2/rng2 >= 0.35) and (w3/rng3 >= 0.35):
                    # Check low alignment within 0.12%
                    min_low = min(b1['low'], b2['low'], b3['low'])
                    max_low = max(b1['low'], b2['low'], b3['low'])
                    if (max_low - min_low) / min_low <= 0.0012 and b3['close'] > b3['open']:
                        strategies["3. Triple-Tap Liquidity Wall (3-Wick Cluster Rejection)"].append(
                            simulate_trade(k, i, 'LONG', b3['close'], tp1_pct=0.55, sl_pct=0.45, be_trigger_pct=0.18)
                        )

            # Triple Top Wicks
            tw1 = b1['high'] - max(b1['open'], b1['close'])
            tw2 = b2['high'] - max(b2['open'], b2['close'])
            tw3 = b3['high'] - max(b3['open'], b3['close'])
            
            if rng1 > 0 and rng2 > 0 and rng3 > 0:
                if (tw1/rng1 >= 0.35) and (tw2/rng2 >= 0.35) and (tw3/rng3 >= 0.35):
                    min_high = min(b1['high'], b2['high'], b3['high'])
                    max_high = max(b1['high'], b2['high'], b3['high'])
                    if (max_high - min_high) / min_high <= 0.0012 and b3['close'] < b3['open']:
                        strategies["3. Triple-Tap Liquidity Wall (3-Wick Cluster Rejection)"].append(
                            simulate_trade(k, i, 'SHORT', b3['close'], tp1_pct=0.55, sl_pct=0.45, be_trigger_pct=0.18)
                        )

    # -------------------------------------------------------------
    # 4. Iceberg Absorption Wall (Volume Explodes, Range Contracts)
    # -------------------------------------------------------------
    # Wyckoff Stopping Volume: Volume surges > 2.5x, but candle body is in the bottom 25% of recent bar ranges.
    # This means enormous aggressive orders were dumped directly into a hidden passive iceberg limit wall!
    for s in symbols:
        k = data_5m[s]
        vols = [c['volume'] for c in k]
        vol_sma = calc_sma(vols, 20)
        
        for i in range(20, len(k) - 30):
            c = k[i]
            prev = k[i-1]
            body = abs(c['close'] - c['open'])
            rng = c['high'] - c['low']
            if rng == 0 or vol_sma[i] == 0: continue
            
            # Volume is 2.5x+, but candle range is very small (compression into an iceberg wall)
            if c['volume'] >= 2.5 * vol_sma[i] and body / rng <= 0.30:
                # Downward dump absorbed (close is higher than low by 60% of range)
                if (c['close'] - c['low']) / rng >= 0.60:
                    strategies["4. Iceberg Absorption Wall (Volume Explodes, Range Contracts)"].append(
                        simulate_trade(k, i, 'LONG', c['close'], tp1_pct=0.55, sl_pct=0.45, be_trigger_pct=0.18)
                    )
                elif (c['high'] - c['close']) / rng >= 0.60:
                    strategies["4. Iceberg Absorption Wall (Volume Explodes, Range Contracts)"].append(
                        simulate_trade(k, i, 'SHORT', c['close'], tp1_pct=0.55, sl_pct=0.45, be_trigger_pct=0.18)
                    )

    # -------------------------------------------------------------
    # 5. Fractal Dimension Noise-to-Trend Spring (Chaos Filter)
    # -------------------------------------------------------------
    # FDI / Hurst proxy: Detect extreme range contraction followed by sudden volatility release
    for s in symbols:
        k = data_5m[s]
        atr = calc_atr(k, 14)
        atr_sma = calc_sma(atr, 30)
        
        for i in range(30, len(k) - 30):
            # Contraction: ATR was < 0.65 of 30-period average for 3 bars
            if atr[i-2] < atr_sma[i-2] * 0.70 and atr[i-1] < atr_sma[i-1] * 0.70:
                # Sudden expansion spring: Current bar range > 2.0x previous bar range
                curr_rng = k[i]['high'] - k[i]['low']
                prev_rng = k[i-1]['high'] - k[i-1]['low']
                if prev_rng > 0 and curr_rng >= 2.2 * prev_rng:
                    if k[i]['close'] > k[i]['open']:
                        strategies["5. Fractal Dimension Noise-to-Trend Spring (Chaos Filter)"].append(
                            simulate_trade(k, i, 'LONG', k[i]['close'], tp1_pct=0.60, sl_pct=0.45, be_trigger_pct=0.20)
                        )
                    else:
                        strategies["5. Fractal Dimension Noise-to-Trend Spring (Chaos Filter)"].append(
                            simulate_trade(k, i, 'SHORT', k[i]['close'], tp1_pct=0.60, sl_pct=0.45, be_trigger_pct=0.20)
                        )

    # -------------------------------------------------------------
    # 6. Exhaustion Sequential Run (5 Consecutive Climax Bars)
    # -------------------------------------------------------------
    # 5 consecutive bars in the exact same direction where the last bar has the largest range
    for s in symbols:
        k = data_5m[s]
        for i in range(5, len(k) - 30):
            # 5 consecutive green bars
            if all(k[i-j]['close'] > k[i-j]['open'] for j in range(5)):
                # Climax bar: last bar is larger than all prior 4 bars
                last_rng = k[i]['high'] - k[i]['low']
                prior_max = max(k[i-j]['high'] - k[i-j]['low'] for j in range(1, 5))
                if last_rng >= 1.5 * prior_max:
                    strategies["6. Exhaustion Sequential Run (5 Consecutive Climax Bars)"].append(
                        simulate_trade(k, i, 'SHORT', k[i]['close'], tp1_pct=0.55, sl_pct=0.50, be_trigger_pct=0.18)
                    )
            # 5 consecutive red bars
            elif all(k[i-j]['close'] < k[i-j]['open'] for j in range(5)):
                last_rng = k[i]['high'] - k[i]['low']
                prior_max = max(k[i-j]['high'] - k[i-j]['low'] for j in range(1, 5))
                if last_rng >= 1.5 * prior_max:
                    strategies["6. Exhaustion Sequential Run (5 Consecutive Climax Bars)"].append(
                        simulate_trade(k, i, 'LONG', k[i]['close'], tp1_pct=0.55, sl_pct=0.50, be_trigger_pct=0.18)
                    )

    # Summarize results
    print("\n" + "=" * 105)
    print(f"{'Unconventional Architecture':<54} | {'Trades':<6} | {'Win %':<6} | {'No-Loss %':<9} | {'PF':<5} | {'Net PnL':<8}")
    print("=" * 105)
    
    results = {}
    for name, trade_list in strategies.items():
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
        
        results[name] = {
            'trades': n,
            'wins': len(wins),
            'be': len(bes),
            'losses': len(losses),
            'win_rate': win_rate,
            'no_loss_rate': no_loss_rate,
            'profit_factor': pf,
            'total_net_pnl': total_pnl
        }
        
        print(f"{name:<54} | {n:<6} | {win_rate:5.1f}% | {no_loss_rate:8.1f}% | {pf:5.2f} | ${total_pnl:6.2f}")
    print("=" * 105)
    
    with open('scratch/unconventional_results.json', 'w') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    run_unconventional_tests()

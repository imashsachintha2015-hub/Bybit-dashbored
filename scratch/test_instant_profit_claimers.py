import urllib.request
import json
import ssl
import time
import math

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'BNBUSDT']
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

def simulate_instant_claim(candles, entry_idx, side, entry_price, tp_pct, sl_pct, be_trigger_pct=None, is_maker_entry=True, is_maker_exit=True, max_bars=30):
    pos_size = 100.0  # $100 position ($10 margin @ 10x)
    fee_entry = pos_size * (MAKER_FEE if is_maker_entry else TAKER_FEE)
    fee_exit_maker = pos_size * MAKER_FEE
    fee_exit_taker = pos_size * TAKER_FEE
    
    tp_price = entry_price * (1 + tp_pct / 100) if side == 'LONG' else entry_price * (1 - tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100) if side == 'LONG' else entry_price * (1 + sl_pct / 100)
    be_price = (entry_price * (1 + be_trigger_pct / 100)) if (be_trigger_pct and side == 'LONG') else ((entry_price * (1 - be_trigger_pct / 100)) if be_trigger_pct else None)
    
    be_active = False
    current_sl = sl_price
    
    for i in range(entry_idx + 1, min(len(candles), entry_idx + max_bars + 1)):
        c = candles[i]
        
        if side == 'LONG':
            if be_price and not be_active and c['high'] >= be_price:
                be_active = True
                current_sl = entry_price
                
            # Instant Profit Claim
            if c['high'] >= tp_price:
                gross_win = pos_size * (tp_pct / 100)
                fee_exit = fee_exit_maker if is_maker_exit else fee_exit_taker
                net_pnl = gross_win - fee_entry - fee_exit
                return {'result': 'WIN', 'pnl': net_pnl, 'bars': i - entry_idx, 'gross': gross_win, 'fees': fee_entry + fee_exit}
                
            if c['low'] <= current_sl:
                if be_active:
                    net_pnl = 0.0 - fee_entry - fee_exit_taker
                    return {'result': 'BE', 'pnl': net_pnl, 'bars': i - entry_idx, 'gross': 0.0, 'fees': fee_entry + fee_exit_taker}
                else:
                    loss_pct = (entry_price - current_sl) / entry_price
                    gross_loss = -pos_size * loss_pct
                    net_pnl = gross_loss - fee_entry - fee_exit_taker
                    return {'result': 'LOSS', 'pnl': net_pnl, 'bars': i - entry_idx, 'gross': gross_loss, 'fees': fee_entry + fee_exit_taker}
        else:
            if be_price and not be_active and c['low'] <= be_price:
                be_active = True
                current_sl = entry_price
                
            if c['low'] <= tp_price:
                gross_win = pos_size * (tp_pct / 100)
                fee_exit = fee_exit_maker if is_maker_exit else fee_exit_taker
                net_pnl = gross_win - fee_entry - fee_exit
                return {'result': 'WIN', 'pnl': net_pnl, 'bars': i - entry_idx, 'gross': gross_win, 'fees': fee_entry + fee_exit}
                
            if c['high'] >= current_sl:
                if be_active:
                    net_pnl = 0.0 - fee_entry - fee_exit_taker
                    return {'result': 'BE', 'pnl': net_pnl, 'bars': i - entry_idx, 'gross': 0.0, 'fees': fee_entry + fee_exit_taker}
                else:
                    loss_pct = (current_sl - entry_price) / entry_price
                    gross_loss = -pos_size * loss_pct
                    net_pnl = gross_loss - fee_entry - fee_exit_taker
                    return {'result': 'LOSS', 'pnl': net_pnl, 'bars': i - entry_idx, 'gross': gross_loss, 'fees': fee_entry + fee_exit_taker}
                    
    # Expired
    c_last = candles[min(len(candles)-1, entry_idx + max_bars)]
    diff_pct = (c_last['close'] - entry_price) / entry_price if side == 'LONG' else (entry_price - c_last['close']) / entry_price
    gross_pnl = pos_size * diff_pct
    net_pnl = gross_pnl - fee_entry - fee_exit_taker
    res = 'WIN' if net_pnl > 0 else ('BE' if abs(diff_pct) < 0.001 else 'LOSS')
    return {'result': res, 'pnl': net_pnl, 'bars': max_bars, 'gross': gross_pnl, 'fees': fee_entry + fee_exit_taker}

def test_instant_claimers():
    print("================================================================================")
    print("    TESTING INSTANT PROFIT CLAIMERS & HIGH-FREQUENCY SCALP ON BYBIT DATA")
    print("================================================================================")
    
    market_5m = {}
    market_1m = {}
    
    for s in SYMBOLS:
        print(f"Loading {s} 5m and 1m klines...")
        market_5m[s] = fetch_klines(s, '5', 1500)
        time.sleep(0.15)
        market_1m[s] = fetch_klines(s, '1', 1500)
        time.sleep(0.15)
        
    setups = {
        "1. Liq Climax: Instant Claim @ +0.20% (Maker TP)": [],
        "2. Liq Climax: Instant Claim @ +0.25% (Maker TP)": [],
        "3. Liq Climax: Instant Claim @ +0.30% (Maker TP)": [],
        "4. Liq Climax: Instant Claim @ +0.25% + BE @ +0.15%": [],
        "5. 1m High-Frequency Liq Climax: Claim @ +0.18%": [],
        "6. 1m EMA Micro-Pullback HFT: Claim @ +0.15%": []
    }
    
    # 5m Strategies (1 - 4)
    for s, k5 in market_5m.items():
        vols = [c['volume'] for c in k5]
        vol_sma20 = calc_sma(vols, 20)
        
        for i in range(50, len(k5) - 30):
            c = k5[i]
            bar_rng = c['high'] - c['low']
            if bar_rng == 0 or vol_sma20[i] == 0: continue
            
            vol_ratio = c['volume'] / vol_sma20[i]
            lower_wick = min(c['open'], c['close']) - c['low']
            upper_wick = c['high'] - max(c['open'], c['close'])
            
            # Liquidation Absorption Setup
            if vol_ratio >= 3.0:
                # Long
                if (lower_wick / bar_rng) >= 0.50 and c['close'] > c['open']:
                    # Setup 1: TP +0.20%, SL -0.40%
                    setups["1. Liq Climax: Instant Claim @ +0.20% (Maker TP)"].append(
                        simulate_instant_claim(k5, i, 'LONG', c['close'], tp_pct=0.20, sl_pct=0.40)
                    )
                    # Setup 2: TP +0.25%, SL -0.45%
                    setups["2. Liq Climax: Instant Claim @ +0.25% (Maker TP)"].append(
                        simulate_instant_claim(k5, i, 'LONG', c['close'], tp_pct=0.25, sl_pct=0.45)
                    )
                    # Setup 3: TP +0.30%, SL -0.50%
                    setups["3. Liq Climax: Instant Claim @ +0.30% (Maker TP)"].append(
                        simulate_instant_claim(k5, i, 'LONG', c['close'], tp_pct=0.30, sl_pct=0.50)
                    )
                    # Setup 4: TP +0.25%, SL -0.50%, BE @ +0.15%
                    setups["4. Liq Climax: Instant Claim @ +0.25% + BE @ +0.15%"].append(
                        simulate_instant_claim(k5, i, 'LONG', c['close'], tp_pct=0.25, sl_pct=0.50, be_trigger_pct=0.15)
                    )
                # Short
                elif (upper_wick / bar_rng) >= 0.50 and c['close'] < c['open']:
                    setups["1. Liq Climax: Instant Claim @ +0.20% (Maker TP)"].append(
                        simulate_instant_claim(k5, i, 'SHORT', c['close'], tp_pct=0.20, sl_pct=0.40)
                    )
                    setups["2. Liq Climax: Instant Claim @ +0.25% (Maker TP)"].append(
                        simulate_instant_claim(k5, i, 'SHORT', c['close'], tp_pct=0.25, sl_pct=0.45)
                    )
                    setups["3. Liq Climax: Instant Claim @ +0.30% (Maker TP)"].append(
                        simulate_instant_claim(k5, i, 'SHORT', c['close'], tp_pct=0.30, sl_pct=0.50)
                    )
                    setups["4. Liq Climax: Instant Claim @ +0.25% + BE @ +0.15%"].append(
                        simulate_instant_claim(k5, i, 'SHORT', c['close'], tp_pct=0.25, sl_pct=0.50, be_trigger_pct=0.15)
                    )

    # 1m High Frequency Strategies (5 - 6)
    for s, k1 in market_1m.items():
        closes1 = [c['close'] for c in k1]
        vols1 = [c['volume'] for c in k1]
        vol1_sma20 = calc_sma(vols1, 20)
        ema9 = calc_ema(closes1, 9)
        ema21 = calc_ema(closes1, 21)
        
        for i in range(50, len(k1) - 30):
            c = k1[i]
            bar_rng = c['high'] - c['low']
            if bar_rng == 0 or vol1_sma20[i] == 0: continue
            
            # Setup 5: 1m Vol Climax (>3.5x vol on 1m, quick +0.18% claim)
            vol_ratio = c['volume'] / vol1_sma20[i]
            if vol_ratio >= 3.5:
                lower_wick = min(c['open'], c['close']) - c['low']
                upper_wick = c['high'] - max(c['open'], c['close'])
                if (lower_wick / bar_rng) >= 0.50 and c['close'] > c['open']:
                    setups["5. 1m High-Frequency Liq Climax: Claim @ +0.18%"].append(
                        simulate_instant_claim(k1, i, 'LONG', c['close'], tp_pct=0.18, sl_pct=0.30, max_bars=15)
                    )
                elif (upper_wick / bar_rng) >= 0.50 and c['close'] < c['open']:
                    setups["5. 1m High-Frequency Liq Climax: Claim @ +0.18%"].append(
                        simulate_instant_claim(k1, i, 'SHORT', c['close'], tp_pct=0.18, sl_pct=0.30, max_bars=15)
                    )
                    
            # Setup 6: 1m EMA Trend Pullback Micro Scalp (High Frequency)
            if ema9[i] > ema21[i] and k1[i-1]['low'] <= ema9[i] and c['close'] > ema9[i]:
                setups["6. 1m EMA Micro-Pullback HFT: Claim @ +0.15%"].append(
                    simulate_instant_claim(k1, i, 'LONG', c['close'], tp_pct=0.15, sl_pct=0.25, max_bars=10)
                )
            elif ema9[i] < ema21[i] and k1[i-1]['high'] >= ema9[i] and c['close'] < ema9[i]:
                setups["6. 1m EMA Micro-Pullback HFT: Claim @ +0.15%"].append(
                    simulate_instant_claim(k1, i, 'SHORT', c['close'], tp_pct=0.15, sl_pct=0.25, max_bars=10)
                )

    print("\n" + "=" * 105)
    print(f"{'Instant Profit Architecture':<52} | {'Trades':<6} | {'Win %':<6} | {'No-Loss %':<9} | {'PF':<5} | {'Net PnL':<8} | {'Avg Hold':<8}")
    print("=" * 105)
    
    results = {}
    for name, trade_list in setups.items():
        n = len(trade_list)
        if n == 0:
            print(f"{name:<52} | 0      | N/A    | N/A       | N/A   | $0.00    | N/A")
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
        total_fees = sum(t.get('fees', 0.0) for t in trade_list)
        avg_bars = sum(t['bars'] for t in trade_list) / n
        
        results[name] = {
            'trades': n,
            'wins': len(wins),
            'be': len(bes),
            'losses': len(losses),
            'win_rate': win_rate,
            'no_loss_rate': no_loss_rate,
            'profit_factor': pf,
            'total_net_pnl': total_pnl,
            'total_fees_paid': total_fees,
            'avg_bars': avg_bars
        }
        
        print(f"{name:<52} | {n:<6} | {win_rate:5.1f}% | {no_loss_rate:8.1f}% | {pf:5.2f} | ${total_pnl:6.2f} | {avg_bars:4.1f} bars")
        
    print("=" * 105)
    with open('scratch/instant_claim_results.json', 'w') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    test_instant_claimers()

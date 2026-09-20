import urllib.request
import json
import ssl
import time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SYMBOLS = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 
    'BNBUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'NEARUSDT', 
    'SUIUSDT', 'APTUSDT'
]
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

def simulate_twostage(candles, entry_idx, side, entry_price, tp1_pct=0.25, tp2_pct=0.55, sl_pct=0.55, be_trigger_pct=0.18, max_bars=40):
    pos_size = 100.0  # $100 notional ($10 margin @ 10x)
    fee_entry = pos_size * MAKER_FEE
    
    tp1_price = entry_price * (1 + tp1_pct / 100) if side == 'LONG' else entry_price * (1 - tp1_pct / 100)
    tp2_price = entry_price * (1 + tp2_pct / 100) if side == 'LONG' else entry_price * (1 - tp2_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100) if side == 'LONG' else entry_price * (1 + sl_pct / 100)
    be_price = entry_price * (1 + be_trigger_pct / 100) if side == 'LONG' else entry_price * (1 - be_trigger_pct / 100)
    
    tp1_hit = False
    be_active = False
    current_sl = sl_price
    
    for i in range(entry_idx + 1, min(len(candles), entry_idx + max_bars + 1)):
        c = candles[i]
        
        if side == 'LONG':
            # Check BE
            if not be_active and c['high'] >= be_price:
                be_active = True
                current_sl = entry_price
                
            # Check TP1 (Instant Claim 70%)
            if not tp1_hit and c['high'] >= tp1_price:
                tp1_hit = True
                be_active = True
                current_sl = entry_price # Lock BE on runner
                
            # Check TP2 (Runner 30%)
            if tp1_hit and c['high'] >= tp2_price:
                pnl1 = (0.70 * pos_size) * (tp1_pct / 100) - (0.70 * pos_size * MAKER_FEE)
                pnl2 = (0.30 * pos_size) * (tp2_pct / 100) - (0.30 * pos_size * MAKER_FEE)
                net = pnl1 + pnl2 - fee_entry
                return {'result': 'FULL_WIN', 'pnl': net, 'bars': i - entry_idx}
                
            # Check SL
            if c['low'] <= current_sl:
                if tp1_hit:
                    # Runner stopped at BE
                    pnl1 = (0.70 * pos_size) * (tp1_pct / 100) - (0.70 * pos_size * MAKER_FEE)
                    pnl2 = 0.0 - (0.30 * pos_size * TAKER_FEE)
                    net = pnl1 + pnl2 - fee_entry
                    return {'result': 'PARTIAL_WIN', 'pnl': net, 'bars': i - entry_idx}
                elif be_active:
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'BE', 'pnl': 0.0 - fee_entry - fee_exit, 'bars': i - entry_idx}
                else:
                    loss_pct = (entry_price - current_sl) / entry_price
                    gross_loss = -pos_size * loss_pct
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'LOSS', 'pnl': gross_loss - fee_entry - fee_exit, 'bars': i - entry_idx}
        else: # SHORT
            if not be_active and c['low'] <= be_price:
                be_active = True
                current_sl = entry_price
                
            if not tp1_hit and c['low'] <= tp1_price:
                tp1_hit = True
                be_active = True
                current_sl = entry_price
                
            if tp1_hit and c['low'] <= tp2_price:
                pnl1 = (0.70 * pos_size) * (tp1_pct / 100) - (0.70 * pos_size * MAKER_FEE)
                pnl2 = (0.30 * pos_size) * (tp2_pct / 100) - (0.30 * pos_size * MAKER_FEE)
                net = pnl1 + pnl2 - fee_entry
                return {'result': 'FULL_WIN', 'pnl': net, 'bars': i - entry_idx}
                
            if c['high'] >= current_sl:
                if tp1_hit:
                    pnl1 = (0.70 * pos_size) * (tp1_pct / 100) - (0.70 * pos_size * MAKER_FEE)
                    pnl2 = 0.0 - (0.30 * pos_size * TAKER_FEE)
                    net = pnl1 + pnl2 - fee_entry
                    return {'result': 'PARTIAL_WIN', 'pnl': net, 'bars': i - entry_idx}
                elif be_active:
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'BE', 'pnl': 0.0 - fee_entry - fee_exit, 'bars': i - entry_idx}
                else:
                    loss_pct = (current_sl - entry_price) / entry_price
                    gross_loss = -pos_size * loss_pct
                    fee_exit = pos_size * TAKER_FEE
                    return {'result': 'LOSS', 'pnl': gross_loss - fee_entry - fee_exit, 'bars': i - entry_idx}
                    
    # Timeout exit
    c_last = candles[min(len(candles)-1, entry_idx + max_bars)]
    diff = (c_last['close'] - entry_price)/entry_price if side == 'LONG' else (entry_price - c_last['close'])/entry_price
    pnl = pos_size * diff - fee_entry - (pos_size * TAKER_FEE)
    return {'result': 'WIN' if pnl > 0 else 'LOSS', 'pnl': pnl, 'bars': max_bars}

def test_twostage_suite():
    print("================================================================================")
    print("    TESTING TWO-STAGE INSTANT PROFIT CLAIMER ACROSS 12 BYBIT COINS")
    print("================================================================================")
    
    market_data = {}
    for s in SYMBOLS:
        print(f"Fetching {s} 5m klines...")
        market_data[s] = fetch_klines(s, '5', 1500)
        time.sleep(0.15)
        
    setups = {
        "A. 100% Instant Claim @ +0.25%": [],
        "B. Two-Stage: 70% @ +0.25% + 30% @ +0.55%": [],
        "C. Two-Stage: 50% @ +0.25% + 50% @ +0.55%": [],
        "D. 100% Standard Sniper @ +0.55%": []
    }
    
    for s, k5 in market_data.items():
        vols = [c['volume'] for c in k5]
        vol_sma20 = calc_sma(vols, 20)
        
        for i in range(50, len(k5) - 30):
            c = k5[i]
            bar_rng = c['high'] - c['low']
            if bar_rng == 0 or vol_sma20[i] == 0: continue
            
            vol_ratio = c['volume'] / vol_sma20[i]
            lower_wick = min(c['open'], c['close']) - c['low']
            upper_wick = c['high'] - max(c['open'], c['close'])
            
            if vol_ratio >= 3.0:
                # Long
                if (lower_wick / bar_rng) >= 0.50 and c['close'] > c['open']:
                    setups["A. 100% Instant Claim @ +0.25%"].append(
                        simulate_twostage(k5, i, 'LONG', c['close'], tp1_pct=0.25, tp2_pct=0.25, sl_pct=0.50)
                    )
                    setups["B. Two-Stage: 70% @ +0.25% + 30% @ +0.55%"].append(
                        simulate_twostage(k5, i, 'LONG', c['close'], tp1_pct=0.25, tp2_pct=0.55, sl_pct=0.55)
                    )
                    setups["C. Two-Stage: 50% @ +0.25% + 50% @ +0.55%"].append(
                        simulate_twostage(k5, i, 'LONG', c['close'], tp1_pct=0.25, tp2_pct=0.55, sl_pct=0.55)
                    )
                    setups["D. 100% Standard Sniper @ +0.55%"].append(
                        simulate_twostage(k5, i, 'LONG', c['close'], tp1_pct=0.55, tp2_pct=0.55, sl_pct=0.60)
                    )
                # Short
                elif (upper_wick / bar_rng) >= 0.50 and c['close'] < c['open']:
                    setups["A. 100% Instant Claim @ +0.25%"].append(
                        simulate_twostage(k5, i, 'SHORT', c['close'], tp1_pct=0.25, tp2_pct=0.25, sl_pct=0.50)
                    )
                    setups["B. Two-Stage: 70% @ +0.25% + 30% @ +0.55%"].append(
                        simulate_twostage(k5, i, 'SHORT', c['close'], tp1_pct=0.25, tp2_pct=0.55, sl_pct=0.55)
                    )
                    setups["C. Two-Stage: 50% @ +0.25% + 50% @ +0.55%"].append(
                        simulate_twostage(k5, i, 'SHORT', c['close'], tp1_pct=0.25, tp2_pct=0.55, sl_pct=0.55)
                    )
                    setups["D. 100% Standard Sniper @ +0.55%"].append(
                        simulate_twostage(k5, i, 'SHORT', c['close'], tp1_pct=0.55, tp2_pct=0.55, sl_pct=0.60)
                    )

    print("\n" + "=" * 105)
    print(f"{'Strategy Configuration':<46} | {'Trades':<6} | {'Profit %':<8} | {'No-Loss %':<9} | {'PF':<5} | {'Net PnL':<8}")
    print("=" * 105)
    
    for name, trade_list in setups.items():
        n = len(trade_list)
        if n == 0: continue
        profitable = [t for t in trade_list if t['pnl'] > 0]
        bes = [t for t in trade_list if t['result'] == 'BE']
        losses = [t for t in trade_list if t['pnl'] < 0 and t['result'] != 'BE']
        
        prof_rate = (len(profitable) / n) * 100
        no_loss_rate = ((len(profitable) + len(bes)) / n) * 100
        gross_profit = sum(t['pnl'] for t in profitable)
        gross_loss = abs(sum(t['pnl'] for t in trade_list if t['pnl'] < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
        total_pnl = sum(t['pnl'] for t in trade_list)
        
        print(f"{name:<46} | {n:<6} | {prof_rate:6.1f}%  | {no_loss_rate:8.1f}% | {pf:5.2f} | ${total_pnl:6.2f}")
    print("=" * 105)

if __name__ == '__main__':
    test_twostage_suite()

import urllib.request
import json
import ssl
import time
import math

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

def simulate_trade(candles, entry_idx, side, entry_price, tp1_pct, sl_pct, be_trigger_pct=0.18, runner_pct=0.10, is_maker=True, max_bars=45):
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

def test_robustness():
    print("================================================================================")
    print("  LIQUIDATION CLIMAX ROBUSTNESS SUITE: 12 TOP BYBIT COINS (1,500 5m BARS)")
    print("================================================================================")
    
    market_data = {}
    for s in SYMBOLS:
        print(f"Loading {s}...")
        try:
            k5 = fetch_klines(s, '5', 1500)
            market_data[s] = k5
            time.sleep(0.15)
        except Exception as e:
            print(f"Error loading {s}: {e}")
            
    variants = {
        "V1: 3.0x Vol, 50% Wick, TP 0.50%, BE 0.18%": [],
        "V2: 3.0x Vol, 50% Wick, TP 0.55%, BE 0.20%": [],
        "V3: 3.5x Vol, 50% Wick, TP 0.50%, BE 0.18%": [],
        "V4: 2.8x Vol, 55% Wick, TP 0.50%, BE 0.18%": [],
        "V5: 2.8x Vol, 50% Wick, TP 0.60%, BE 0.22%": []
    }
    
    for s, k5 in market_data.items():
        volumes = [c['volume'] for c in k5]
        vol_sma20 = calc_sma(volumes, 20)
        
        for i in range(50, len(k5) - 30):
            c = k5[i]
            bar_range = c['high'] - c['low']
            if bar_range == 0 or vol_sma20[i] == 0: continue
            
            vol_ratio = c['volume'] / vol_sma20[i]
            lower_wick = min(c['open'], c['close']) - c['low']
            upper_wick = c['high'] - max(c['open'], c['close'])
            lower_wick_ratio = lower_wick / bar_range
            upper_wick_ratio = upper_wick / bar_range
            
            # V1: 3.0x Vol, 50% Wick, TP 0.50%, BE 0.18%
            if vol_ratio >= 3.0:
                if lower_wick_ratio >= 0.50 and c['close'] > c['open']:
                    variants["V1: 3.0x Vol, 50% Wick, TP 0.50%, BE 0.18%"].append(
                        simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18)
                    )
                elif upper_wick_ratio >= 0.50 and c['close'] < c['open']:
                    variants["V1: 3.0x Vol, 50% Wick, TP 0.50%, BE 0.18%"].append(
                        simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18)
                    )

            # V2: 3.0x Vol, 50% Wick, TP 0.55%, BE 0.20%
            if vol_ratio >= 3.0:
                if lower_wick_ratio >= 0.50 and c['close'] > c['open']:
                    variants["V2: 3.0x Vol, 50% Wick, TP 0.55%, BE 0.20%"].append(
                        simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.55, sl_pct=0.65, be_trigger_pct=0.20)
                    )
                elif upper_wick_ratio >= 0.50 and c['close'] < c['open']:
                    variants["V2: 3.0x Vol, 50% Wick, TP 0.55%, BE 0.20%"].append(
                        simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.55, sl_pct=0.65, be_trigger_pct=0.20)
                    )

            # V3: 3.5x Vol, 50% Wick, TP 0.50%, BE 0.18%
            if vol_ratio >= 3.5:
                if lower_wick_ratio >= 0.50 and c['close'] > c['open']:
                    variants["V3: 3.5x Vol, 50% Wick, TP 0.50%, BE 0.18%"].append(
                        simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18)
                    )
                elif upper_wick_ratio >= 0.50 and c['close'] < c['open']:
                    variants["V3: 3.5x Vol, 50% Wick, TP 0.50%, BE 0.18%"].append(
                        simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18)
                    )

            # V4: 2.8x Vol, 55% Wick, TP 0.50%, BE 0.18%
            if vol_ratio >= 2.8:
                if lower_wick_ratio >= 0.55 and c['close'] > c['open']:
                    variants["V4: 2.8x Vol, 55% Wick, TP 0.50%, BE 0.18%"].append(
                        simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18)
                    )
                elif upper_wick_ratio >= 0.55 and c['close'] < c['open']:
                    variants["V4: 2.8x Vol, 55% Wick, TP 0.50%, BE 0.18%"].append(
                        simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.50, sl_pct=0.60, be_trigger_pct=0.18)
                    )

            # V5: 2.8x Vol, 50% Wick, TP 0.60%, BE 0.22%
            if vol_ratio >= 2.8:
                if lower_wick_ratio >= 0.50 and c['close'] > c['open']:
                    variants["V5: 2.8x Vol, 50% Wick, TP 0.60%, BE 0.22%"].append(
                        simulate_trade(k5, i, 'LONG', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.22)
                    )
                elif upper_wick_ratio >= 0.50 and c['close'] < c['open']:
                    variants["V5: 2.8x Vol, 50% Wick, TP 0.60%, BE 0.22%"].append(
                        simulate_trade(k5, i, 'SHORT', c['close'], tp1_pct=0.60, sl_pct=0.65, be_trigger_pct=0.22)
                    )

    print("\n" + "=" * 98)
    print(f"{'Variant Name':<48} | {'Trades':<6} | {'Win %':<6} | {'No-Loss %':<9} | {'PF':<5} | {'Net PnL':<8}")
    print("=" * 98)
    
    results = {}
    for name, trade_list in variants.items():
        n = len(trade_list)
        if n == 0:
            print(f"{name:<48} | 0      | N/A    | N/A       | N/A   | $0.00")
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
        
        print(f"{name:<48} | {n:<6} | {win_rate:5.1f}% | {no_loss_rate:8.1f}% | {pf:5.2f} | ${total_pnl:6.2f}")
        
    print("=" * 98)
    with open('scratch/robustness_results.json', 'w') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    test_robustness()

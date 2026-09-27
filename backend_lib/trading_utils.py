"""
Institutional Trading Utilities & Precision Math for CME-X5 & Bybit V5 Linear.
Provides tested coin specifications, lot-size rounding, price-tick rounding,
and risk-managed order sizing.
"""

COIN_SPECS = {
    'BTCUSDT':  {'min_qty': 0.001, 'qty_step': 0.001, 'price_dec': 2, 'min_notional': 5.0},
    'ETHUSDT':  {'min_qty': 0.01,  'qty_step': 0.01,  'price_dec': 2, 'min_notional': 5.0},
    'SOLUSDT':  {'min_qty': 0.1,   'qty_step': 0.1,   'price_dec': 2, 'min_notional': 5.0},
    'XRPUSDT':  {'min_qty': 1.0,   'qty_step': 1.0,   'price_dec': 4, 'min_notional': 5.0},
    'LINKUSDT': {'min_qty': 0.1,   'qty_step': 0.1,   'price_dec': 3, 'min_notional': 5.0},
    'SEIUSDT':  {'min_qty': 1.0,   'qty_step': 1.0,   'price_dec': 4, 'min_notional': 5.0},
    'AVAXUSDT': {'min_qty': 0.1,   'qty_step': 0.1,   'price_dec': 3, 'min_notional': 5.0},
    'DOGEUSDT': {'min_qty': 1.0,   'qty_step': 1.0,   'price_dec': 5, 'min_notional': 5.0},
    'BNBUSDT':  {'min_qty': 0.01,  'qty_step': 0.01,  'price_dec': 2, 'min_notional': 5.0},
    'ADAUSDT':  {'min_qty': 1.0,   'qty_step': 1.0,   'price_dec': 4, 'min_notional': 5.0},
    'DOTUSDT':  {'min_qty': 0.1,   'qty_step': 0.1,   'price_dec': 3, 'min_notional': 5.0},
    'MATICUSDT':{'min_qty': 1.0,   'qty_step': 1.0,   'price_dec': 4, 'min_notional': 5.0},
    'LTCUSDT':  {'min_qty': 0.1,   'qty_step': 0.1,   'price_dec': 2, 'min_notional': 5.0},
    'NEARUSDT': {'min_qty': 0.1,   'qty_step': 0.1,   'price_dec': 3, 'min_notional': 5.0},
    'APTUSDT':  {'min_qty': 0.1,   'qty_step': 0.1,   'price_dec': 3, 'min_notional': 5.0},
}

DEFAULT_SPEC = {'min_qty': 0.1, 'qty_step': 0.1, 'price_dec': 2, 'min_notional': 5.0}

def get_spec(symbol):
    return COIN_SPECS.get(symbol, DEFAULT_SPEC)

def round_qty(symbol, raw_qty):
    spec = get_spec(symbol)
    step = spec['qty_step']
    min_q = spec['min_qty']
    decimals = len(str(step).split('.')[1]) if '.' in str(step) else 0
    q = round(round(float(raw_qty) / step) * step, decimals)
    return max(q, min_q)

def round_price(symbol, price):
    spec = get_spec(symbol)
    dec = spec['price_dec']
    return round(float(price), dec)

def compute_order_sizing(symbol, cur_price, target_notional=50.0):
    """
    Computes rounded order qty respecting min_notional (5.0 USDT)
    and coin-specific lot size filters.
    """
    cur_p = max(1e-6, float(cur_price))
    spec = get_spec(symbol)
    min_notional = spec.get('min_notional', 5.0)
    desired_notional = max(min_notional * 1.1, float(target_notional))
    raw_qty = desired_notional / cur_p
    qty = round_qty(symbol, raw_qty)
    # Ensure notional meets minimum
    while (qty * cur_p) < min_notional:
        qty += spec['qty_step']
        qty = round_qty(symbol, qty)
    return qty

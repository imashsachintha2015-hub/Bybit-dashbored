"""Real funding for a trade from funding/{SYM}.json (Binance archive, fetch_funding.py).
Longs pay the rate at every funding time inside (entry, exit]; shorts receive it (negative rates reverse the flow).
Where no funding history exists yet (coin not listed on Binance), fall back to the old flat model: 0.5 bp per 8h paid by both sides."""
import json, os, bisect
FLAT_8H = 0.5e-4
_cache = {}

def series(sym):
    if sym not in _cache:
        p = f"funding/{sym}.json"; rows = json.load(open(p)) if os.path.exists(p) else []
        t = [r[0] for r in rows]; cum = [0.0]
        for r in rows: cum.append(cum[-1] + r[1])
        _cache[sym] = (t, cum)
    return _cache[sym]

def paid(sym, side, te, tx):
    """fraction of notional paid by the position (negative = received) between entry te and exit tx (ms), and whether real data was used"""
    t, cum = series(sym)
    if not t or te < t[0]:
        return FLAT_8H * (tx - te) / (8 * 3600000), False
    a = bisect.bisect_right(t, te); b = bisect.bisect_right(t, tx); s = cum[b] - cum[a]
    return (s if side == "L" else -s), True

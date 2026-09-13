"""Signal log logic, exercised against an in-memory KV stub.

The dedup path is the part worth testing: the live engine re-evaluates every
symbol every five seconds, so a setup that stands for an hour would write ~720
identical rows if folding were broken, and the retention cap would then evict
real history within minutes. Run with: python3 tests/test_signal_log.py

Note on scope: folding compares only against the NEWEST row for a symbol, so a
candidate that changes and later reverts appends a second row rather than
re-folding into the first. That is deliberate -- each state change is a real
event -- but it does mean an oscillating symbol produces one row per flip.
"""
import sys, types
sys.path.insert(0, '/home/user/Bybit-dashbored')

# Stub KV with an in-memory store so the module's logic can be exercised
# without a live Redis attached.
store = {}
fake = types.ModuleType('backend_lib.kv')
fake.kv_get_json = lambda k, d=None: store.get(k, d)
fake.kv_set_json = lambda k, v: store.__setitem__(k, v)
fake.kv_configured = lambda: True
sys.modules['backend_lib.kv'] = fake

from backend_lib import signal_log as S

def sig(sym, setup, grade, outcome, decision='BUY'):
    return {"fingerprint": f"{sym}|{setup}|LONG|{grade}|{decision}", "symbol": sym,
            "direction": "LONG", "setup_type": setup, "grade": grade, "score": 70,
            "regime": "TREND", "outcome": outcome, "entry": 100.0, "stop": 99.0}

# 1) dedup: same unchanged candidate seen repeatedly stays ONE row
for _ in range(50):
    S.record(sig("BTCUSDT", "BREAKOUT_RETEST", "A", "NOT_TRADED"))
rows = S.load()["signals"]
assert len(rows) == 1, f"dedup failed: {len(rows)} rows"
assert rows[0]["seen_count"] == 50, rows[0]["seen_count"]
print(f"  dedup: 50 identical evaluations -> {len(rows)} row, seen_count={rows[0]['seen_count']}  OK")

# 2) a CHANGED candidate on the same symbol appends a new row
S.record(sig("BTCUSDT", "BREAKOUT_RETEST", "A+", "TAKEN"))
assert len(S.load()["signals"]) == 2, S.load()["signals"]
print("  change of grade/outcome -> new row  OK")

# 3) other symbols are independent
for i in range(5):
    S.record(sig(f"ALT{i}USDT", "RANGE_FADE", "A", "REJECTED_RISK"))
total = len(S.load()["signals"])
assert total == 7, total
print(f"  5 other symbols -> {total} rows total  OK")

# 4) paging + filters
p1 = S.page(1, 3)
assert len(p1["items"]) == 3 and p1["pages"] == 3 and p1["total"] == 7, p1
p3 = S.page(3, 3)
assert len(p3["items"]) == 1, p3
print(f"  paging: total={p1['total']} pages={p1['pages']} page3={len(p3['items'])} item  OK")
f = S.page(1, 50, outcome="REJECTED_RISK")
assert f["total"] == 5, f["total"]
print(f"  outcome filter REJECTED_RISK -> {f['total']}  OK")
f2 = S.page(1, 50, symbol="BTCUSDT")
assert f2["total"] == 2, f2["total"]
print(f"  symbol filter BTCUSDT -> {f2['total']}  OK")
print(f"  counts: {p1['counts']}")

# 5) retention cap holds
for i in range(600):
    S.record(sig(f"X{i}USDT", "S", "A", "NOT_TRADED"))
n = len(S.load()["signals"])
assert n == S.MAX_ROWS, n
print(f"  retention cap: after 600 more -> {n} rows (MAX_ROWS)  OK")
# 6) shadow resolution attaches the verdict to the right row
store.clear()
S.record(sig("BTCUSDT", "RANGE_FADE", "A", "NOT_TRADED"))
S.record(sig("ETHUSDT", "RANGE_FADE", "A", "NOT_TRADED"))
fp = "BTCUSDT|RANGE_FADE|LONG|A|BUY"
row = S.resolve({"fingerprint": fp, "symbol": "BTCUSDT", "shadow_outcome": "WIN",
                 "exit_price": 101.0, "held_ms": 900000})
assert row and row["shadow_outcome"] == "WIN", row
rows = S.load()["signals"]
btc = [r for r in rows if r["symbol"] == "BTCUSDT"][0]
eth = [r for r in rows if r["symbol"] == "ETHUSDT"][0]
assert btc["shadow_outcome"] == "WIN" and btc["shadow_held_ms"] == 900000
assert not eth.get("shadow_outcome"), "resolution leaked onto another symbol"
assert S.resolve({"fingerprint": "nope", "symbol": "BTCUSDT", "shadow_outcome": "LOSS"}) is None
assert btc["shadow_outcome"] == "WIN", "a non-matching fingerprint overwrote a verdict"
print("  shadow resolve: verdict on the right row, no leak, no match -> None  OK")

print("\nALL SIGNAL LOG TESTS PASSED")

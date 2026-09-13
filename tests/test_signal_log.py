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

# 7) fingerprint drift: a candidate logged at one grade resolves after the row
#    has moved on to another, and must still find a home.
store.clear()
S.record(sig("SOLUSDT", "RANGE_FADE", "D", "NOT_TRADED"))   # logged at grade D
S.record(sig("SOLUSDT", "RANGE_FADE", "B", "NOT_TRADED"))   # later, grade B -> new row
row = S.resolve({"fingerprint": "SOLUSDT|RANGE_FADE|LONG|D|BUY", "symbol": "SOLUSDT",
                 "shadow_outcome": "LOSS"})
assert row and row["shadow_outcome"] == "LOSS", row
assert row["grade"] == "D", f"resolved the wrong row: grade {row['grade']}"
# and a fingerprint matching nothing still lands on the newest unresolved untaken row
row2 = S.resolve({"fingerprint": "totally-unknown", "symbol": "SOLUSDT", "shadow_outcome": "WIN"})
assert row2 and row2["grade"] == "B", row2
# a taken row is never claimed by the fallback
store.clear()
S.record(sig("XRPUSDT", "RANGE_FADE", "A", "TAKEN"))
assert S.resolve({"fingerprint": "nope", "symbol": "XRPUSDT", "shadow_outcome": "WIN"}) is None
print("  fingerprint drift: exact match wins, fallback finds newest untaken, TAKEN untouched  OK")

# 8) server-side settlement, with candles stubbed so the test needs no network
store.clear()
import time as _t
now = int(_t.time() * 1000)
def mk(sym, entry, stop, tgt, age_ms):
    r = {"fingerprint": f"{sym}|X|LONG|A|BUY", "symbol": sym, "direction": "LONG",
         "setup_type": "X", "grade": "A", "outcome": "NOT_TRADED",
         "entry": entry, "stop": stop, "targets": [tgt]}
    row = S.record(r)
    row["recorded_at"] = now - age_ms
    S.save(S.load())
    return row
mk("AAAUSDT", 100.0, 99.0, 101.0, 10 * 60 * 1000)   # should WIN
mk("BBBUSDT", 100.0, 99.0, 101.0, 10 * 60 * 1000)   # should LOSS
mk("CCCUSDT", 100.0, 99.0, 101.0, 13 * 60 * 60 * 1000)  # stale -> EXPIRED

def fake_fetch(symbol, start_ms, limit=300):
    if symbol == "AAAUSDT":   # runs up to the target, never near the stop
        return [{"start": start_ms, "high": 101.5, "low": 99.8, "close": 101.2}]
    if symbol == "BBBUSDT":   # drops through the stop first
        return [{"start": start_ms, "high": 100.2, "low": 98.5, "close": 98.7}]
    return []                  # CCC: no data -> stays unresolved -> expires on age
klines_stub = type(S.klines_mod)("klines")
klines_stub.fetch_1m = fake_fetch
klines_stub.settle = S.klines_mod.settle.__get__(None, object) if False else None
import types
def settle(row, now_ms=None):
    bars = fake_fetch(row["symbol"], row["recorded_at"])
    if not bars: return None
    t, st = row["targets"][0], row["stop"]
    for b in bars:
        if b["low"] <= st: return "LOSS"
        if b["high"] >= t: return "WIN"
    return None
klines_stub.settle = settle
S.klines_mod = klines_stub

n = S.settle_pending(max_rows=10)
rows = {r["symbol"]: r for r in S.load()["signals"]}
assert rows["AAAUSDT"]["shadow_outcome"] == "WIN", rows["AAAUSDT"]
assert rows["BBBUSDT"]["shadow_outcome"] == "LOSS", rows["BBBUSDT"]
assert rows["CCCUSDT"]["shadow_outcome"] == "EXPIRED", rows["CCCUSDT"]
assert rows["AAAUSDT"]["shadow_source"] == "server"
print(f"  server settlement: {n} rows settled -> WIN / LOSS / EXPIRED as expected  OK")

print("\nALL SIGNAL LOG TESTS PASSED")

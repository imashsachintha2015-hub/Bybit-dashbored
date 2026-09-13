"""Track record assembly, against stubbed stores.

The point of this block is to stop the supervisor repeating mistakes it has no
memory of making, so the tests focus on the two ways that goes wrong: saying
nothing when there is history to report, and stating a confident conclusion off
a sample far too small to support one.
"""
import sys, types
sys.path.insert(0, '/home/user/Bybit-dashbored')

store = {}
fake = types.ModuleType('backend_lib.kv')
fake.kv_get_json = lambda k, d=None: store.get(k, d)
fake.kv_set_json = lambda k, v: store.__setitem__(k, v)
fake.kv_configured = lambda: True
sys.modules['backend_lib.kv'] = fake

from backend_lib import track_record as TR

def trade(sym, setup, r, side="BUY", reason="STOP"):
    return {"symbol": sym, "setup_type": setup, "r_multiple": r, "side": side, "exit_reason": reason}

def setup_stores(trades, signals):
    store["trade_stats"] = {"trade_history": trades}
    store["signal_log"] = {"signals": signals}

# 1) nothing recorded -> say nothing, rather than inventing reassurance
setup_stores([], [])
assert TR.build("BTCUSDT", "RANGE_FADE") == "", TR.build("BTCUSDT", "RANGE_FADE")
print("  empty history -> empty block  OK")

# 2) a small sample is reported WITH its count and no conclusion drawn
setup_stores([trade("BTCUSDT", "RANGE_FADE", 1.2)] * 2, [])
out = TR.build("BTCUSDT", "RANGE_FADE")
assert "only 2 closed trades" in out and "too few" in out, out
assert "100%" not in out, f"drew a conclusion from n=2: {out}"
print("  n=2 -> reported as too few, no win rate claimed  OK")

# 3) a real sample reports rates, and recent losses appear concretely
setup_stores([trade("BTCUSDT", "RANGE_FADE", r) for r in (1.5, -1, 2, -1, -1, 1)], [])
out = TR.build("BTCUSDT", "RANGE_FADE")
assert "6 closed trades" in out and "50% won" in out, out
assert "Recent losses on BTCUSDT" in out, out
print("  n=6 -> win rate and recent losses reported  OK")

# 4) the supervisor's own calibration, including the warning when it is not calibrated
sigs = []
for i in range(6):
    sigs.append({"symbol": "BTCUSDT", "supervisor": {"verdict": "CONFIRM"},
                 "shadow_outcome": "LOSS" if i < 5 else "WIN"})
for i in range(6):
    sigs.append({"symbol": "BTCUSDT", "supervisor": {"verdict": "DOWNGRADE"},
                 "shadow_outcome": "WIN" if i < 5 else "LOSS"})
setup_stores([], sigs)
out = TR.build("BTCUSDT", "RANGE_FADE")
assert "Your own past verdicts" in out, out
assert "NOT done better" in out, f"failed to flag miscalibration: {out}"
print("  confirms losing to downgrades -> miscalibration warning raised  OK")

# 5) and no warning when it IS calibrated
for s in sigs:
    s["shadow_outcome"] = "WIN" if s["supervisor"]["verdict"] == "CONFIRM" else "LOSS"
setup_stores([], sigs)
out = TR.build("BTCUSDT", "RANGE_FADE")
assert "NOT done better" not in out, out
print("  confirms beating downgrades -> no warning  OK")

print("\nALL TRACK RECORD TESTS PASSED")

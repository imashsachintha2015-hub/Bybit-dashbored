"""Absorption + HVN indicator: synthetic bars with a known absorption bar, causality, profile nodes."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib import absorption as A


def flat(n, base=100.0):
    return [dict(t=i * 900, o=base, h=base + 0.5, l=base - 0.5, c=base, v=100.0, tb=50.0) for i in range(n)]


def test_heavy_buying_without_progress_is_sell_absorption():
    b = flat(80); b[70] = dict(t=70 * 900, o=100.0, h=100.6, l=99.6, c=100.05, v=400.0, tb=320.0)      # 4x volume, 60% of it taker buys, close ~flat
    s = A.signals(b); assert [(x["t"], x["side"]) for x in s] == [(70 * 900, "SELL")], s


def test_heavy_selling_without_progress_is_buy_absorption():
    b = flat(80); b[70] = dict(t=70 * 900, o=100.0, h=100.6, l=99.5, c=99.95, v=400.0, tb=80.0)
    s = A.signals(b); assert len(s) == 1 and s[0]["side"] == "BUY"


def test_buying_that_moves_price_is_not_absorption_and_light_volume_is_not_heavy():
    b = flat(80); b[70] = dict(t=70 * 900, o=100.0, h=102.0, l=99.9, c=101.9, v=400.0, tb=320.0)      # price rose 1.9 / ATR ~1
    assert A.signals(b) == []
    b = flat(80); b[70] = dict(t=70 * 900, o=100.0, h=100.6, l=99.6, c=100.05, v=120.0, tb=100.0)     # volume only 1.2x
    assert A.signals(b) == []


def test_no_look_ahead_signals_before_a_cut_do_not_change():
    b = flat(100); b[70] = dict(t=70 * 900, o=100.0, h=100.6, l=99.6, c=100.05, v=400.0, tb=320.0); b[90] = dict(b[70], t=90 * 900)
    full = [s for s in A.signals(b) if s["t"] <= 70 * 900]; cut = A.signals(b[:71]); assert full == cut and len(cut) == 1


def test_profile_nodes_find_the_heavy_price_and_mark_hvn_signals():
    b = flat(120)
    for i in range(0, 20): b[i].update(o=94.0, h=94.5, l=93.5, c=94.0)                               # a lower and a higher area with little volume
    for i in range(60, 100): b[i].update(o=106.0, h=106.5, l=105.5, c=106.0)
    for i in range(20, 60): b[i].update(v=300.0)                                                     # long, heavy stay at 100
    b[100] = dict(t=100 * 900, o=100.0, h=100.5, l=99.6, c=100.0, v=900.0, tb=700.0)
    nd = A.nodes(A.profile(b[:100])); assert nd and abs(nd[0]["price"] - 100.0) < 1.0
    s = A.signals(b); assert s and s[-1]["hvn"] is True
    out = A.compute(b); assert out["ok"] and out["poc"] is not None and out["signals"]


def test_missing_taker_data_gives_no_signals():
    b = flat(80); b[70] = dict(b[70], v=400.0, tb=None); assert A.signals(b) == []


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"): v(); print("ok", k)

"""Tests that DeepSeek is really off.  Run:  python tests/test_deepseek_off.py   (or pytest tests/test_deepseek_off.py)

DeepSeek costs money, so "off" has to hold on every route, not only the one somebody remembered:

  * the switch (backend_lib/deepseek_switch.py): off unless DEEPSEEK_ENABLED is 1/true/yes/on
  * every call site -- news scoring, chart read, market read (direct and over HTTP), post-trade reflection --
    run in a fresh process with a DEEPSEEK_API_KEY present and urllib's urlopen replaced by a recorder:
    once with the switch off (no request may name DeepSeek) and once on (each site must reach for it, so the
    check above cannot pass by accident)

Nothing here touches the network, Bybit, Supabase or the contents of the real databases.
"""
import http.client
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib.deepseek_switch import deepseek_enabled, deepseek_key  # noqa: E402

# Runs in a fresh interpreter so the modules read their key from a clean environment.
DRIVER = r'''
import http.client, json, os, tempfile, threading, urllib.request

sent, step = [], ["setup"]


def refuse(req, *a, **k):
    sent.append([step[0], getattr(req, "full_url", str(req))])
    raise OSError("no network in tests")


urllib.request.urlopen = refuse

import server                                   # its start-up Bybit clock sync lands in refuse() and is ignored
from backend_lib import deepseek_market_reader as mr
from backend_lib import market_knowledge as mk

# a market for the reader to look at, so it runs end to end without a network
bars = [{"start": 1700000000000 + i * 900000, "open": 100 + i * 0.1, "high": 101 + i * 0.1,
         "low": 99 + i * 0.1, "close": 100.5 + i * 0.1, "volume": 1000.0 + i} for i in range(60)]
tick = {"lastPrice": 106.0, "price24hPcnt": 0.01, "highPrice24h": 110.0, "lowPrice24h": 98.0, "turnover24h": 1e6}
mr.fetch_klines = lambda sym, interval="15", limit=60: [dict(b) for b in bars]
mr.fetch_ticker = lambda sym: dict(tick, symbol=sym)
mr.fetch_all_tickers = lambda: {s: dict(tick, symbol=s) for s in mr.SYMBOLS}
mk.SUPABASE_URL = mk.SUPABASE_KEY = ""         # keep the reflection off Supabase

httpd = server.ThreadingDashboardServer(("127.0.0.1", 0), server.DashboardHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def call(method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
    c.request(method, path, body=None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
    r = c.getresponse()
    data = json.loads(r.read().decode("utf-8"))
    c.close()
    return r.status, data


res = {}
step[0] = "budget"
res["budget_take"] = server.llm_budget_take("test")
step[0] = "news"
res["news"] = server.score_headlines_with_deepseek(["Bitcoin ETF approved by SEC"])
step[0] = "reader"
r = mr.get_market_analysis("BTCUSDT", force_refresh=True)
res["reader"] = {"is_ai_live": r.get("is_ai_live"), "model": r.get("model")}
step[0] = "reflection"
kb = mk.MarketKnowledgeBase(db_path=os.path.join(tempfile.mkdtemp(), "k.db"))
r = kb.generate_post_mortem_reflection(
    None, {"symbol": "BTCUSDT", "direction": "BUY", "pnl": -1.0, "pnl_pct": -0.5, "mfe_pct": 0.1, "mae_pct": -0.6, "exit_reason": "SL"},
    {"upper_wick_pct_5m": 40.0, "vol_ratio_5m": 1.2})
res["reflection"] = {"has_text": bool(r.get("reflection"))}
step[0] = "http-chart"
st, b = call("POST", "/api/deepseek/chart-analyze", {"symbol": "BTCUSDT", "timeframe": "15",
                                                      "candles": [{"t": 1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]})
res["http_chart"] = {"status": st, "action": b.get("action"), "is_fallback": b.get("is_fallback"), "rationale": b.get("rationale")}
step[0] = "http-reader"
st, b = call("GET", "/api/deepseek/market-reader?symbol=BTCUSDT&refresh=true")
res["http_reader"] = {"status": st, "is_ai_live": b.get("is_ai_live")}
step[0] = "http-status"
st, b = call("GET", "/api/llm/status")
res["http_status"] = {"status": st, "enabled": b.get("enabled"), "configured": b.get("configured")}
httpd.shutdown()
print("RESULT " + json.dumps({"sent": sent, "res": res}, default=str))
'''


def _run(enabled):
    env = dict(os.environ)
    env.update({
        "DEEPSEEK_ENABLED": "1" if enabled else "0",
        "DEEPSEEK_API_KEY": "sk-test-not-a-real-key",
        "DEEPSEEK_URL": "https://api.deepseek.com/v1/chat/completions",
        "DEEPSEEK_DAILY_CALL_BUDGET": "50",
        "BYBIT_API_KEY": "test", "BYBIT_API_SECRET": "test", "BYBIT_BASE_URL": "https://api-demo.bybit.com",
        "BENZINGA_API_KEY": "", "COINGECKO_API_KEY": "", "SUPABASE_URL": "", "SUPABASE_ANON_KEY": "",
        "PYTHONIOENCODING": "utf-8",
    })
    p = subprocess.run([sys.executable, "-c", DRIVER], cwd=ROOT, env=env, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=180)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, f"driver gave no result (exit {p.returncode})\n--- stdout\n{p.stdout[-2000:]}\n--- stderr\n{p.stderr[-2000:]}"
    return json.loads(lines[-1][len("RESULT "):])


def test_switch_is_off_unless_enabled():
    key = {"DEEPSEEK_API_KEY": "sk-test"}
    for off in ({}, {"DEEPSEEK_ENABLED": ""}, {"DEEPSEEK_ENABLED": "0"}, {"DEEPSEEK_ENABLED": "false"},
                {"DEEPSEEK_ENABLED": "no"}, {"DEEPSEEK_ENABLED": "off"}, {"DEEPSEEK_ENABLED": "2"}, {"DEEPSEEK_ENABLED": "y"}):
        assert not deepseek_enabled({**key, **off}), off
        assert deepseek_key({**key, **off}) == "", off          # a key that is still set is ignored
    for on in ("1", "true", "TRUE", " yes ", "On"):
        assert deepseek_enabled({**key, "DEEPSEEK_ENABLED": on}), on
        assert deepseek_key({**key, "DEEPSEEK_ENABLED": on}) == "sk-test", on
    assert deepseek_key({"DEEPSEEK_ENABLED": "1"}) == ""         # on, but there is no key to send
    assert deepseek_key({"DEEPSEEK_ENABLED": "1", "DEEPSEEK_API_KEY": "  sk-test \n"}) == "sk-test"


def test_env_example_ships_with_deepseek_off():
    lines = [ln.strip() for ln in open(os.path.join(ROOT, ".env.example"), encoding="utf-8")]
    assert "DEEPSEEK_ENABLED=0" in lines
    assert "DEEPSEEK_API_KEY=" in lines                          # no key in the template


def test_nothing_reaches_deepseek_while_off():
    out = _run(enabled=False)
    asked = [s for s in out["sent"] if "deepseek" in s[1].lower()]
    assert not asked, asked
    r = out["res"]
    assert r["budget_take"] is False
    assert r["news"] is None                                     # keyword scoring takes over
    assert r["reader"]["is_ai_live"] is False                    # the local quant read
    assert r["reflection"]["has_text"]                           # the default post-trade notes
    assert r["http_chart"]["status"] == 200 and r["http_chart"]["action"] == "WAIT" and r["http_chart"]["is_fallback"] is True
    assert "switched off" in r["http_chart"]["rationale"], r["http_chart"]
    assert r["http_reader"] == {"status": 200, "is_ai_live": False}
    assert r["http_status"] == {"status": 200, "enabled": False, "configured": False}


def test_every_call_site_does_reach_deepseek_when_on():
    out = _run(enabled=True)                                     # the control: the recorder does see each site
    reached = {step for step, url in out["sent"] if "deepseek" in url.lower()}
    assert {"news", "reader", "reflection", "http-chart", "http-reader"} <= reached, reached
    assert out["res"]["http_status"] == {"status": 200, "enabled": True, "configured": True}


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS {name} ({time.time() - t0:.1f}s)")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

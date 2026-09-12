"""Macro Regime Feed (CoinGecko) -- BTC dominance & total market cap trend.

A broad "risk-off" macro tape (total market cap sliding hard) is another
confluence input: a technically-clean long is worth less when the entire
market is bleeding, so this feeds the same veto-style gate as news/whale.
Ported from server.py; see backend_lib/news.py's docstring for why this
process-local cache doesn't need kv.py the way trade_stats/llm_budget do.
"""
import json
import os
import time
import urllib.request

COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
MARKET_OVERVIEW_TTL = int(os.environ.get("MARKET_OVERVIEW_TTL", "300"))

market_overview_cache = {
    "updated_at": 0,
    "btc_dominance": 0.0,
    "total_market_cap_usd": 0,
    "market_cap_change_24h_pct": 0.0,
    "risk_off": False,
    "is_fallback": True,
    "fallback_reason": "Not yet polled"
}


def fetch_market_overview():
    if not COINGECKO_API_KEY:
        market_overview_cache["is_fallback"] = True
        market_overview_cache["fallback_reason"] = "No COINGECKO_API_KEY configured — the macro risk-off gate is inactive"
        market_overview_cache["risk_off"] = False
        market_overview_cache["updated_at"] = time.time()
        return
    url = f"{COINGECKO_BASE_URL}/global"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "MASIS/3.0",
            "x-cg-demo-api-key": COINGECKO_API_KEY
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = json.loads(r.read().decode())
    except Exception as e:
        print(f"[CoinGecko] Fetch failed, serving last-known cache: {e}")
        market_overview_cache["is_fallback"] = True
        market_overview_cache["fallback_reason"] = f"CoinGecko API unreachable: {e}"
        market_overview_cache["updated_at"] = time.time()
        return

    data = raw.get("data", {}) if isinstance(raw, dict) else {}
    btc_dom = round(data.get("market_cap_percentage", {}).get("btc", 0.0), 2)
    total_mcap = data.get("total_market_cap", {}).get("usd", 0)
    mcap_change = round(data.get("market_cap_change_percentage_24h_usd", 0.0), 2)

    market_overview_cache["btc_dominance"] = btc_dom
    market_overview_cache["total_market_cap_usd"] = total_mcap
    market_overview_cache["market_cap_change_24h_pct"] = mcap_change
    market_overview_cache["risk_off"] = mcap_change <= -3.0
    market_overview_cache["updated_at"] = time.time()
    market_overview_cache["is_fallback"] = False
    market_overview_cache["fallback_reason"] = ""


def get_market_overview_state(force=False):
    stale = (time.time() - market_overview_cache["updated_at"]) > MARKET_OVERVIEW_TTL
    if stale or force:
        fetch_market_overview()
    return dict(market_overview_cache)

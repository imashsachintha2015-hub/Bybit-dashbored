"""
Bybit V5 Demo Trading & DeepSeek Agent Intelligence Server
Integrates:
- Bybit Demo Trading API (Account Balance, Live Positions, Order Placement, Closed PnL)
- DeepSeek AI Institutional Analysis Engine (with MASIS local fallback)
- Real-time Performance Tracking (Win/Loss Count, Profit, Loss, Win Rate)
- Static File Server for Terminal Frontend
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import urllib.parse
import json
import math
import hmac
import hashlib
import time
import os
import re
import sys
import threading
import xml.etree.ElementTree as ET

PORT = int(os.environ.get("PORT", 8080))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────────────────────────────────
# Credentials — read from the environment, never from source.
#
# The previous version of this file carried live Bybit, DeepSeek, Benzinga and
# CoinGecko keys as string literals. Anything committed to a repository, pasted
# into a chat, or zipped and shared carries those keys with it, and a Bybit key
# with trade permissions is enough to move money even on a demo account once it
# is pointed at the live endpoint. Rotate every key that was in the old file —
# they must be treated as compromised — and supply the new ones like this:
#
#   export BYBIT_API_KEY=...        export BYBIT_API_SECRET=...
#   export DEEPSEEK_API_KEY=...     export BENZINGA_API_KEY=...
#   export COINGECKO_API_KEY=...
#
# See .env.example. The server starts without the optional keys and degrades
# the corresponding feature explicitly rather than failing silently.
# ─────────────────────────────────────────────────────────────────────────
def _load_env_file():
    env_file = os.path.join(DIRECTORY, ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("\"'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to parse .env: {e}\n")

_load_env_file()

def _env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        sys.stderr.write(
            f"\n[FATAL] {name} is not set.\n"
            f"        Credentials are read from the environment, not from source.\n"
            f"        See .env.example, then:  export {name}=...\n\n")
        sys.exit(1)
    return val


BYBIT_API_KEY = _env("BYBIT_API_KEY", required=True)
BYBIT_API_SECRET = _env("BYBIT_API_SECRET", required=True)
BYBIT_BASE_URL = _env("BYBIT_BASE_URL", "https://api-demo.bybit.com")

DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_URL = _env("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions")
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-chat")

# Hard ceiling on model spend. Reaching it is not an error state: the engine is
# designed to make every decision locally, with the model acting only as an
# optional second opinion on setups that already passed every local gate.
DEEPSEEK_DAILY_CALL_BUDGET = int(_env("DEEPSEEK_DAILY_CALL_BUDGET", "2000"))

BENZINGA_API_KEY = _env("BENZINGA_API_KEY")
BENZINGA_NEWS_URL = "https://api.benzinga.com/api/v2/news"
NEWS_CACHE_TTL = int(_env("NEWS_CACHE_TTL", "300"))

COINGECKO_API_KEY = _env("COINGECKO_API_KEY")
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
MARKET_OVERVIEW_TTL = int(_env("MARKET_OVERVIEW_TTL", "300"))


class BybitDemoClient:
    def __init__(self, key, secret, base_url):
        self.key = key
        self.secret = secret
        self.base_url = base_url
        self.time_offset = 0
        self.last_sync = 0
        self.sync_time()

    def sync_time(self):
        try:
            req = urllib.request.Request(f"{self.base_url}/v5/market/time", headers={"User-Agent": "MASIS/2.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
                server_ts = int(data["result"]["timeNano"]) // 1000000
                local_ts = int(time.time() * 1000)
                self.time_offset = server_ts - local_ts
                self.last_sync = time.time()
                print(f"[Bybit] Synced server time. Offset: {self.time_offset} ms")
        except Exception as e:
            print(f"[Bybit] Failed to sync server time: {e}")

    def signed_request(self, method, path, params=None, body=None):
        if time.time() - self.last_sync > 300:  # re-sync every 5 min
            self.sync_time()

        ts = str(int(time.time() * 1000) + self.time_offset)
        recv_window = "20000"

        query_str = ""
        if params:
            query_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        body_str = json.dumps(body) if body is not None else ""
        payload = ts + self.key + recv_window + (query_str if method == "GET" else body_str)
        signature = hmac.new(self.secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

        url = f"{self.base_url}{path}" + (f"?{query_str}" if query_str else "")
        req = urllib.request.Request(url, data=body_str.encode("utf-8") if body_str else None, method=method)
        req.add_header("X-BAPI-API-KEY", self.key)
        req.add_header("X-BAPI-TIMESTAMP", ts)
        req.add_header("X-BAPI-SIGN", signature)
        req.add_header("X-BAPI-RECV-WINDOW", recv_window)
        req.add_header("User-Agent", "MASIS/2.0")
        if body_str:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"[Bybit HTTPError {e.code}] {err_body}")
            try:
                return json.loads(err_body)
            except Exception:
                return {"retCode": e.code, "retMsg": err_body}
        except Exception as e:
            print(f"[Bybit Error] {e}")
            return {"retCode": -1, "retMsg": str(e)}

    def get_wallet_balance(self):
        return self.signed_request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})

    def get_positions(self, symbol=None):
        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        return self.signed_request("GET", "/v5/position/list", params)

    def get_closed_pnl(self, limit=50):
        return self.signed_request("GET", "/v5/position/closed-pnl", {"category": "linear", "limit": str(limit)})

    def place_order(self, category, symbol, side, order_type, qty, price=None, tp=None, sl=None):
        body = {
            "category": category,
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty": str(qty),
            "timeInForce": "GTC"
        }
        if price and order_type.lower() == "limit":
            body["price"] = str(price)
        if tp:
            body["takeProfit"] = str(tp)
        if sl:
            body["stopLoss"] = str(sl)
        return self.signed_request("POST", "/v5/order/create", body=body)

    def set_trading_stop(self, category, symbol, stop_loss=None, take_profit=None, position_idx=0):
        """Moves the broker-side stop on an open position.

        The position manager uses this to ratchet the stop to break-even after
        the first target and to trail it afterwards. It matters that this is
        broker-side: a stop that only exists in the browser tab is not a stop.
        """
        body = {"category": category, "symbol": symbol, "positionIdx": position_idx}
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)
        return self.signed_request("POST", "/v5/position/trading-stop", body=body)

    def get_open_orders(self, symbol=None):
        """Returns resting (unfilled/partially filled) orders. Used by the
        same-coin duplicate guard to prevent opening a second limit order
        on a symbol that already has one resting."""
        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        return self.signed_request("GET", "/v5/order/realtime", params)

    def set_leverage(self, symbol, leverage):
        """Sets buy and sell leverage for a linear perp symbol.

        Bybit returns retCode 110043 ('Set leverage not modified') if the
        requested leverage already matches — that is harmless and callers
        should treat it as success."""
        body = {
            "category": "linear",
            "symbol": symbol,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage)
        }
        return self.signed_request("POST", "/v5/position/set-leverage", body=body)

    def switch_margin_mode(self, symbol, mode, leverage=10):
        """Switches between cross (tradeMode=0) and isolated (tradeMode=1)
        margin for a linear perp symbol.  Leverage must be supplied because
        the Bybit endpoint requires it on every call.

        Returns retCode 110026 ('Position mode is not modified') if already
        set — harmless, callers should treat as success."""
        trade_mode = 0 if str(mode).lower() == "cross" else 1
        body = {
            "category": "linear",
            "symbol": symbol,
            "tradeMode": trade_mode,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage)
        }
        return self.signed_request("POST", "/v5/position/switch-isolated", body=body)

    def close_position(self, category, symbol, side, qty):
        # To close a position, place an opposing reduceOnly market order
        close_side = "Sell" if side.lower() == "buy" else "Buy"
        body = {
            "category": category,
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": str(qty),
            "reduceOnly": True,
            "timeInForce": "IOC"
        }
        return self.signed_request("POST", "/v5/order/create", body=body)


bybit_client = BybitDemoClient(BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_BASE_URL)

# In-memory Local Trade History Store (persisted to JSON)
STATS_FILE = os.path.join(DIRECTORY, "trade_stats.json")

def load_trade_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    # A fresh install starts at zero. The previous version seeded this file with
    # 14 wins, 6 losses, $3,482 of profit and four invented trades, so the
    # dashboard displayed a 70% win rate and a 3.1 profit factor before the
    # system had placed a single order. Any number shown here should have been
    # earned.
    return {
        "win_count": 0,
        "loss_count": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "trade_history": []
    }

trade_stats = load_trade_stats()
trade_stats_lock = threading.Lock()

def save_trade_stats():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(trade_stats, f, indent=2)
    except Exception as e:
        print(f"[Stats] Failed to save stats: {e}")


_closed_pnl_cache = {"timestamp": 0, "trades": []}

# ─────────────────────────────────────────────────────────────────────────
# Kline / Candlestick Cache & Multi-Exchange Proxy
# ─────────────────────────────────────────────────────────────────────────
_kline_cache = {}
_kline_lock = threading.Lock()

def get_cached_klines(symbol, interval="5", limit=60, end_time=None):
    cache_key = f"{symbol}_{interval}_{limit}_{end_time or 'latest'}"
    now = time.time()
    with _kline_lock:
        if cache_key in _kline_cache:
            entry = _kline_cache[cache_key]
            if now - entry["time"] < 15:  # 15s TTL
                return entry["data"]

    candles = []
    # 1. Bybit Linear Kline
    bybit_url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    if end_time:
        bybit_url += f"&end={end_time}"
    try:
        req = urllib.request.Request(bybit_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            raw_list = data.get("result", {}).get("list", [])
            if raw_list:
                for r in reversed(raw_list):
                    candles.append({
                        "start": int(r[0]),
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5])
                    })
    except Exception as e:
        pass

    # 2. OKX Fallback if Bybit returned nothing
    if not candles:
        okx_interval_map = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "120": "2H", "240": "4H", "D": "1D"}
        bar = okx_interval_map.get(str(interval), "5m")
        inst_id = symbol.replace("USDT", "-USDT-SWAP")
        okx_url = f"https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
        try:
            req = urllib.request.Request(okx_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                raw_list = data.get("data", [])
                if raw_list:
                    for r in reversed(raw_list):
                        candles.append({
                            "start": int(r[0]),
                            "open": float(r[1]),
                            "high": float(r[2]),
                            "low": float(r[3]),
                            "close": float(r[4]),
                            "volume": float(r[5])
                        })
        except Exception as e:
            pass

    with _kline_lock:
        if candles:
            _kline_cache[cache_key] = {"time": now, "data": candles}
        elif cache_key in _kline_cache:
            return _kline_cache[cache_key]["data"]

    return candles



# ─────────────────────────────────────────────────────────────────────────
# LLM call budget.
#
# The old build had no budget and three uncapped call sites: on every decision
# change (which oscillated on a 3-second timer across five symbols), a blind
# 60-second refresh, and a full re-score of every news headline every 90
# seconds. That is on the order of 2,400 calls a day, the large majority of
# them re-analysing a state that had not changed, and the output was prose that
# no code read — so none of it could alter a single trading decision.
#
# Here every call is counted, attributed to a caller, and refused past the
# ceiling. Refusal is a normal operating state: the engine's decisions are made
# locally and the model only ever acts as an optional second opinion.
# ─────────────────────────────────────────────────────────────────────────
llm_budget = {
    "day": time.strftime("%Y-%m-%d", time.gmtime()),
    "calls": 0,
    "by_caller": {},
    "refused": 0,
    "cache_hits": 0,
}
llm_budget_lock = threading.Lock()


def llm_budget_take(caller):
    """Reserves one call against today's budget. False means do not call."""
    if not DEEPSEEK_API_KEY:
        return False
    with llm_budget_lock:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if llm_budget["day"] != today:
            llm_budget.update({"day": today, "calls": 0, "by_caller": {}, "refused": 0, "cache_hits": 0})
        if DEEPSEEK_DAILY_CALL_BUDGET > 0 and llm_budget["calls"] >= DEEPSEEK_DAILY_CALL_BUDGET:
            llm_budget["refused"] += 1
            return False
        llm_budget["calls"] += 1
        llm_budget["by_caller"][caller] = llm_budget["by_caller"].get(caller, 0) + 1
        return True


def llm_budget_note_cache_hit():
    with llm_budget_lock:
        llm_budget["cache_hits"] += 1


def llm_budget_status():
    with llm_budget_lock:
        return {
            "day": llm_budget["day"],
            "calls_today": llm_budget["calls"],
            "daily_budget": DEEPSEEK_DAILY_CALL_BUDGET,
            "remaining": max(0, DEEPSEEK_DAILY_CALL_BUDGET - llm_budget["calls"]),
            "by_caller": dict(llm_budget["by_caller"]),
            "refused": llm_budget["refused"],
            "cache_hits": llm_budget["cache_hits"],
            "configured": bool(DEEPSEEK_API_KEY),
        }


# ─────────────────────────────────────────────────────────────────────────
# DIV-10: Benzinga News Sentinel
# Continuously monitors Benzinga for crypto-market-moving headlines, scores
# each one for sentiment/impact (via DeepSeek, with a keyword-based fallback),
# and exposes an aggregate sentiment signal the MASIS confluence gate uses to
# veto technically-bullish setups that are being contradicted by bad news.
# ─────────────────────────────────────────────────────────────────────────
NEWS_NEGATIVE_KEYWORDS = [
    "hack", "hacked", "exploit", "drain", "rug pull", "ban", "banned", "lawsuit", "sues", "sued",
    "charges", "fraud", "crash", "plunge", "dump", "liquidation", "liquidated", "bankrupt",
    "insolvent", "outage", "delist", "sec charges", "investigation", "halt", "warning", "downgrade"
]
NEWS_POSITIVE_KEYWORDS = [
    "etf approval", "approved", "partnership", "adoption", "surge", "rally", "soars", "upgrade",
    "integrates", "integration", "launch", "listing", "record high", "breakout", "inflow",
    "institutional", "buy the dip", "bullish", "all-time high"
]

news_cache = {
    "updated_at": 0,
    "articles": [],
    "sentiment_score": 0.0,
    "sentiment_label": "NEUTRAL",
    "headline": "No major catalysts detected",
    "is_fallback": True,
    "fallback_reason": "Not yet polled"
}
news_lock = threading.Lock()

def keyword_sentiment(title):
    t = title.lower()
    for kw in NEWS_NEGATIVE_KEYWORDS:
        if kw in t:
            return -0.6, "HIGH" if kw in ("hack", "hacked", "exploit", "sec charges", "fraud") else "MEDIUM"
    for kw in NEWS_POSITIVE_KEYWORDS:
        if kw in t:
            return 0.5, "MEDIUM"
    return 0.0, "LOW"


# Persistent store of headline -> (score, impact). The old build re-sent all 15
# headlines to the model every 90 seconds, so the same unchanged headline was
# scored dozens of times an hour. Headlines are immutable once published, so a
# score only ever needs computing once.
headline_scores = {}
headline_scores_lock = threading.Lock()


def score_headlines_with_deepseek(headlines):
    """Scores only headlines not already scored. Returns {index: (score, impact)}.

    Returns results for every index it can, drawing on the cache for the ones it
    has seen before, so a feed where nothing has changed costs zero model calls.
    """
    if not headlines:
        return None
    if not DEEPSEEK_API_KEY:
        return None

    with headline_scores_lock:
        unscored = [(i, h) for i, h in enumerate(headlines) if h not in headline_scores]
        cached = {i: headline_scores[h] for i, h in enumerate(headlines) if h in headline_scores}

    if not unscored:
        return cached  # nothing new in the feed — no call at all

    if not llm_budget_take("news-scoring"):
        return cached or None
    numbered = "\n".join(f"{i}. {h}" for i, h in unscored)
    prompt = f"""Score each crypto-market headline below for trading sentiment.
Indices are not contiguous — echo back exactly the index given for each headline.
Return ONLY a JSON array (no prose) of objects: [{{"i": <index>, "score": <-1.0 to 1.0>, "impact": "LOW"|"MEDIUM"|"HIGH"}}]

Headlines:
{numbered}"""
    try:
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are a quantitative crypto news-sentiment scoring engine. Output strict JSON only."},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 500,
            "temperature": 0.1
        }).encode("utf-8")
        req = urllib.request.Request(
            DEEPSEEK_URL, data=body,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json", "User-Agent": "MASIS/2.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            res = json.loads(r.read().decode())
            text = res["choices"][0]["message"]["content"]
            match = re.search(r"\[.*\]", text, re.DOTALL)
            arr = json.loads(match.group(0) if match else text)
            out = dict(cached)
            with headline_scores_lock:
                for item in arr:
                    idx = int(item["i"])
                    val = (float(item.get("score", 0)), item.get("impact", "LOW"))
                    out[idx] = val
                    if 0 <= idx < len(headlines):
                        headline_scores[headlines[idx]] = val
                # Bound the cache; headlines age out of the feed anyway.
                if len(headline_scores) > 500:
                    for k in list(headline_scores)[:200]:
                        del headline_scores[k]
            return out
    except Exception as e:
        print(f"[Benzinga/DeepSeek Sentiment] Falling back to keyword scorer: {e}")
        return cached or None


def parse_benzinga_xml(xml_text):
    """Benzinga's v2/news endpoint serves XML for this key/tier regardless of Accept
    header or format= param, so we parse the wire format directly rather than fight it."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for item in root.findall("item"):
        stocks = [{"name": s.findtext("name")} for s in item.findall("./stocks/item") if s.findtext("name")]
        items.append({
            "id": item.findtext("id"),
            "title": (item.findtext("title") or "").strip(),
            "created": item.findtext("created") or "",
            "url": item.findtext("url") or "",
            "stocks": stocks
        })
    return items


def fetch_benzinga_news():
    """Pulls latest crypto headlines from Benzinga, scores sentiment, updates news_cache."""
    if not BENZINGA_API_KEY:
        with news_lock:
            news_cache["is_fallback"] = True
            news_cache["fallback_reason"] = "No BENZINGA_API_KEY configured — the news gate is inactive and will neither block nor support any setup"
            news_cache["updated_at"] = time.time()
        return
    params = {
        "token": BENZINGA_API_KEY,
        "channels": "Cryptocurrency",
        "pagesize": "15"
    }
    url = f"{BENZINGA_NEWS_URL}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/xml", "User-Agent": "MASIS/2.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = parse_benzinga_xml(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[Benzinga] Fetch failed, serving last-known cache: {e}")
        with news_lock:
            news_cache["is_fallback"] = True
            news_cache["fallback_reason"] = f"Benzinga API unreachable: {e}"
            news_cache["updated_at"] = time.time()
        return

    titles = [a.get("title", "").strip() for a in raw if a.get("title")]
    scored = score_headlines_with_deepseek(titles)

    articles = []
    weighted_sum = 0.0
    weight_total = 0.0
    for idx, a in enumerate(raw[:15]):
        title = a.get("title", "").strip()
        if not title:
            continue
        if scored and idx in scored:
            score, impact = scored[idx]
        else:
            score, impact = keyword_sentiment(title)

        stocks = [s.get("name") for s in a.get("stocks", []) if s.get("name")]
        articles.append({
            "id": a.get("id"),
            "title": title,
            "time": a.get("created", ""),
            "url": a.get("url", ""),
            "tickers": stocks,
            "sentiment_score": round(score, 2),
            "impact": impact
        })

        # Recency-weighted aggregate: earlier items in the feed are more recent
        w = 1.0 / (idx + 1)
        weighted_sum += score * w
        weight_total += w

    agg_score = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0
    label = "BULLISH" if agg_score > 0.2 else ("BEARISH" if agg_score < -0.2 else "NEUTRAL")

    with news_lock:
        news_cache["articles"] = articles
        news_cache["sentiment_score"] = agg_score
        news_cache["sentiment_label"] = label
        news_cache["headline"] = articles[0]["title"] if articles else "No major catalysts detected"
        news_cache["updated_at"] = time.time()
        news_cache["is_fallback"] = scored is None
        news_cache["fallback_reason"] = "" if scored is not None else "DeepSeek scoring unavailable; used keyword heuristic"


def get_news_state(force=False):
    with news_lock:
        stale = (time.time() - news_cache["updated_at"]) > NEWS_CACHE_TTL
    if stale or force:
        fetch_benzinga_news()
    with news_lock:
        return dict(news_cache)


# ─────────────────────────────────────────────────────────────────────────
# Macro Regime Feed (CoinGecko) — BTC dominance & total market cap trend.
# A broad "risk-off" macro tape (total market cap sliding hard) is another
# confluence input: a technically-clean long is worth less when the entire
# market is bleeding, so this feeds the same veto-style gate as news/whale.
# ─────────────────────────────────────────────────────────────────────────
market_overview_cache = {
    "updated_at": 0,
    "btc_dominance": 0.0,
    "total_market_cap_usd": 0,
    "market_cap_change_24h_pct": 0.0,
    "risk_off": False,
    "is_fallback": True,
    "fallback_reason": "Not yet polled"
}
market_overview_lock = threading.Lock()


def fetch_market_overview():
    if not COINGECKO_API_KEY:
        with market_overview_lock:
            market_overview_cache["is_fallback"] = True
            market_overview_cache["fallback_reason"] = "No COINGECKO_API_KEY configured — the macro risk-off gate is inactive"
            market_overview_cache["risk_off"] = False
            market_overview_cache["updated_at"] = time.time()
        return
    url = f"{COINGECKO_BASE_URL}/global"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "MASIS/2.0",
            "x-cg-demo-api-key": COINGECKO_API_KEY
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = json.loads(r.read().decode())
    except Exception as e:
        print(f"[CoinGecko] Fetch failed, serving last-known cache: {e}")
        with market_overview_lock:
            market_overview_cache["is_fallback"] = True
            market_overview_cache["fallback_reason"] = f"CoinGecko API unreachable: {e}"
            market_overview_cache["updated_at"] = time.time()
        return

    data = raw.get("data", {}) if isinstance(raw, dict) else {}
    btc_dom = round(data.get("market_cap_percentage", {}).get("btc", 0.0), 2)
    total_mcap = data.get("total_market_cap", {}).get("usd", 0)
    mcap_change = round(data.get("market_cap_change_percentage_24h_usd", 0.0), 2)

    with market_overview_lock:
        market_overview_cache["btc_dominance"] = btc_dom
        market_overview_cache["total_market_cap_usd"] = total_mcap
        market_overview_cache["market_cap_change_24h_pct"] = mcap_change
        market_overview_cache["risk_off"] = mcap_change <= -3.0
        market_overview_cache["updated_at"] = time.time()
        market_overview_cache["is_fallback"] = False
        market_overview_cache["fallback_reason"] = ""


def get_market_overview_state(force=False):
    with market_overview_lock:
        stale = (time.time() - market_overview_cache["updated_at"]) > MARKET_OVERVIEW_TTL
    if stale or force:
        fetch_market_overview()
    with market_overview_lock:
        return dict(market_overview_cache)


# ─────────────────────────────────────────────────────────────────────────
# Supervisor verdict.
#
# The model is asked one question, about one fully-formed candidate that has
# already passed every local gate, and must answer in a fixed JSON shape the
# engine consumes: CONFIRM, DOWNGRADE or VETO. That is the only point in the
# pipeline where a model's answer can change an outcome.
#
# It replaces the old free-text "institutional executive brief" — five prose
# sections at 700 max_tokens, generated on a timer, that no code parsed and no
# decision depended on. Same information in, roughly a fifth of the tokens, and
# now it is actually wired to the gate.
# ─────────────────────────────────────────────────────────────────────────
from backend_lib import supervisor as supervisor_mod
from backend_lib import signal_log as signal_log_mod

SUPERVISOR_SYSTEM = supervisor_mod.SUPERVISOR_SYSTEM


def run_supervisor_verdict(payload):
    """Returns {verdict, confidence, rationale, is_fallback, ...} with full track record."""
    return supervisor_mod.run_supervisor_verdict(payload)


def _sanitize_for_json(obj):
    """Recursively replaces NaN/Infinity/-Infinity (valid Python floats, not
    valid JSON) with None, and anything json.dumps can't otherwise handle
    with its str(). Guarantees _send_json always emits parseable JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def _send_json(self, status, payload):
        # json.dumps allows Python's NaN/Infinity by default, which are not
        # valid JSON tokens -- a browser's JSON.parse rejects them outright
        # ("unexpected character at line 1 column 1"). Sanitize recursively so
        # this endpoint can never emit a body the client can't parse.
        body = json.dumps(_sanitize_for_json(payload)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        global _closed_pnl_cache
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # 1. API: Account & Wallet Balance
        if self.path == "/api/account":
            res = bybit_client.get_wallet_balance()
            self._send_json(200, res)
            return

        # 2. API: Live Open Positions
        if self.path.startswith("/api/positions"):
            res = bybit_client.get_positions()
            self._send_json(200, res)
            return

        # 3. API: Closed PnL & performance metrics.
        #
        # Previously this added Bybit's closed-PnL list to the locally-recorded
        # stats. But the client ALSO posts every close to /api/trades/record, so
        # each trade was counted twice — once from each source — and the win
        # rate, profit factor and net P&L on the dashboard were all derived from
        # doubled figures.
        #
        # Bybit's closed-PnL feed is now the single source of truth for money.
        # The local records supply only the things Bybit cannot know: which
        # 3-reset. API: Reset Performance Scorecard (Preserves 100% of all trade history data)
        if self.path.startswith("/api/performance/reset"):
            with trade_stats_lock:
                if hasattr(bybit_client, "sync_time"):
                    bybit_client.sync_time()
                bybit_server_ms = int(time.time() * 1000) + getattr(bybit_client, 'time_offset', 0)
                latest_closed_ts = 0
                if _closed_pnl_cache.get("trades"):
                    for t in _closed_pnl_cache["trades"][:10]:
                        t_ts = int(t.get("updatedTime") or t.get("createdTime") or 0)
                        if t_ts > latest_closed_ts:
                            latest_closed_ts = t_ts
                now_anchor = max(bybit_server_ms, latest_closed_ts + 1000)
                trade_stats["reset_anchor_time"] = now_anchor
                trade_stats["win_count"] = 0
                trade_stats["loss_count"] = 0
                trade_stats["gross_profit"] = 0.0
                trade_stats["gross_loss"] = 0.0
                save_trade_stats()
            _closed_pnl_cache["timestamp"] = 0
            _closed_pnl_cache["trades"] = []
            self._send_json(200, {
                "success": True,
                "reset_anchor_time": now_anchor,
                "message": "Performance metrics reset to 0 (all trade history data preserved)."
            })
            return

        # 3. API: Closed PnL & performance metrics.
        #
        # Bybit's closed-PnL feed is the single source of truth for money.
        # We paginate across all pages with a 15-second cache so no trades are cut off.
        if self.path.startswith("/api/performance"):
            now = time.time()
            if "_closed_pnl_cache" not in globals():
                _closed_pnl_cache = {"timestamp": 0, "trades": []}

            if not _closed_pnl_cache["trades"] or (now - _closed_pnl_cache["timestamp"] >= 15):
                cursor = ''
                all_trades = []
                for _ in range(15):
                    params = {'category': 'linear', 'limit': '100'}
                    if cursor:
                        params['cursor'] = cursor
                    bybit_pnl = bybit_client.signed_request('GET', '/v5/position/closed-pnl', params)
                    r_res = bybit_pnl.get("result", {})
                    items = r_res.get("list", []) or []
                    if not items:
                        break
                    all_trades.extend(items)
                    cursor = r_res.get("nextPageCursor")
                    if not cursor:
                        break
                    if all_trades:
                        _closed_pnl_cache = {"timestamp": now, "trades": all_trades}

            closed_list = _closed_pnl_cache.get("trades", [])

            with trade_stats_lock:
                local_history = list(trade_stats["trade_history"])
                reset_anchor = trade_stats.get("reset_anchor_time", 0)

            def annotate(row):
                """Attaches the local reasoning snapshot to a Bybit closed-PnL row."""
                try:
                    ts = int(row.get("updatedTime") or row.get("createdTime") or 0)
                except (TypeError, ValueError):
                    ts = 0
                best = None
                for loc in local_history:
                    if loc.get("symbol") != row.get("symbol"):
                        continue
                    if str(loc.get("side", "")).upper() != str(row.get("side", "")).upper():
                        continue
                    if abs(int(loc.get("recorded_at", 0)) - ts) < 120000:
                        best = loc
                        break
                return best or {}

            from datetime import datetime, timezone, timedelta
            SL_TZ = timezone(timedelta(hours=5, minutes=30))
            today_sl_str = datetime.now(tz=SL_TZ).strftime("%Y-%m-%d")
            yesterday_sl_str = (datetime.now(tz=SL_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")

            w_count = l_count = 0
            g_profit = g_loss = 0.0

            today_w = today_l = 0
            today_gp = today_gl = 0.0

            yest_w = yest_l = 0
            yest_gp = yest_gl = 0.0

            merged = []
            for row in closed_list:
                try:
                    pnl = float(row.get("closedPnl", 0))
                except (TypeError, ValueError):
                    continue

                ts_ms = int(row.get("updatedTime") or row.get("createdTime") or 0)
                dt_sl = datetime.fromtimestamp(ts_ms / 1000, tz=SL_TZ) if ts_ms else datetime.now(tz=SL_TZ)
                d_str = dt_sl.strftime("%Y-%m-%d")

                # Metrics reset anchor: only aggregate metrics for trades closed after reset
                is_after_reset = (ts_ms >= reset_anchor) if reset_anchor else True

                if is_after_reset:
                    if pnl > 0:
                        w_count += 1
                        g_profit += pnl
                        if d_str == today_sl_str:
                            today_w += 1
                            today_gp += pnl
                        elif d_str == yesterday_sl_str:
                            yest_w += 1
                            yest_gp += pnl
                    elif pnl < 0:
                        l_count += 1
                        g_loss += abs(pnl)
                        if d_str == today_sl_str:
                            today_l += 1
                            today_gl += abs(pnl)
                        elif d_str == yesterday_sl_str:
                            yest_l += 1
                            yest_gl += abs(pnl)

                extra = annotate(row)
                entry_p = float(row.get("avgEntryPrice") or 0)
                exit_p = float(row.get("avgExitPrice") or 0)

                # In Bybit V5 closed-pnl, row.get("side") is the CLOSING order's side.
                # Closing order "Sell" = original position was "BUY" (LONG).
                # Closing order "Buy" = original position was "SELL" (SHORT).
                raw_side = str(row.get("side", "")).upper()
                pos_side = extra.get("side")
                if not pos_side:
                    pos_side = "BUY" if raw_side == "SELL" else "SELL"

                is_short = pos_side.upper() in ("SELL", "SHORT")

                stop_val = float(extra.get("stop", 0) or extra.get("stopLoss", 0) or 0)
                target_list = extra.get("targets") or ([] if not extra.get("target") else [extra.get("target")])
                if not stop_val:
                    if pnl < 0 and exit_p:
                        stop_val = exit_p
                    else:
                        stop_val = round(entry_p * (1.018 if is_short else 0.982), 4)

                if not target_list:
                    if pnl > 0 and exit_p:
                        target_list = [exit_p]
                    else:
                        risk_dist = abs(entry_p - stop_val) if stop_val else (entry_p * 0.015)
                        target_val = round(entry_p - risk_dist * 2.0 if is_short else entry_p + risk_dist * 2.0, 4)
                        target_list = [target_val]

                merged.append({
                    "id": row.get("orderId", "")[-8:] or "--",
                    "time": dt_sl.strftime("%Y-%m-%d %H:%M"),
                    "exitTime": ts_ms,
                    "createdTime": int(row.get("createdTime") or 0),
                    "symbol": row.get("symbol"),
                    "side": pos_side.upper(),
                    "entry": entry_p,
                    "exit": exit_p,
                    "stop": stop_val,
                    "targets": target_list,
                    "nextSupport": extra.get("nextSupport"),
                    "nextResistance": extra.get("nextResistance"),
                    "isScalp": bool(extra.get("isScalp")),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(float(row.get("closedPnl", 0)) / max(float(row.get("cumEntryValue") or 1), 1e-9) * 100, 3),
                    "status": "WIN" if pnl > 0 else "LOSS",
                    "setup_type": extra.get("setup_type", ""),
                    "grade": extra.get("grade", ""),
                    "r_multiple": extra.get("r_multiple"),
                    "exit_reason": extra.get("exit_reason", ""),
                    "reason": extra.get("reason", "")
                })

            # Ensure newly closed trade is always on top (descending timestamp)
            merged.sort(key=lambda x: int(x.get("exitTime") or x.get("createdTime") or 0), reverse=True)

            tot_trades = w_count + l_count
            win_rate = round((w_count / tot_trades) * 100, 1) if tot_trades > 0 else 0.0
            profit_factor = round(g_profit / g_loss, 2) if g_loss > 0 else (0.0 if g_profit == 0 else 99.9)

            r_values = [m["r_multiple"] for m in merged if isinstance(m.get("r_multiple"), (int, float)) and ((m.get("exitTime") or 0) >= reset_anchor if reset_anchor else True)]
            r_wins = [r for r in r_values if r > 0]
            r_losses = [abs(r) for r in r_values if r <= 0]
            expectancy_r = round(sum(r_values) / len(r_values), 3) if r_values else None

            tot_today = today_w + today_l
            tot_yest = yest_w + yest_l

            res = {
                "strategy_mode": "SWING_RUNNER",
                "win_count": w_count,
                "loss_count": l_count,
                "total_trades": tot_trades,
                "win_rate": win_rate,
                "gross_profit": round(g_profit, 2),
                "gross_loss": round(g_loss, 2),
                "net_pnl": round(g_profit - g_loss, 2),
                "profit_factor": profit_factor,
                "reset_anchor_time": reset_anchor,
                "today": {
                    "total_trades": tot_today,
                    "win_count": today_w,
                    "loss_count": today_l,
                    "win_rate": round((today_w / tot_today * 100), 1) if tot_today else 0.0,
                    "gross_profit": round(today_gp, 2),
                    "gross_loss": round(today_gl, 2),
                    "net_pnl": round(today_gp - today_gl, 2),
                    "profit_factor": round(today_gp / today_gl, 2) if today_gl > 0 else (0.0 if today_gp == 0 else 99.9)
                },
                "yesterday": {
                    "total_trades": tot_yest,
                    "win_count": yest_w,
                    "loss_count": yest_l,
                    "win_rate": round((yest_w / tot_yest * 100), 1) if tot_yest else 0.0,
                    "gross_profit": round(yest_gp, 2),
                    "gross_loss": round(yest_gl, 2),
                    "net_pnl": round(yest_gp - yest_gl, 2),
                    "profit_factor": round(yest_gp / yest_gl, 2) if yest_gl > 0 else (0.0 if yest_gp == 0 else 99.9)
                },
                "expectancy_r": expectancy_r,
                "avg_win_r": round(sum(r_wins) / len(r_wins), 2) if r_wins else None,
                "avg_loss_r": round(sum(r_losses) / len(r_losses), 2) if r_losses else None,
                "r_sample_size": len(r_values),
                "trade_history": merged,
                "timezone": "Asia/Colombo (UTC+05:30)",
                "accounting_note": "Bybit closed-PnL is the single source of truth for money; local records supply setup and exit-reason metadata only."
            }

            try:
                from backend_lib.measurement_journal import mj
                auth_perf = mj.get_authoritative_performance()
                res["authoritative_journal"] = {
                    "total_trades": auth_perf.get("total_trades", 0),
                    "win_count": auth_perf.get("win_count", 0),
                    "loss_count": auth_perf.get("loss_count", 0),
                    "win_rate": auth_perf.get("win_rate", 0.0),
                    "gross_profit": auth_perf.get("gross_profit", 0.0),
                    "gross_loss": auth_perf.get("gross_loss", 0.0),
                    "net_pnl_after_fees": auth_perf.get("net_pnl", 0.0),
                    "total_fees_paid": auth_perf.get("total_fees_paid", 0.0),
                    "profit_factor": auth_perf.get("profit_factor", 1.0),
                    "expectancy_r": auth_perf.get("expectancy_r", 0.0),
                    "avg_mfe_pct": auth_perf.get("avg_mfe_pct", 0.0),
                    "avg_mae_pct": auth_perf.get("avg_mae_pct", 0.0),
                    "decisions_evaluated": auth_perf.get("decisions_evaluated", 0),
                    "decisions_vetoed": auth_perf.get("decisions_vetoed", 0),
                    "fee_friction_vetoes": auth_perf.get("fee_friction_vetoes", 0),
                    "red_team_vetoes": auth_perf.get("red_team_vetoes", 0)
                }
            except Exception:
                pass

            self._send_json(200, res)
            return

        # 3b. API: DeepSeek Smart Profit Claimer history & state
        if self.path.startswith("/api/profit-claimer/history"):
            claim_file = os.path.join(DIRECTORY, "scratch", "deepseek_claimer_decisions.json")
            history = []
            if os.path.exists(claim_file):
                try:
                    with open(claim_file, "r", encoding="utf-8") as f:
                        history = json.load(f)
                except Exception:
                    pass
            self._send_json(200, {"history": history, "count": len(history)})
            return

        # 3c. API: Agent Target Mode & Progress
        if self.path.startswith("/api/agent/target-mode"):
            from backend_lib.market_knowledge import kb
            wb = bybit_client.get_wallet_balance()
            coins = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
            usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
            eq = float(usdt.get("equity", 0)) if usdt else None
            state = kb.get_target_state(current_equity=eq)
            self._send_json(200, state)
            return

        # 3c-1. API: Dynamic Market Feasibility Determination
        if self.path.startswith("/api/agent/target-feasibility"):
            from backend_lib.market_knowledge import kb
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            tgt_q = q.get("target_equity", [None])[0]
            hrs_q = q.get("time_horizon_hours", [None])[0]
            wb = bybit_client.get_wallet_balance()
            coins = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
            usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
            eq = float(usdt.get("equity", 0)) if usdt else None
            feasibility = kb.evaluate_target_feasibility(
                target_equity=float(tgt_q) if tgt_q else 15.0,
                time_horizon_hours=float(hrs_q) if hrs_q else 24.0,
                current_equity=eq
            )
            self._send_json(200, feasibility)
            return

        # 3c-2. API: DeepSeek Pre-Trade Gatekeeper Decisions
        if self.path.startswith("/api/agent/gatekeeper-decisions"):
            decisions_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scratch', 'deepseek_pre_trade_decisions.json')
            decisions = []
            if os.path.exists(decisions_path):
                try:
                    with open(decisions_path, 'r', encoding='utf-8') as f:
                        decisions = json.load(f)
                except Exception:
                    pass
            self._send_json(200, {"decisions": list(reversed(decisions)), "count": len(decisions)})
            return

        # 3c-3. API: Historical Pattern Archaeologist (Super-Trades & Anti-Rules)
        if self.path.startswith("/api/agent/archaeologist"):
            super_trades_path = os.path.join(DIRECTORY, "scratch", "historical_super_trades.json")
            data = {"super_trades": [], "anti_rules": [], "super_trades_count": 0}
            if os.path.exists(super_trades_path):
                try:
                    with open(super_trades_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    pass
            self._send_json(200, data)
            return

        # 3c-4. API: Adversarial Red Team Gatekeeper (DIV-14)
        if self.path.startswith("/api/agent/redteam"):
            from backend_lib.measurement_journal import mj
            perf = mj.get_authoritative_performance()
            recent_vetoes = mj.get_recent_decisions(limit=15)
            self._send_json(200, {
                "status": "ACTIVE",
                "mode": "FALSIFICATION_GUARD_ON",
                "fee_cap_pct": 25.0,
                "friction_roundtrip_pct": 0.150,
                "min_ev_hurdle_r": 0.35,
                "decisions_evaluated": perf.get("decisions_evaluated", 0),
                "decisions_executed": perf.get("decisions_executed", 0),
                "decisions_vetoed": perf.get("decisions_vetoed", 0),
                "fee_friction_vetoes": perf.get("fee_friction_vetoes", 0),
                "negative_ev_vetoes": perf.get("negative_ev_vetoes", 0),
                "red_team_vetoes": perf.get("red_team_vetoes", 0),
                "recent_decisions": recent_vetoes
            })
            return

        # 3c-5. API: Authoritative Signal Decision Journal (NO-TRADE & Executed decisions)
        if self.path.startswith("/api/journal/decisions"):
            from backend_lib.measurement_journal import mj
            decisions = mj.get_recent_decisions(limit=60)
            self._send_json(200, {"decisions": decisions, "count": len(decisions)})
            return

        # 3c-6. API: Authoritative Measurement Journal Performance
        if self.path.startswith("/api/journal/performance"):
            from backend_lib.measurement_journal import mj
            perf = mj.get_authoritative_performance()
            self._send_json(200, perf)
            return

        # 3d. API: Market Prophet Knowledge Base
        if self.path.startswith("/api/market-prophet/knowledge"):
            from backend_lib.market_knowledge import kb
            summary = kb.get_knowledge_summary()
            self._send_json(200, summary)
            return

        # 3e-1. API: CME-X4 V4 Shadow Mode Candidates
        if self.path.startswith("/api/cme-x4/shadow/candidates"):
            from backend_lib.cme_x4_shadow_db import shadow_db
            candidates = shadow_db.get_recent_candidates(limit=60)
            self._send_json(200, {"candidates": candidates, "count": len(candidates)})
            return

        # 3e-2. API: CME-X4 V4 Shadow Mode Live Performance & Stats
        if self.path.startswith("/api/cme-x4/shadow/stats"):
            from backend_lib.cme_x4_shadow_db import shadow_db
            perf = shadow_db.get_shadow_performance()
            self._send_json(200, perf)
            return

        # 3e-3. API: CME-X4 V4 Research Expected vs Live Observed Comparison
        if self.path.startswith("/api/cme-x4/shadow/comparison"):
            from backend_lib.cme_x4_shadow_db import shadow_db
            live_perf = shadow_db.get_shadow_performance()
            comp = {
                "research_expected": {
                    "win_rate": 74.0,
                    "net_ev_r": 0.167,
                    "profit_factor": 1.75,
                    "slippage_bps": 4.0,
                    "latency_ms": 100,
                    "limit_fill_rate_pct": 28.5,
                    "veto_rejection_rate_pct": 80.0,
                    "reversal_win_rate": 71.4,
                    "reversal_ev_r": 0.993,
                    "harvest_win_rate": 77.4
                },
                "live_observed": live_perf
            }
            self._send_json(200, comp)
            return

        # 3e-4. API: CME-X4 V4 Real-Time Live Trade Simulator
        if self.path.startswith("/api/cme-x4/live-simulation"):
            sim_file = os.path.join(DIRECTORY, "scratch", "cme_x4_live_simulation.json")
            if os.path.exists(sim_file):
                try:
                    with open(sim_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json(200, data)
                    return
                except Exception as e:
                    self._send_json(500, {"error": f"Failed reading live simulation state: {e}"})
                    return
            else:
                self._send_json(200, {
                    "status": "INITIALIZING",
                    "account_config": {
                        "initial_capital_usd": 10.0,
                        "current_equity_usd": 10.0,
                        "leverage": "10.0x (Isolated)",
                        "margin_per_trade_usd": 1.0,
                        "notional_per_trade_usd": 10.0
                    },
                    "active_trades": [],
                    "recent_closed_trades": []
                })
                return

        # 4. API: DIV-10 Benzinga News Sentinel feed
        if self.path.startswith("/api/news"):
            res = get_news_state()
            self._send_json(200, res)
            return

        # 5. API: model-spend accounting — what was called, what was refused,
        # and what was served from cache instead of being paid for again.
        if self.path.startswith("/api/llm/status"):
            self._send_json(200, llm_budget_status())
            return

        # 6. API: Macro Regime feed (CoinGecko BTC dominance & total market cap)
        if self.path.startswith("/api/market-overview"):
            res = get_market_overview_state()
            self._send_json(200, res)
            return

        # 6-btc. API: Real-time BTC Macro Regime & Altcoin Sensitivity Guard
        if self.path.startswith("/api/market/btc-macro"):
            try:
                from daemons.btc_macro_monitor import fetch_btc_macro
                macro = fetch_btc_macro()
                self._send_json(200, macro or {})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # 6-state. API: Live market scanner state snapshot
        if self.path.startswith("/api/market/live-state"):
            state_f = os.path.join(DIRECTORY, "scratch", "live_market_state.json")
            if os.path.exists(state_f):
                try:
                    with open(state_f, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json(200, data)
                    return
                except Exception:
                    pass
            self._send_json(200, {})
            return

        # 6a. API: Kline / Candlestick feed proxy with caching
        if self.path.startswith("/api/kline"):
            parsed_url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed_url.query)
            symbol = (q.get("symbol") or ["BTCUSDT"])[0].upper()
            interval = (q.get("interval") or ["5"])[0]
            end_time = (q.get("end") or [None])[0]
            try:
                limit = min(200, max(10, int((q.get("limit") or ["60"])[0])))
            except Exception:
                limit = 60
            candles = get_cached_klines(symbol, interval, limit, end_time)
            self._send_json(200, {"retCode": 0, "symbol": symbol, "interval": interval, "list": candles})
            return

        # 6b. API: Trades and suggestions record (matches Vercel api/trades/record)
        if self.path.startswith("/api/trades/record"):
            parsed_url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed_url.query)
            action = (q.get("_action") or [""])[0]

            if action == "signals":
                try:
                    signal_log_mod.settle_pending(max_rows=4)
                except Exception as e:
                    print(f"[signals] settle_pending failed: {e}")
                self._send_json(200, signal_log_mod.page(
                    page_num=(q.get("page") or ["1"])[0],
                    limit=(q.get("limit") or ["25"])[0],
                    symbol=(q.get("symbol") or [None])[0],
                    outcome=(q.get("outcome") or [None])[0],
                ))
                return

            if action == "trades":
                with trade_stats_lock:
                    history = list(trade_stats["trade_history"])
                try:
                    limit = max(1, min(int((q.get("limit") or ["25"])[0]), 100))
                    page_num = max(1, int((q.get("page") or ["1"])[0]))
                except (TypeError, ValueError):
                    limit, page_num = 25, 1
                start = (page_num - 1) * limit
                self._send_json(200, {
                    "items": history[start:start + limit],
                    "page": page_num, "limit": limit, "total": len(history),
                    "pages": max(1, (len(history) + limit - 1) // limit),
                })
                return

            with trade_stats_lock:
                history = list(trade_stats["trade_history"])
            self._send_json(200, {
                "kv_configured": True,
                "trade_count": len(history),
                "signal_count": len(signal_log_mod.load().get("signals", [])),
                "most_recent": history[0] if history else None
            })
            return

        # 7. API: Autonomous execution settings & arm state
        if self.path == "/api/auto-trade/state":
            try:
                from backend_lib import auto_trade_state
                self._send_json(200, auto_trade_state.load())
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # Default: Serve static files
        super().do_GET()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            body = json.loads(post_data.decode("utf-8"))
        except Exception:
            body = {}

        # Every route below is expected to always call self._send_json(...)
        # and return. If any of them raises instead (a bad symbol, a None
        # where .capitalize() is called, anything unanticipated), the
        # exception must still end in a JSON response -- an uncaught
        # exception here otherwise closes the connection with no body (or,
        # depending on the client, a generic non-JSON error page), which
        # surfaces client-side as a cryptic "JSON.parse: unexpected
        # character..." instead of a message that says what actually failed.
        try:
            return self._route_post(body)
        except Exception as e:
            print(f"[POST {self.path}] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

    def _route_post(self, body):
        # 0. API: Configure Agent Target Mode
        if self.path == "/api/agent/target-mode":
            from backend_lib.market_knowledge import kb
            target_eq = body.get("target_equity")
            is_armed = body.get("is_armed")
            status = body.get("status")
            strategy_mode = body.get("strategy_mode")
            time_horizon_hours = body.get("time_horizon_hours")
            start_equity = body.get("start_equity")
            kb.set_target_state(
                target_equity=target_eq,
                is_armed=is_armed,
                status=status,
                strategy_mode=strategy_mode,
                time_horizon_hours=time_horizon_hours,
                start_equity=start_equity
            )
            wb = bybit_client.get_wallet_balance()
            coins = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
            usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
            eq = float(usdt.get("equity", 0)) if usdt else None
            self._send_json(200, kb.get_target_state(current_equity=eq))
            return

        # 1. API: Place Demo Order
        if self.path == "/api/order/place":
            category = body.get("category", "linear")
            symbol = body.get("symbol", "BTCUSDT")
            side = body.get("side", "Buy")
            order_type = body.get("orderType", "Market")
            qty = body.get("qty", 0.001)
            price = body.get("price")
            tp = body.get("takeProfit")
            sl = body.get("stopLoss")
            leverage = body.get("leverage")
            margin_mode = body.get("marginMode")

            # ── Same-coin duplicate guard ──
            # Prevent opening a second position or limit order on the same coin
            try:
                pos_res = bybit_client.get_positions(symbol)
                positions = pos_res.get("result", {}).get("list", [])
                live = [p for p in positions if float(p.get("size", 0)) > 0]
                if live:
                    self._send_json(200, {
                        "retCode": -1,
                        "retMsg": f"Already holding a live {symbol} position — duplicate blocked"
                    })
                    return

                orders_res = bybit_client.get_open_orders(symbol)
                pending = orders_res.get("result", {}).get("list", [])
                if pending:
                    self._send_json(200, {
                        "retCode": -1,
                        "retMsg": f"A limit order for {symbol} is already resting — duplicate blocked"
                    })
                    return
            except Exception as e:
                print(f"[duplicate-guard] Check failed (proceeding): {e}")

            # ── Apply leverage and margin mode before placing the order ──
            if margin_mode:
                try:
                    mm_res = bybit_client.switch_margin_mode(symbol, margin_mode, leverage or 10)
                    rc = mm_res.get("retCode", 0)
                    # 110026 = 'Position mode is not modified' — already set, harmless
                    if rc not in (0, 110026):
                        print(f"[margin-mode] {symbol} switch to {margin_mode}: retCode={rc} {mm_res.get('retMsg','')}")
                except Exception as e:
                    print(f"[margin-mode] {symbol} switch failed (proceeding): {e}")

            if leverage:
                try:
                    lev_res = bybit_client.set_leverage(symbol, leverage)
                    rc = lev_res.get("retCode", 0)
                    # 110043 = 'Set leverage not modified' — already set, harmless
                    if rc not in (0, 110043):
                        print(f"[leverage] {symbol} set to {leverage}x: retCode={rc} {lev_res.get('retMsg','')}")
                except Exception as e:
                    print(f"[leverage] {symbol} set failed (proceeding): {e}")

            res = bybit_client.place_order(category, symbol, side, order_type, qty, price, tp, sl)
            self._send_json(200, res)
            return


        # 2. API: Close Position
        if self.path == "/api/order/close":
            category = body.get("category", "linear")
            symbol = body.get("symbol", "BTCUSDT")
            side = body.get("side", "Buy")
            qty = body.get("qty", 0.001)

            res = bybit_client.close_position(category, symbol, side, qty)
            self._send_json(200, res)
            return

        # 3. API: move the broker-side stop (break-even ratchet / trailing).
        if self.path == "/api/position/stop":
            res = bybit_client.set_trading_stop(
                body.get("category", "linear"),
                body.get("symbol", "BTCUSDT"),
                stop_loss=body.get("stopLoss"),
                take_profit=body.get("takeProfit"),
                position_idx=body.get("positionIdx", 0),
            )
            self._send_json(200, res)
            return

        # 4. API: supervisor verdict on ONE pre-screened candidate.
        # This is the only model call the trading path makes, and it is gated
        # client-side by agents/deepseek-governor.js before it ever gets here.
        if self.path == "/api/supervisor/verdict":
            res = run_supervisor_verdict(body)
            self._send_json(200, res)
            return

        # Legacy prose-synthesis route, kept so an older cached frontend cannot
        # 404, but it no longer calls the model — it returns the local
        # synthesis only. The prose was never read by any code path.
        if self.path == "/api/deepseek/analyze":
            self._send_json(200, {
                "success": True,
                "is_fallback": True,
                "fallback_reason": "Prose synthesis retired in V3. Use POST /api/supervisor/verdict, which returns a structured verdict the engine actually consumes.",
                "model": "MASIS local",
                "analysis": "The free-text synthesis endpoint has been retired. It generated ~700 tokens per call on a timer, no code parsed the result, and it could not change any decision. See /api/llm/status for current model spend.",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            })
            return

        # 3b-reset. API: Reset Performance Scorecard (Preserves 100% of all trade history data)
        if self.path.startswith("/api/performance/reset"):
            with trade_stats_lock:
                if hasattr(bybit_client, "sync_time"):
                    bybit_client.sync_time()
                bybit_server_ms = int(time.time() * 1000) + getattr(bybit_client, 'time_offset', 0)
                latest_closed_ts = 0
                if _closed_pnl_cache.get("trades"):
                    for t in _closed_pnl_cache["trades"][:10]:
                        t_ts = int(t.get("updatedTime") or t.get("createdTime") or 0)
                        if t_ts > latest_closed_ts:
                            latest_closed_ts = t_ts
                now_anchor = max(bybit_server_ms, latest_closed_ts + 1000)
                trade_stats["reset_anchor_time"] = now_anchor
                trade_stats["win_count"] = 0
                trade_stats["loss_count"] = 0
                trade_stats["gross_profit"] = 0.0
                trade_stats["gross_loss"] = 0.0
                save_trade_stats()
            _closed_pnl_cache["timestamp"] = 0
            _closed_pnl_cache["trades"] = []
            self._send_json(200, {
                "success": True,
                "reset_anchor_time": now_anchor,
                "message": "Performance metrics reset to 0 (all trade history data preserved)."
            })
            return

        # 4. API: record the reasoning behind a close or candidate signal.
        if self.path.startswith("/api/trades/record"):
            parsed_url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed_url.query)
            action = (q.get("_action") or [""])[0]

            if action == "signal_resolve":
                try:
                    self._send_json(200, {"success": True, "row": signal_log_mod.resolve(body)})
                except Exception as e:
                    print(f"[POST /api/trades/record?_action=signal_resolve] error: {e}")
                    self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})
                return

            if action == "signal":
                try:
                    self._send_json(200, {"success": True, "row": signal_log_mod.record(body)})
                except Exception as e:
                    print(f"[POST /api/trades/record?_action=signal] error: {e}")
                    self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})
                return

            try:
                pnl = float(body.get("pnl", 0))
            except (TypeError, ValueError):
                pnl = 0.0
            with trade_stats_lock:
                trade_stats["trade_history"].insert(0, {
                    "id": f"TRD-{int(time.time()) % 100000}",
                    "recorded_at": int(time.time() * 1000),
                    "time": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
                    "symbol": body.get("symbol", ""),
                    "side": str(body.get("side", "")).upper(),
                    "entry": float(body.get("entry", 0) or 0),
                    "exit": float(body.get("exit", 0) or 0),
                    "stop": float(body.get("stop", 0) or body.get("stopLoss", 0) or 0),
                    "targets": body.get("targets") or ([] if not body.get("target") else [body.get("target")]),
                    "nextSupport": body.get("nextSupport"),
                    "nextResistance": body.get("nextResistance"),
                    "isScalp": bool(body.get("isScalp")),
                    "pnl": pnl,
                    "status": "WIN" if pnl > 0 else "LOSS",
                    "setup_type": body.get("setup_type", ""),
                    "grade": body.get("grade", ""),
                    "score": body.get("score"),
                    "r_multiple": body.get("r_multiple"),
                    "exit_reason": body.get("exit_reason", ""),
                    "reason": body.get("reason", "")
                })
                if len(trade_stats["trade_history"]) > 200:
                    trade_stats["trade_history"].pop()
                save_trade_stats()
            try:
                signal_log_mod.record_trade_outcome(body)
            except Exception as e:
                print(f"[trades/record] signal_log update failed: {e}")
            self._send_json(200, {"success": True})
            return

        # 5. API: Autonomous execution settings & arm state
        if self.path == "/api/auto-trade/state":
            try:
                from backend_lib import auto_trade_state
                state = auto_trade_state.save(
                    body.get("armed"),
                    body.get("riskPerTradePct"),
                    body.get("sizingMode"),
                    body.get("fixedUsdtSize"),
                    body.get("leverage"),
                    body.get("marginMode"),
                    theses=body.get("theses"),
                    daily_gross_target=body.get("dailyGrossTarget"),
                    target_notional=body.get("targetNotional"),
                    virtual_equity=body.get("virtualEquity"),
                    max_concurrent_positions=body.get("maxConcurrentPositions"),
                )
                self._send_json(200, state)
            except Exception as e:
                self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})
            return

        # 6. API: Trigger Historical Archaeology Scan on demand
        if self.path.startswith("/api/agent/archaeologist/scan"):
            try:
                from daemons.historical_pattern_archaeologist import archaeologist
                res = archaeologist.run_archaeology_cycle()
                self._send_json(200, {"success": True, "result": res})
            except Exception as e:
                self._send_json(500, {"success": False, "error": str(e)})
            return

        self._send_json(404, {"error": "Not Found"})

    def log_message(self, format, *args):
        # Quiet down routine polling noise; still surface it via print for errors elsewhere.
        pass


class ThreadingDashboardServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server():
    with ThreadingDashboardServer(("", PORT), DashboardHandler) as httpd:
        print("=" * 64)
        print("  MASIS V3 — Multi-Timeframe Confluence Engine & Demo Trading Server")
        print(f"  Dashboard:        http://localhost:{PORT}")
        print(f"  Bybit endpoint:   {BYBIT_BASE_URL}")
        print(f"  Supervisor model: {'configured, budget ' + str(DEEPSEEK_DAILY_CALL_BUDGET) + '/day' if DEEPSEEK_API_KEY else 'NOT configured — running on local gates only'}")
        print(f"  News sentinel:    {'Benzinga live' if BENZINGA_API_KEY else 'disabled (no BENZINGA_API_KEY)'}")
        print(f"  Macro feed:       {'CoinGecko live' if COINGECKO_API_KEY else 'disabled (no COINGECKO_API_KEY)'}")
        print("=" * 64)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")


if __name__ == "__main__":
    run_server()

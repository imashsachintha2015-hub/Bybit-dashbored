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

PORT = 8080
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
DEEPSEEK_DAILY_CALL_BUDGET = int(_env("DEEPSEEK_DAILY_CALL_BUDGET", "120"))

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
        if llm_budget["calls"] >= DEEPSEEK_DAILY_CALL_BUDGET:
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
SUPERVISOR_SYSTEM = (
    "You are a risk supervisor reviewing ONE pre-screened trade candidate from a "
    "quantitative crypto system. The technical work is already done and is not "
    "yours to redo. Your job is narrow: identify a reason this specific trade "
    "should NOT be taken right now that the local gates could have missed — a "
    "known event, a contradiction between the stated evidence, or an obviously "
    "poor location. Default to CONFIRM. Reserve VETO for a concrete, nameable "
    "problem. Reply with JSON only, no prose, no code fences."
)


def run_supervisor_verdict(payload):
    """Returns {verdict, confidence, rationale, is_fallback, ...}."""
    symbol = payload.get("symbol", "BTCUSDT")
    direction = payload.get("direction", "LONG")
    setup = payload.get("setup", "UNKNOWN")
    score = payload.get("score", 0)
    grade = payload.get("grade", "B")
    regime = payload.get("regime", "UNKNOWN")
    bias = payload.get("bias", "NEUTRAL")
    entry = payload.get("entry")
    stop = payload.get("stop")
    targets = payload.get("targets", [])
    rr = payload.get("riskReward")
    evidence = payload.get("evidence", [])

    news_state = get_news_state()
    macro_state = get_market_overview_state()

    if not DEEPSEEK_API_KEY:
        return {
            "verdict": "CONFIRM", "confidence": 0, "is_fallback": True,
            "fallback_reason": "No DEEPSEEK_API_KEY configured — running on local gates only, which is a supported mode",
            "rationale": "No supervisor model configured; the local gate result stands unmodified.",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

    if not llm_budget_take("supervisor-verdict"):
        return {
            "verdict": "CONFIRM", "confidence": 0, "is_fallback": True,
            "fallback_reason": f"Daily model budget spent ({DEEPSEEK_DAILY_CALL_BUDGET} calls) — local gates stand on their own",
            "rationale": "Budget exhausted; the local gate result stands unmodified. This is the designed fallback, not a degradation.",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

    evidence_lines = "\n".join(f"- {e}" for e in evidence[:8]) or "- (none supplied)"
    prompt = f"""Candidate: {direction} {symbol}
Setup: {setup} | local quality score {score}/100 (grade {grade})
Regime: {regime}, higher-timeframe bias {bias}
Entry {entry} | stop {stop} | targets {targets} | reward:risk to TP2 {rr}

Local evidence:
{evidence_lines}

Context:
- News sentiment: {news_state.get('sentiment_label')} ({news_state.get('sentiment_score')}); latest: "{news_state.get('headline')}"
- Macro: BTC dominance {macro_state.get('btc_dominance')}%, total market cap 24h {macro_state.get('market_cap_change_24h_pct')}%, risk-off: {macro_state.get('risk_off')}

Reply with exactly this JSON:
{{"verdict":"CONFIRM|DOWNGRADE|VETO","confidence":0-100,"rationale":"one sentence, max 30 words","risk":"the single biggest risk to this trade, max 15 words"}}"""

    try:
        body = json.dumps({
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": SUPERVISOR_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 150,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        req = urllib.request.Request(DEEPSEEK_URL, data=body, headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "MASIS/3.0",
        })
        with urllib.request.urlopen(req, timeout=12) as r:
            res = json.loads(r.read().decode())
            text = res["choices"][0]["message"]["content"]
            match = re.search(r"\{.*\}", text, re.DOTALL)
            parsed = json.loads(match.group(0) if match else text)
            verdict = str(parsed.get("verdict", "CONFIRM")).upper()
            if verdict not in ("CONFIRM", "DOWNGRADE", "VETO"):
                verdict = "CONFIRM"
            usage = res.get("usage", {})
            return {
                "verdict": verdict,
                "confidence": int(parsed.get("confidence", 50)),
                "rationale": str(parsed.get("rationale", ""))[:240],
                "risk": str(parsed.get("risk", ""))[:160],
                "is_fallback": False,
                "model": DEEPSEEK_MODEL,
                "tokens": usage.get("total_tokens"),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            }
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:200]
        reason = ("DeepSeek HTTP 402 — account has no credit balance."
                  if e.code == 402 else f"DeepSeek HTTP {e.code}: {detail}")
    except Exception as e:
        reason = f"DeepSeek unreachable: {e}"

    # A supervisor that cannot be reached must never block a locally-valid
    # trade, and must never wave through a locally-invalid one. It abstains.
    return {
        "verdict": "CONFIRM", "confidence": 0, "is_fallback": True,
        "fallback_reason": reason,
        "rationale": "Supervisor unavailable; abstaining. The local gate result stands unmodified.",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }


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
        # setup produced the trade and why it was exited. They are matched to
        # Bybit's rows by symbol, side and time rather than added to them.
        if self.path == "/api/performance":
            bybit_pnl = bybit_client.get_closed_pnl(limit=100)
            closed_list = bybit_pnl.get("result", {}).get("list", []) or []

            with trade_stats_lock:
                local_history = list(trade_stats["trade_history"])

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

            w_count = l_count = 0
            g_profit = g_loss = 0.0
            merged = []
            for row in closed_list:
                try:
                    pnl = float(row.get("closedPnl", 0))
                except (TypeError, ValueError):
                    continue
                if pnl > 0:
                    w_count += 1
                    g_profit += pnl
                elif pnl < 0:
                    l_count += 1
                    g_loss += abs(pnl)
                extra = annotate(row)
                merged.append({
                    "id": row.get("orderId", "")[-8:] or "--",
                    "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(
                        int(row.get("updatedTime") or row.get("createdTime") or 0) / 1000)),
                    "symbol": row.get("symbol"),
                    "side": str(row.get("side", "")).upper(),
                    "entry": float(row.get("avgEntryPrice") or 0),
                    "exit": float(row.get("avgExitPrice") or 0),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(float(row.get("closedPnl", 0)) / max(float(row.get("cumEntryValue") or 1), 1e-9) * 100, 3),
                    "status": "WIN" if pnl > 0 else "LOSS",
                    "setup_type": extra.get("setup_type", ""),
                    "grade": extra.get("grade", ""),
                    "r_multiple": extra.get("r_multiple"),
                    "exit_reason": extra.get("exit_reason", ""),
                    "reason": extra.get("reason", "")
                })

            tot_trades = w_count + l_count
            win_rate = round((w_count / tot_trades) * 100, 1) if tot_trades > 0 else 0.0
            profit_factor = round(g_profit / g_loss, 2) if g_loss > 0 else (0.0 if g_profit == 0 else 99.9)

            # Expectancy in R is the figure that says whether the system makes
            # money. A win rate without the average win and loss beside it says
            # nothing: the old build's 1.1R target against a 1.0R stop needed
            # about 48% winners just to break even before fees.
            r_values = [m["r_multiple"] for m in merged if isinstance(m.get("r_multiple"), (int, float))]
            r_wins = [r for r in r_values if r > 0]
            r_losses = [abs(r) for r in r_values if r <= 0]
            expectancy_r = round(sum(r_values) / len(r_values), 3) if r_values else None

            res = {
                "win_count": w_count,
                "loss_count": l_count,
                "total_trades": tot_trades,
                "win_rate": win_rate,
                "gross_profit": round(g_profit, 2),
                "gross_loss": round(g_loss, 2),
                "net_pnl": round(g_profit - g_loss, 2),
                "profit_factor": profit_factor,
                "expectancy_r": expectancy_r,
                "avg_win_r": round(sum(r_wins) / len(r_wins), 2) if r_wins else None,
                "avg_loss_r": round(sum(r_losses) / len(r_losses), 2) if r_losses else None,
                "r_sample_size": len(r_values),
                "trade_history": merged,
                "accounting_note": "Bybit closed-PnL is the single source of truth for money; local records supply setup and exit-reason metadata only."
            }
            self._send_json(200, res)
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

        # 4. API: record the reasoning behind a close.
        #
        # This no longer maintains its own win/loss/profit tallies — those came
        # from here AND from Bybit, which is what double-counted every trade.
        # What it stores is the part Bybit has no way to know: which setup fired,
        # what grade it scored, why the position was exited, and the realised R.
        if self.path == "/api/trades/record":
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
            self._send_json(200, {"success": True})
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

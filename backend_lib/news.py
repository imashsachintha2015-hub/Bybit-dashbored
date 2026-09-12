"""DIV-10 Benzinga News Sentinel -- headline fetch + sentiment scoring.

Ported from server.py. news_cache and headline_scores are process-local
caches with their own TTL/immutability logic already; on a serverless
instance that just means a cold start re-fetches or re-scores a bit sooner
than a long-lived server would -- a cost/latency detail, not a correctness
one, unlike trade_stats.py and llm_budget.py, which need to survive across
invocations to be correct and so go through kv.py instead.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import llm_budget

BENZINGA_API_KEY = os.environ.get("BENZINGA_API_KEY")
BENZINGA_NEWS_URL = "https://api.benzinga.com/api/v2/news"
NEWS_CACHE_TTL = int(os.environ.get("NEWS_CACHE_TTL", "300"))
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions")

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

# Persistent-within-process store of headline -> (score, impact). Headlines
# are immutable once published, so a score only ever needs computing once.
headline_scores = {}


def keyword_sentiment(title):
    t = title.lower()
    for kw in NEWS_NEGATIVE_KEYWORDS:
        if kw in t:
            return -0.6, "HIGH" if kw in ("hack", "hacked", "exploit", "sec charges", "fraud") else "MEDIUM"
    for kw in NEWS_POSITIVE_KEYWORDS:
        if kw in t:
            return 0.5, "MEDIUM"
    return 0.0, "LOW"


def score_headlines_with_deepseek(headlines):
    """Scores only headlines not already scored. Returns {index: (score, impact)}."""
    if not headlines or not DEEPSEEK_API_KEY:
        return None

    unscored = [(i, h) for i, h in enumerate(headlines) if h not in headline_scores]
    cached = {i: headline_scores[h] for i, h in enumerate(headlines) if h in headline_scores}

    if not unscored:
        return cached  # nothing new in the feed — no call at all

    if not llm_budget.take("news-scoring"):
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
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json", "User-Agent": "MASIS/3.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            res = json.loads(r.read().decode())
            text = res["choices"][0]["message"]["content"]
            match = re.search(r"\[.*\]", text, re.DOTALL)
            arr = json.loads(match.group(0) if match else text)
            out = dict(cached)
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
        req = urllib.request.Request(url, headers={"Accept": "application/xml", "User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = parse_benzinga_xml(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[Benzinga] Fetch failed, serving last-known cache: {e}")
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

    news_cache["articles"] = articles
    news_cache["sentiment_score"] = agg_score
    news_cache["sentiment_label"] = label
    news_cache["headline"] = articles[0]["title"] if articles else "No major catalysts detected"
    news_cache["updated_at"] = time.time()
    news_cache["is_fallback"] = scored is None
    news_cache["fallback_reason"] = "" if scored is not None else "DeepSeek scoring unavailable; used keyword heuristic"


def get_news_state(force=False):
    stale = (time.time() - news_cache["updated_at"]) > NEWS_CACHE_TTL
    if stale or force:
        fetch_benzinga_news()
    return dict(news_cache)

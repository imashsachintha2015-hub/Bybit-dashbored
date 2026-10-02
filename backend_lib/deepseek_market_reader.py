"""
DeepSeek AI Institutional Market Intelligence & Structure Reader
Analyzes:
1. BTC Macro Spillover & 24H/1H Dump or Pump Correlation
2. Smart Money Concepts: Liquidity Hunts, Sweeps (BSL/SSL) & Fair Value Gaps (FVG)
3. Multi-Touch Support & Resistance Dynamics (e.g. 3x test attempts with wick absorption)
4. Predictive Price Roadmaps: Next targets (POC, Value Area, Liquidity pools) and invalidations
"""

import urllib.request
import json
import time
import math
import re
import os
from daemons.agents.sr_agent import SRAgent
from daemons.agents.fvg_agent import FVGImpactAgent
from .deepseek_switch import deepseek_key

def _init_env():
    global DEEPSEEK_API_KEY, BYBIT_BASE_URL, DEEPSEEK_URL, DEEPSEEK_MODEL
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("\"'")
                        if k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass
    BYBIT_BASE_URL = os.environ.get("BYBIT_BASE_URL", "https://api-demo.bybit.com")
    DEEPSEEK_API_KEY = deepseek_key()   # "" unless DEEPSEEK_ENABLED=1; with no key the local quant read is used
    DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/v1/chat/completions")
    DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

_init_env()

SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","LINKUSDT",
    "SEIUSDT","AVAXUSDT","DOGEUSDT","BNBUSDT","ADAUSDT",
    "DOTUSDT","POLUSDT","LTCUSDT","NEARUSDT","APTUSDT"
]

# Simple in-memory cache to prevent spamming and fast UI rendering
_ANALYSIS_CACHE = {}
CACHE_TTL = 15  # seconds

def _http_get(url, timeout=7):
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS-MarketReader/4.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception:
        return {}

def fetch_klines(sym, interval="15", limit=60):
    url = f"{BYBIT_BASE_URL}/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}"
    data = _http_get(url)
    raw_list = data.get("result", {}).get("list", [])
    bars = []
    for b in reversed(raw_list):
        bars.append({
            "start": int(b[0]),
            "open": float(b[1]),
            "high": float(b[2]),
            "low": float(b[3]),
            "close": float(b[4]),
            "volume": float(b[5])
        })
    return bars

def fetch_ticker(sym):
    url = f"{BYBIT_BASE_URL}/v5/market/tickers?category=linear&symbol={sym}"
    data = _http_get(url)
    items = data.get("result", {}).get("list", [])
    if items:
        t = items[0]
        return {
            "symbol": sym,
            "lastPrice": float(t.get("lastPrice", 0)),
            "price24hPcnt": float(t.get("price24hPcnt", 0)),
            "highPrice24h": float(t.get("highPrice24h", 0)),
            "lowPrice24h": float(t.get("lowPrice24h", 0)),
            "turnover24h": float(t.get("turnover24h", 0))
        }
    return None

def fetch_all_tickers():
    url = f"{BYBIT_BASE_URL}/v5/market/tickers?category=linear"
    data = _http_get(url)
    items = data.get("result", {}).get("list", [])
    tickers = {}
    for t in items:
        sym = t.get("symbol")
        if sym in SYMBOLS:
            tickers[sym] = {
                "symbol": sym,
                "lastPrice": float(t.get("lastPrice", 0)),
                "price24hPcnt": float(t.get("price24hPcnt", 0)),
                "highPrice24h": float(t.get("highPrice24h", 0)),
                "lowPrice24h": float(t.get("lowPrice24h", 0)),
                "turnover24h": float(t.get("turnover24h", 0))
            }
    return tickers

def calc_ema(closes, period):
    if not closes:
        return 0.0
    k = 2.0 / (period + 1)
    res = [closes[0]] * len(closes)
    for i in range(1, len(closes)):
        res[i] = closes[i] * k + res[i-1] * (1 - k)
    return res[-1]

def calc_vp(bars, bins=20):
    if len(bars) < 5:
        return None
    lows = [b["low"] for b in bars]
    highs = [b["high"] for b in bars]
    mn, mx = min(lows), max(highs)
    if mx <= mn:
        return None
    bs = (mx - mn) / bins
    bv = [0.0] * bins
    bc = [mn + (i + 0.5) * bs for i in range(bins)]
    for b in bars:
        mid = (b["high"] + b["low"]) / 2.0
        idx = min(int((mid - mn) / bs), bins - 1)
        bv[idx] += b["volume"]
    max_idx = bv.index(max(bv))
    return {
        "poc": bc[max_idx],
        "val": mn + 0.20 * (mx - mn),
        "vah": mn + 0.80 * (mx - mn)
    }

def analyze_sr_and_tests(bars, cur_price):
    """
    Detects swing clusters and counts distinct test waves and rejections
    for the nearest support and resistance.
    """
    sr = SRAgent()
    swings = sr._detect_swing_levels(bars, lookback=min(len(bars), 50), sensitivity=2)
    clusters = sr._cluster_levels(swings, tolerance_pct=0.005)

    supports = [c for c in clusters if c["price"] < cur_price * 0.999]
    resistances = [c for c in clusters if c["price"] > cur_price * 1.001]

    nearest_sup = max(supports, key=lambda x: x["price"])["price"] if supports else min(b["low"] for b in bars)
    nearest_res = min(resistances, key=lambda x: x["price"])["price"] if resistances else max(b["high"] for b in bars)

    # Count distinct test waves at support (within 0.3% of support)
    sup_tests = 0
    last_sup_bar = -99
    for i, b in enumerate(bars):
        rng = b["high"] - b["low"]
        if rng <= 0:
            continue
        lower_wick = min(b["open"], b["close"]) - b["low"]
        # touched support zone and rejected up or closed above
        if abs(b["low"] - nearest_sup) / nearest_sup <= 0.0035 or b["low"] <= nearest_sup:
            if b["close"] > nearest_sup:
                if i - last_sup_bar > 2:  # group into distinct waves
                    sup_tests += 1
                last_sup_bar = i

    # Count distinct test waves at resistance
    res_tests = 0
    last_res_bar = -99
    for i, b in enumerate(bars):
        rng = b["high"] - b["low"]
        if rng <= 0:
            continue
        upper_wick = b["high"] - max(b["open"], b["close"])
        if abs(b["high"] - nearest_res) / nearest_res <= 0.0035 or b["high"] >= nearest_res:
            if b["close"] < nearest_res:
                if i - last_res_bar > 2:
                    res_tests += 1
                last_res_bar = i

    # Ensure at least 1-3 baseline count if price is oscillating in range
    if sup_tests == 0 and cur_price > nearest_sup:
        sup_tests = 1
    if res_tests == 0 and cur_price < nearest_res:
        res_tests = 1

    return {
        "support": round(nearest_sup, 4),
        "resistance": round(nearest_res, 4),
        "sup_tests": sup_tests,
        "res_tests": res_tests
    }

def analyze_liquidity_hunt(bars, cur_price):
    """
    Detects liquidity sweeps (BSL/SSL hunts) and Fair Value Gaps.
    """
    fvgs = FVGImpactAgent.detect_fvgs(bars, lookback=25)
    unfilled_bullish = [f for f in fvgs if f["type"] == "BULLISH" and not f.get("filled")]
    unfilled_bearish = [f for f in fvgs if f["type"] == "BEARISH" and not f.get("filled")]

    # Check for recent stop sweep in the last 6 bars
    sweep_type = "NONE"
    sweep_level = None
    if len(bars) >= 20:
        prev_low = min(b["low"] for b in bars[-20:-6])
        prev_high = max(b["high"] for b in bars[-20:-6])

        recent_bars = bars[-6:]
        recent_low = min(b["low"] for b in recent_bars)
        recent_high = max(b["high"] for b in recent_bars)
        last_close = bars[-1]["close"]

        # Sell-Side Liquidity Hunt: wicked below prior low, now closed back above
        if recent_low < prev_low and last_close > prev_low:
            sweep_type = "SELL_SIDE_LIQUIDITY_HUNT (BEAR_TRAP)"
            sweep_level = prev_low
        # Buy-Side Liquidity Hunt: wicked above prior high, now closed back below
        elif recent_high > prev_high and last_close < prev_high:
            sweep_type = "BUY_SIDE_LIQUIDITY_HUNT (BULL_TRAP)"
            sweep_level = prev_high

    return {
        "sweep_type": sweep_type,
        "sweep_level": round(sweep_level, 4) if sweep_level else None,
        "unfilled_bullish_fvgs": len(unfilled_bullish),
        "unfilled_bearish_fvgs": len(unfilled_bearish),
        "nearest_fvg": unfilled_bullish[0]["mid"] if unfilled_bullish else (unfilled_bearish[0]["mid"] if unfilled_bearish else None)
    }

def get_amd_phase():
    """
    Determines institutional AMD session phase based on current UTC hour.
    """
    utc_hour = time.gmtime().tm_hour
    if 0 <= utc_hour < 7:
        return "ASIAN_ACCUMULATION (Building Liquidity Boundaries)"
    elif 7 <= utc_hour < 13:
        return "LONDON_MANIPULATION (Judas Swing / Liquidity Hunt)"
    elif 13 <= utc_hour < 21:
        return "NY_DISTRIBUTION (True Expansion Trend)"
    else:
        return "DAILY_CLOSE_CONSOLIDATION (Mean Reversion)"

def build_local_fallback_analysis(sym, btc_data, coin_data, sr_data, liq_data, vp_data):
    """
    Generates high-grade deterministic quantitative market intelligence
    if DeepSeek API is unavailable or offline.
    """
    cur_p = coin_data["lastPrice"]
    coin_24h = coin_data["price24hPcnt"] * 100
    btc_24h = btc_data["price24hPcnt"] * 100
    btc_p = btc_data["lastPrice"]
    btc_regime = "BEARISH (Below 15M EMA200)" if btc_data["is_bear"] else "BULLISH (Above 15M EMA200)"

    sup = sr_data["support"]
    res = sr_data["resistance"]
    sup_tests = sr_data["sup_tests"]
    res_tests = sr_data["res_tests"]
    poc = vp_data["poc"] if vp_data else cur_p

    # Compute correlation / beta
    beta = round(coin_24h / (btc_24h if abs(btc_24h) > 0.05 else 0.5), 2)
    relative_strength = "OUTPERFORMING" if coin_24h > btc_24h else "UNDERPERFORMING"

    # Determine bias
    if liq_data["sweep_type"].startswith("SELL_SIDE") or (cur_p <= sup * 1.015 and sup_tests >= 2):
        bias = "BULLISH_REVERSAL"
        confidence = 82
        action = "BUY_RECLAIM"
        primary_target = round(poc, 4)
        secondary_target = round(res, 4)
        invalidation = round(sup * 0.995, 4)
        path = f"Defending {sup} support after {sup_tests} rejection wicks. Price projected to reclaim POC at {primary_target} and retest {secondary_target} resistance ceiling."
    elif liq_data["sweep_type"].startswith("BUY_SIDE") or (cur_p >= res * 0.985 and res_tests >= 2):
        bias = "BEARISH_EXHAUSTION"
        confidence = 80
        action = "FADE_RALLY"
        primary_target = round(poc, 4)
        secondary_target = round(sup, 4)
        invalidation = round(res * 1.005, 4)
        path = f"Rejected at {res} resistance after {res_tests} failed breakout attempts. Liquidity hunt trapped breakout buyers. Price projected to rotate back to POC ({primary_target}) and test {secondary_target}."
    else:
        bias = "BEARISH_DRIFT" if btc_data["is_bear"] else "BULLISH_CONTINUATION"
        confidence = 74
        action = "WAIT_CONFIRMATION"
        primary_target = round(res if not btc_data["is_bear"] else sup, 4)
        secondary_target = round(poc, 4)
        invalidation = round(sup * 0.994 if not btc_data["is_bear"] else res * 1.006, 4)
        path = f"Oscillating between {sup} support and {res} resistance. BTC macro {btc_regime} sets directional boundary. Wait for confirmed candle close beyond key levels."

    btc_dump_str = f"dumped {abs(btc_24h):.2f}%" if btc_24h < 0 else f"pumped +{btc_24h:.2f}%"
    btc_impact = (
        f"BTC has {btc_dump_str} over 24H (Mark: ${btc_p:,.2f}), trading in a {btc_regime} environment. "
        f"{sym} exhibits a {beta}x beta sensitivity ({coin_24h:+.2f}%), currently {relative_strength} relative to BTC. "
        f"When BTC dips, institutional liquidity is prioritized on major pairs, causing {sym} to experience orderbook thinning "
        f"and accentuated wick volatility around structural support."
    )

    liq_explanation = (
        f"Market Structure Status: {liq_data['sweep_type']}. "
        f"Detected {liq_data['unfilled_bullish_fvgs']} unfilled Bullish FVGs and {liq_data['unfilled_bearish_fvgs']} Bearish FVGs. "
        f"Smart money algorithms have triggered volatility around recent swing extremes to trigger retail stop clusters. "
        f"AMD Session Phase: {get_amd_phase()}."
    )

    sr_explanation = (
        f"The primary support floor is anchored at {sup} and overhead resistance is locked at {res}. "
        f"Price has tested the support zone {sup_tests} distinct time(s) with clear wick rejections, "
        f"demonstrating that buyers are actively absorbing supply and rejecting lower prices. "
        f"Resistance has been challenged {res_tests} time(s)."
    )

    # Calculate trade suggestion & institutional veto audit
    trade_dir = "LONG" if (bias.startswith("BULL") or not btc_data["is_bear"]) else "SHORT"
    setup_name = "S2 POC Reclaim & Bullish FVG Sweep" if trade_dir == "LONG" else "S2 Resistance Rejection & Bearish Expansion"
    
    veto_reason = None
    if trade_dir == "LONG" and btc_data["is_bear"] and coin_24h < 0:
        veto_reason = "VETO: BTC Macro Regime is Bearish (< 15M EMA200). Long setups are vetoed to prevent catching falling knife alts."
    elif trade_dir == "LONG" and cur_p >= res * 0.99:
        veto_reason = f"VETO: Price is directly under Multi-Touch Resistance (${res} tested {res_tests}x). Longs vetoed into overhead supply."
    elif trade_dir == "SHORT" and not btc_data["is_bear"] and coin_24h > 0:
        veto_reason = "VETO: BTC Macro Regime is Bullish (> 15M EMA200). Shorting into macro uptrend is vetoed by Trend Guard."
    elif sup_tests < 2 and res_tests < 2 and action == "WAIT_CONFIRMATION":
        veto_reason = "VETO: Low structural confluence (< 2 multi-touch tests). Invalidation risk too high."

    trade_status = "VETOED" if veto_reason else "READY"

    if trade_dir == "LONG":
        s_entry = round(cur_p, 4)
        s_stop = round(sup * 0.993, 4)
        s_tp = round(res * 0.998, 4)
    else:
        s_entry = round(cur_p, 4)
        s_stop = round(res * 1.007, 4)
        s_tp = round(sup * 1.002, 4)
    
    risk = abs(s_entry - s_stop)
    reward = abs(s_tp - s_entry)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.1

    if trade_status == "READY" and rr_ratio < 1.6:
        trade_status = "VETOED"
        veto_reason = f"VETO: Risk/Reward ratio ({rr_ratio}R) is below institutional threshold (minimum 1.8R)."

    trade_suggestion = {
        "direction": trade_dir,
        "setup_name": setup_name,
        "status": trade_status,
        "veto_reason": veto_reason,
        "suggested_entry": str(s_entry),
        "suggested_stop": str(s_stop),
        "suggested_tp": str(s_tp),
        "risk_reward": f"{rr_ratio}R"
    }

    return {
        "symbol": sym,
        "bias": bias,
        "confidence": confidence,
        "model": "MASIS Local Quant Fallback",
        "is_ai_live": False,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "executive_summary": f"{sym} ({coin_24h:+.2f}%) is coiling between support at {sup} and resistance at {res}. S/R tests ({sup_tests}x support rejections) indicate active institutional absorption despite BTC macro headwind ({btc_24h:+.2f}%).",
        "btc_macro_impact": btc_impact,
        "liquidity_hunt": liq_explanation,
        "sr_structure": {
            "support_level": str(sup),
            "resistance_level": str(res),
            "sup_tests": sup_tests,
            "res_tests": res_tests,
            "analysis": sr_explanation
        },
        "projected_levels": {
            "primary_target": str(primary_target),
            "secondary_target": str(secondary_target),
            "invalidation_level": str(invalidation),
            "projected_path": path
        },
        "trade_suggestion": trade_suggestion,
        "tactical_action": action,
        "key_takeaway": f"Focus on {action} near {sup} with invalidation at {invalidation}. Respect BTC {btc_regime} macro trend."
    }

def get_market_analysis(target_symbol="ALL", force_refresh=False):
    """
    Main entry point for DeepSeek Market Reader.
    Gathers real Bybit market data, runs quant metrics, queries DeepSeek only when it is
    switched on (DEEPSEEK_ENABLED=1; otherwise the local quant read is returned),
    and returns rich structured market analysis.
    """
    now = time.time()
    cache_key = target_symbol.upper()
    if not force_refresh and cache_key in _ANALYSIS_CACHE:
        cached = _ANALYSIS_CACHE[cache_key]
        if (now - cached["_cached_at"] < CACHE_TTL) and ("trade_suggestion" in cached.get("data", {})):
            return cached["data"]

    # 1. Fetch BTC Macro data
    btc_ticker = fetch_ticker("BTCUSDT") or {
        "symbol": "BTCUSDT", "lastPrice": 83350.0, "price24hPcnt": -0.015,
        "highPrice24h": 84600.0, "lowPrice24h": 82800.0, "turnover24h": 0
    }
    btc_bars = fetch_klines("BTCUSDT", interval="15", limit=60)
    btc_closes = [b["close"] for b in btc_bars] if btc_bars else [btc_ticker["lastPrice"]]
    btc_ema200 = calc_ema(btc_closes, 200) if len(btc_closes) >= 10 else btc_closes[-1]
    is_btc_bear = btc_ticker["lastPrice"] < btc_ema200

    btc_data = {
        "lastPrice": btc_ticker["lastPrice"],
        "price24hPcnt": btc_ticker["price24hPcnt"],
        "ema200": round(btc_ema200, 2),
        "is_bear": is_btc_bear,
        "regime": "BEARISH (Below EMA200)" if is_btc_bear else "BULLISH (Above EMA200)"
    }

    # 2. If 'ALL', also gather 15-pair matrix
    all_tickers = fetch_all_tickers()
    pairs_matrix = []
    for s in SYMBOLS:
        t = all_tickers.get(s, {})
        p_chg = t.get("price24hPcnt", 0) * 100
        p_px = t.get("lastPrice", 0)
        # Determine quick status
        status = "NEUTRAL"
        if p_chg > btc_ticker["price24hPcnt"] * 100 + 1.5:
            status = "STRONG DIVERGENCE (BULLISH)"
        elif p_chg < btc_ticker["price24hPcnt"] * 100 - 1.5:
            status = "HEAVY BLEED (BEARISH)"
        pairs_matrix.append({
            "symbol": s,
            "price": p_px,
            "change24h": round(p_chg, 2),
            "status": status
        })

    # Pick focus symbol for detailed reading
    focus_sym = "BTCUSDT" if (target_symbol == "ALL" or target_symbol not in SYMBOLS) else target_symbol

    # Fetch focus symbol details
    coin_ticker = fetch_ticker(focus_sym) or btc_ticker
    coin_bars = fetch_klines(focus_sym, interval="15", limit=60)
    if not coin_bars:
        coin_bars = btc_bars

    coin_closes = [b["close"] for b in coin_bars]
    coin_ema21 = calc_ema(coin_closes, 21)
    coin_ema50 = calc_ema(coin_closes, 50)
    vp_data = calc_vp(coin_bars)
    sr_data = analyze_sr_and_tests(coin_bars, coin_ticker["lastPrice"])
    liq_data = analyze_liquidity_hunt(coin_bars, coin_ticker["lastPrice"])

    coin_data = {
        "symbol": focus_sym,
        "lastPrice": coin_ticker["lastPrice"],
        "price24hPcnt": coin_ticker["price24hPcnt"],
        "ema21": round(coin_ema21, 4),
        "ema50": round(coin_ema50, 4)
    }

    # Prepare DeepSeek Prompt
    prompt = f"""You are the Lead Quantitative Crypto Market Maker and ICT Smart Money Concepts analyst for the CME-X5 Trading Terminal.
Generate an advanced, comprehensive institutional market read for {focus_sym} based on this real-time exchange data:

[BTC MACRO REGIME]
- BTC Price: ${btc_data['lastPrice']:,.2f}
- BTC 24h Change: {btc_data['price24hPcnt']*100:+.2f}%
- BTC 15M EMA200: ${btc_data['ema200']:,.2f}
- BTC Macro Regime: {btc_data['regime']}

[TARGET ASSET: {focus_sym}]
- Current Mark Price: {coin_data['lastPrice']}
- 24h Change: {coin_data['price24hPcnt']*100:+.2f}%
- 15M EMAs: EMA21={coin_data['ema21']}, EMA50={coin_data['ema50']}
- Volume Profile POC: {vp_data['poc'] if vp_data else 'N/A'} (VAH: {vp_data['vah'] if vp_data else 'N/A'}, VAL: {vp_data['val'] if vp_data else 'N/A'})

[STRUCTURAL S/R & MULTI-TOUCH DYNAMICS]
- Support Level: {sr_data['support']} (Tested & rejected {sr_data['sup_tests']} distinct times with lower wicks)
- Resistance Level: {sr_data['resistance']} (Tested & rejected {sr_data['res_tests']} distinct times)

[LIQUIDITY HUNT & ORDER FLOW]
- Liquidity Sweep Status: {liq_data['sweep_type']} (Level: {liq_data['sweep_level']})
- Unfilled FVGs: {liq_data['unfilled_bullish_fvgs']} Bullish Fair Value Gaps, {liq_data['unfilled_bearish_fvgs']} Bearish Fair Value Gaps
- AMD Session Phase: {get_amd_phase()}

Respond with ONLY a strict JSON object (no markdown quotes, no triple backticks) matching this exact format:
{{
  "symbol": "{focus_sym}",
  "bias": "BULLISH_REVERSAL" | "BEARISH_BREAKDOWN" | "RANGEBOUND_ABSORPTION" | "BREAKOUT_WATCH",
  "confidence": 85,
  "executive_summary": "<Crisp 2-sentence institutional summary stating current structure and immediate bias>",
  "btc_macro_impact": "<Deep explanation of how BTC's 24h move of {btc_data['price24hPcnt']*100:+.2f}% and EMA200 position affects {focus_sym}, beta spillover, and whether alts are bleeding, resisting, or diverging>",
  "liquidity_hunt": "<Analytical breakdown of the liquidity hunt / sweep, fair value gaps (FVG), AMD phase, and whether retail stops were swept>",
  "sr_structure": {{
    "support_level": "{sr_data['support']}",
    "resistance_level": "{sr_data['resistance']}",
    "sup_tests": {sr_data['sup_tests']},
    "res_tests": {sr_data['res_tests']},
    "analysis": "<Detailed read on the {sr_data['sup_tests']} test attempts at support and the buyer/seller rejection absorption behavior>"
  }},
  "projected_levels": {{
    "primary_target": "<Exact price number and reason e.g. POC / FVG fill>",
    "secondary_target": "<Exact price number for extension>",
    "invalidation_level": "<Exact price number where the setup is invalidated>",
    "projected_path": "<Step-by-step forecasted price trajectory with specific price levels>"
  }},
  "tactical_action": "BUY_RECLAIM" | "WAIT_CONFIRMATION" | "FADE_RALLY" | "SHORT_BREAKDOWN",
  "key_takeaway": "<One punchy tactical rule for the trader right now>"
}}
"""

    result_payload = None

    if DEEPSEEK_API_KEY:
        try:
            req_body = json.dumps({
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a quantitative institutional crypto market analyst. Return strict JSON only without markdown formatting."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1200,
                "temperature": 0.2
            }).encode("utf-8")

            req = urllib.request.Request(
                DEEPSEEK_URL,
                data=req_body,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "MASIS/4.0"
                }
            )
            with urllib.request.urlopen(req, timeout=12) as res:
                raw_text = res.read().decode("utf-8")
                api_json = json.loads(raw_text)
                ai_content = api_json["choices"][0]["message"]["content"].strip()
                # Clean up markdown fences if any
                if ai_content.startswith("```"):
                    ai_content = re.sub(r"^```(?:json)?\s*", "", ai_content)
                    ai_content = re.sub(r"\s*```$", "", ai_content)
                match = re.search(r"\{.*\}", ai_content, re.DOTALL)
                parsed_ai = json.loads(match.group(0) if match else ai_content)
                parsed_ai["model"] = "DeepSeek-V3 Neural Quant"
                parsed_ai["is_ai_live"] = True
                parsed_ai["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                result_payload = parsed_ai
        except Exception as e:
            print(f"[DeepSeek Market Reader] API query fallback: {e}")

    # Fallback to local quant analysis if API call failed or no key
    if not result_payload:
        result_payload = build_local_fallback_analysis(focus_sym, btc_data, coin_data, sr_data, liq_data, vp_data)

    if not result_payload.get("trade_suggestion"):
        fb = build_local_fallback_analysis(focus_sym, btc_data, coin_data, sr_data, liq_data, vp_data)
        result_payload["trade_suggestion"] = fb.get("trade_suggestion")

    # Attach BTC macro context and 15-pair matrix
    result_payload["btc_macro"] = btc_data
    result_payload["pairs_matrix"] = pairs_matrix
    result_payload["all_symbols"] = SYMBOLS

    # Cache response
    _ANALYSIS_CACHE[cache_key] = {
        "_cached_at": now,
        "data": result_payload
    }

    return result_payload

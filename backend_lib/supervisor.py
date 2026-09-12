"""Supervisor verdict -- the only model call the trading path makes.

The model is asked one question, about one fully-formed candidate that has
already passed every local gate, and must answer in a fixed JSON shape the
engine consumes: CONFIRM, DOWNGRADE or VETO. Ported from server.py with no
logic changes.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import llm_budget
from . import market as market_mod
from . import news as news_mod

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL") or "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"

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

    news_state = news_mod.get_news_state()
    macro_state = market_mod.get_market_overview_state()

    if not DEEPSEEK_API_KEY:
        return {
            "verdict": "CONFIRM", "confidence": 0, "is_fallback": True,
            "fallback_reason": "No DEEPSEEK_API_KEY configured — running on local gates only, which is a supported mode",
            "rationale": "No supervisor model configured; the local gate result stands unmodified.",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }

    if not llm_budget.take("supervisor-verdict"):
        return {
            "verdict": "CONFIRM", "confidence": 0, "is_fallback": True,
            "fallback_reason": f"Daily model budget spent ({llm_budget.DAILY_BUDGET} calls) — local gates stand on their own",
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

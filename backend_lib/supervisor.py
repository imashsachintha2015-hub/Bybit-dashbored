"""Supervisor verdict -- the only model call the trading path makes.

The supervisor reviews one pre-screened candidate. Historical performance is
used as learning evidence, not as a permanent blacklist: a previous loss can
veto a new trade only when the current candidate still exhibits the concrete
failure condition that caused the earlier loss.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

from . import llm_budget
from . import track_record as track_record_mod
from . import market as market_mod
from . import news as news_mod


DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

DEEPSEEK_URL = (
    os.environ.get("DEEPSEEK_URL")
    or "https://api.deepseek.com/v1/chat/completions"
)

DEEPSEEK_MODEL = (
    os.environ.get("DEEPSEEK_MODEL")
    or "deepseek-chat"
)


# ============================================================
# SUPERVISOR SYSTEM PROMPT
# ============================================================

SUPERVISOR_SYSTEM = (
    "You are a risk supervisor reviewing ONE pre-screened trade candidate from a "
    "quantitative crypto system. The technical work is already done and is not "
    "yours to redo. Your job is narrow: identify a reason this specific trade "
    "should NOT be taken right now that the local gates could have missed — a "
    "known event, a contradiction between the stated evidence, or an obviously "
    "poor location. Default to CONFIRM. Reserve VETO for a concrete, nameable "
    "present-tense problem. Reply with JSON only, no prose, no code fences.\n\n"

    "CRITICAL — LEARN FROM FAILURES WITHOUT BLACKLISTING:\n"

    "1. TRACK RECORD IS LEARNING DATA, NOT A COIN/SETUP BLACKLIST. "
    "A symbol or setup losing 3+ times does NOT by itself justify VETO. "
    "Never veto solely because a coin, setup, or combination lost previously.\n"

    "2. When repeated failures are reported, identify the concrete failure "
    "conditions recorded for those trades and compare them with THIS candidate. "
    "Only treat the history as a strong veto signal when the same failure "
    "conditions are visibly present now.\n"

    "3. If the current market regime, direction, location, momentum, volume, "
    "or other recorded conditions are materially different from the losing "
    "examples, history alone must NOT block the trade. "
    "The system should learn what caused the loss, not learn to fear the coin.\n"

    "4. Position manager exits such as THESIS_FLIP, TIME_STOP, "
    "STRUCTURE_BROKEN and EMERGENCY are useful evidence. "
    "Do not turn their count into an automatic veto. "
    "Ask whether the current candidate repeats the conditions associated "
    "with those losses.\n"

    "5. Small samples are weak evidence. A few losses can justify caution "
    "but cannot create a permanent rule. "
    "Absence of history is not evidence against a trade.\n"

    "6. EVERY VETO MUST HAVE A CONCRETE CURRENT REASON. "
    "The rationale must describe a present-tense problem with this candidate. "
    "A historical loss count alone is never sufficient.\n"

    "7. The objective is to protect profitability by avoiding repeated mistakes "
    "while still allowing genuinely new and high-quality setups to trade. "
    "Do not solve bad history by stopping all trading."
)


# ============================================================
# MAIN SUPERVISOR
# ============================================================

def run_supervisor_verdict(payload):
    """
    Returns:

        {
            "verdict": "CONFIRM | DOWNGRADE | VETO",
            "confidence": 0-100,
            "rationale": "...",
            "risk": "...",
            "is_fallback": bool
        }

    IMPORTANT:
    Supervisor failure falls back to CONFIRM because local gates have already
    screened the candidate. The supervisor is a second-opinion risk layer,
    not the primary signal generator.
    """

    symbol = payload.get(
        "symbol",
        "BTCUSDT",
    )

    direction = payload.get(
        "direction",
        "LONG",
    )

    setup = payload.get(
        "setup",
        "UNKNOWN",
    )

    score = payload.get(
        "score",
        0,
    )

    grade = payload.get(
        "grade",
        "B",
    )

    regime = payload.get(
        "regime",
        "UNKNOWN",
    )

    bias = payload.get(
        "bias",
        "NEUTRAL",
    )

    entry = payload.get(
        "entry",
    )

    stop = payload.get(
        "stop",
    )

    targets = payload.get(
        "targets",
        [],
    )

    rr = payload.get(
        "riskReward",
    )

    evidence = payload.get(
        "evidence",
        [],
    )


    # ========================================================
    # MARKET CONTEXT
    # ========================================================

    news_state = news_mod.get_news_state()

    macro_state = (
        market_mod
        .get_market_overview_state()
    )


    # ========================================================
    # API KEY CHECK
    # ========================================================

    api_key = (
        os.environ.get("DEEPSEEK_API_KEY")
        or DEEPSEEK_API_KEY
    )

    if not api_key:

        return {
            "verdict": "CONFIRM",
            "confidence": 0,
            "is_fallback": True,

            "fallback_reason": (
                "No DEEPSEEK_API_KEY configured — "
                "running on local gates only, "
                "which is a supported mode"
            ),

            "rationale": (
                "No supervisor model configured; "
                "the local gate result stands unmodified."
            ),

            "timestamp": time.strftime(
                "%Y-%m-%d %H:%M:%S UTC",
                time.gmtime(),
            ),
        }


    # ========================================================
    # LLM BUDGET
    # ========================================================

    if not llm_budget.take(
        "supervisor-verdict"
    ):

        daily_b = int(
            os.environ.get(
                "DEEPSEEK_DAILY_CALL_BUDGET"
            )
            or "2000"
        )

        return {
            "verdict": "CONFIRM",
            "confidence": 0,
            "is_fallback": True,

            "fallback_reason": (
                f"Daily model budget spent "
                f"({daily_b} calls) — "
                "local gates stand on their own"
            ),

            "rationale": (
                "Budget exhausted; the local gate "
                "result stands unmodified. "
                "This is the designed fallback, "
                "not a degradation."
            ),

            "timestamp": time.strftime(
                "%Y-%m-%d %H:%M:%S UTC",
                time.gmtime(),
            ),
        }


    # ========================================================
    # EVIDENCE
    # ========================================================

    evidence_lines = "\n".join(
        f"- {e}"
        for e in evidence[:8]
    ) or "- (none supplied)"


    # ========================================================
    # TRACK RECORD / FAILURE MEMORY
    # ========================================================

    try:

        record = track_record_mod.build(
            symbol,
            setup,
            direction,
            regime,
        )

    except TypeError:

        # Backward compatibility with an older track_record.py
        try:

            record = track_record_mod.build(
                symbol,
                setup,
            )

        except Exception as e:

            print(
                f"[supervisor] track record unavailable: {e}"
            )

            record = ""

    except Exception as e:

        print(
            f"[supervisor] track record unavailable: {e}"
        )

        record = ""


    if record:

        record_block = (
            "\n\n"
            "TRACK RECORD "
            "(measured learning evidence; NOT a blacklist):\n"
            f"{record}"
        )

    else:

        record_block = ""


    # ========================================================
    # SUPERVISOR PROMPT
    # ========================================================

    prompt = f"""
Candidate: {direction} {symbol}

Setup: {setup}

Local quality score: {score}/100

Grade: {grade}

Regime: {regime}

Higher-timeframe bias: {bias}

Entry: {entry}

Stop: {stop}

Targets: {targets}

Reward:risk to TP2: {rr}


Local evidence:
{evidence_lines}


Context:

- News sentiment:
  {news_state.get('sentiment_label')}
  ({news_state.get('sentiment_score')})

- Latest headline:
  "{news_state.get('headline')}"

- Macro:
  BTC dominance {macro_state.get('btc_dominance')}%

- Total market cap 24h:
  {macro_state.get('market_cap_change_24h_pct')}%

- Risk-off:
  {macro_state.get('risk_off')}
{record_block}


DECISION RULES FOR HISTORY:

1. Historical loss count alone cannot VETO.

2. A repeated failure pattern is actionable only if the current
   candidate matches the recorded failure conditions.

3. If conditions changed, allow normal evaluation.

4. VETO requires a concrete present-tense reason.

5. The rationale for VETO must identify the current problem,
   not merely say that this coin or setup lost before.

6. Do not assume the cause of an old loss if that cause was not
   actually recorded.

7. A different market regime can invalidate the relevance of
   an old failure pattern.

8. Protect profitability by avoiding repeated mistakes while
   allowing genuinely new/high-quality setups to trade.


VETO REQUIREMENT:

A VETO should normally contain a concrete combination such as:

- current market contradiction
- current structural failure
- current liquidity problem
- current risk event
- current invalidation
- OR current conditions that clearly reproduce a documented
  previous failure pattern


Do NOT use:

"VETO because this coin lost 3 times."

Do NOT use:

"VETO because this setup has a poor history."

Do NOT use:

"VETO because repeated failures."

Instead explain the current condition.


Reply with exactly this JSON:

{{
    "verdict": "CONFIRM|DOWNGRADE|VETO",
    "confidence": 0-100,
    "rationale": "one sentence, max 30 words",
    "risk": "the single biggest risk to this trade, max 15 words"
}}
"""


    # ========================================================
    # DEEPSEEK CALL
    # ========================================================

    try:

        model = (
            os.environ.get("DEEPSEEK_MODEL")
            or DEEPSEEK_MODEL
        )

        url = (
            os.environ.get("DEEPSEEK_URL")
            or DEEPSEEK_URL
        )


        body = json.dumps(
            {
                "model": model,

                "messages": [
                    {
                        "role": "system",
                        "content": SUPERVISOR_SYSTEM,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],

                "max_tokens": 150,

                "temperature": 0.1,

                "response_format": {
                    "type": "json_object",
                },
            }
        ).encode("utf-8")


        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "MASIS/3.0",
            },
        )


        with urllib.request.urlopen(
            request,
            timeout=25,
        ) as response:

            result = json.loads(
                response
                .read()
                .decode(
                    "utf-8",
                    errors="replace",
                )
            )


            text = (
                result["choices"][0]
                ["message"]
                ["content"]
            )


            # ------------------------------------------------
            # Extract JSON
            # ------------------------------------------------

            match = re.search(
                r"\{.*\}",
                text,
                re.DOTALL,
            )


            parsed = json.loads(
                match.group(0)
                if match
                else text
            )


            # ------------------------------------------------
            # Normalize verdict
            # ------------------------------------------------

            verdict = str(
                parsed.get(
                    "verdict",
                    "CONFIRM",
                )
            ).upper()


            if verdict not in (
                "CONFIRM",
                "DOWNGRADE",
                "VETO",
            ):

                verdict = "CONFIRM"


            # ------------------------------------------------
            # Confidence
            # ------------------------------------------------

            try:

                confidence = int(
                    parsed.get(
                        "confidence",
                        50,
                    )
                )

            except Exception:

                confidence = 50


            confidence = max(
                0,
                min(
                    100,
                    confidence,
                ),
            )


            # ------------------------------------------------
            # Usage
            # ------------------------------------------------

            usage = result.get(
                "usage",
                {},
            )


            return {
                "verdict": verdict,

                "confidence": confidence,

                "rationale": str(
                    parsed.get(
                        "rationale",
                        "",
                    )
                )[:240],

                "risk": str(
                    parsed.get(
                        "risk",
                        "",
                    )
                )[:160],

                "is_fallback": False,

                "model": model,

                "tokens": usage.get(
                    "total_tokens"
                ),

                "timestamp": time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC",
                    time.gmtime(),
                ),
            }


    # ========================================================
    # HTTP ERROR
    # ========================================================

    except urllib.error.HTTPError as e:

        detail = (
            e.read()
            .decode(
                "utf-8",
                errors="replace",
            )[:200]
        )


        if e.code == 402:

            reason = (
                "DeepSeek HTTP 402 — "
                "account has no credit balance."
            )

        else:

            reason = (
                f"DeepSeek HTTP {e.code}: "
                f"{detail}"
            )


        print(
            f"[supervisor] HTTP error: {reason}"
        )


    # ========================================================
    # GENERAL ERROR
    # ========================================================

    except Exception as e:

        reason = (
            f"DeepSeek unreachable: {e}"
        )

        print(
            f"[supervisor] Exception: {reason}"
        )


    # ========================================================
    # FALLBACK
    # ========================================================

    return {
        "verdict": "CONFIRM",

        "confidence": 0,

        "is_fallback": True,

        "fallback_reason": reason,

        "rationale": (
            "Supervisor unavailable; abstaining. "
            "The local gate result stands unmodified."
        ),

        "timestamp": time.strftime(
            "%Y-%m-%d %H:%M:%S UTC",
            time.gmtime(),
        ),
}

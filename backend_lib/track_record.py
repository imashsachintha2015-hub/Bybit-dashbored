"""Measured trading history used by the supervisor.

Historical losses are learning evidence, NOT a permanent blacklist.

The important distinction is:
    failed trade != failed coin/setup

The supervisor should learn the conditions associated with previous losses and
compare those conditions with the current candidate.
"""

from . import signal_log as signal_log_mod
from . import trade_stats as trade_stats_mod

MIN_SAMPLE = 5
RECENT_LOSSES = 4
REPEAT_SAMPLE = 3


def _rate(rows):
    if not rows:
        return None

    wins = sum(
        1 for r in rows
        if (r.get("r_multiple") or 0) > 0
    )

    rs = [
        r.get("r_multiple") or 0
        for r in rows
    ]

    return {
        "n": len(rows),
        "wr": round(100 * wins / len(rows)),
        "avg_r": round(sum(rs) / len(rs), 2),
    }


def _text_values(value):
    """Flatten recorded context into compact searchable text."""

    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, (list, tuple)):
        output = []

        for item in value:
            output.extend(_text_values(item))

        return output

    if isinstance(value, dict):
        output = []

        for key, val in value.items():
            if isinstance(val, (str, int, float, bool)):
                output.append(f"{key}={val}")

        return output

    return [str(value)]


def _failure_context(trade):
    """
    Extract conditions that were actually recorded with the losing trade.

    IMPORTANT:
    We do NOT invent reasons for losses.
    """

    fields = (
        "failure_reason",
        "exit_reason",
        "regime",
        "bias",
        "direction",
        "side",
        "setup_type",
        "market_regime",
        "timeframe",
        "entry_reason",
        "invalidation_reason",
        "veto_reason",
        "risk_reason",
        "evidence",
        "features",
        "conditions",
        "signals",
    )

    values = []

    for field in fields:
        value = trade.get(field)

        if value not in (None, "", [], {}):
            values.extend(_text_values(value))

    seen = set()
    result = []

    for value in values:
        value = str(value).strip()

        if not value:
            continue

        key = value.lower()

        if key not in seen:
            seen.add(key)
            result.append(value)

    return result[:12]


def _context_similarity(
    current_setup,
    current_direction,
    current_regime,
    loss,
):
    """
    Compare only information that exists on both sides.

    Returns:
        score, known_fields
    """

    comparisons = (
        (
            current_setup,
            loss.get("setup_type"),
        ),
        (
            current_direction,
            loss.get("side") or loss.get("direction"),
        ),
        (
            current_regime,
            loss.get("regime") or loss.get("market_regime"),
        ),
    )

    score = 0
    known = 0

    for current, previous in comparisons:

        if current in (None, "", "UNKNOWN"):
            continue

        if previous in (None, "", "UNKNOWN"):
            continue

        known += 1

        if str(current).upper() == str(previous).upper():
            score += 1

    return score, known


def build(
    symbol,
    setup,
    direction="UNKNOWN",
    regime="UNKNOWN",
):
    """
    Build measured learning evidence for the current candidate.

    IMPORTANT:

    Previous losses are warnings.

    They are NOT automatic VETO instructions.
    """

    lines = []

    # ---------------------------------------------------------
    # LOAD HISTORY
    # ---------------------------------------------------------

    try:
        history = (
            trade_stats_mod
            .load()
            .get("trade_history", [])
            or []
        )
    except Exception:
        history = []

    try:
        signals = (
            signal_log_mod
            .load()
            .get("signals", [])
            or []
        )
    except Exception:
        signals = []

    # ---------------------------------------------------------
    # 1. SUPERVISOR CALIBRATION
    # ---------------------------------------------------------

    by_verdict = {
        "CONFIRM": [],
        "DOWNGRADE": [],
        "VETO": [],
    }

    for row in signals:

        verdict = (
            (row.get("supervisor") or {})
            .get("verdict")
            or ""
        ).upper()

        outcome = row.get("shadow_outcome")

        if verdict in by_verdict and outcome in (
            "WIN",
            "LOSS",
        ):
            by_verdict[verdict].append(
                1 if outcome == "WIN" else 0
            )

    scored = {
        key: value
        for key, value in by_verdict.items()
        if value
    }

    if scored:

        parts = []

        for key, values in scored.items():

            parts.append(
                f"{key} "
                f"{round(100 * sum(values) / len(values))}% "
                f"(n={len(values)})"
            )

        lines.append(
            "Past supervisor verdict outcomes: "
            + ", ".join(parts)
        )

    # ---------------------------------------------------------
    # 2. SETUP PERFORMANCE
    # ---------------------------------------------------------

    same_setup = [
        trade
        for trade in history
        if trade.get("setup_type") == setup
    ]

    setup_stats = _rate(same_setup)

    if setup_stats:

        if setup_stats["n"] >= MIN_SAMPLE:

            lines.append(
                f"{setup} history: "
                f"{setup_stats['n']} closed trades, "
                f"{setup_stats['wr']}% won, "
                f"average {setup_stats['avg_r']}R"
            )

        else:

            lines.append(
                f"{setup} history: "
                f"{setup_stats['n']} closed trades — "
                f"insufficient sample for a conclusion"
            )

    # ---------------------------------------------------------
    # 3. SYMBOL HISTORY
    # ---------------------------------------------------------

    symbol_history = [
        trade
        for trade in history
        if trade.get("symbol") == symbol
    ]

    symbol_losses = [
        trade
        for trade in symbol_history
        if (
            (trade.get("r_multiple") or 0) <= 0
            or
            (trade.get("pnl") or 0) < 0
        )
    ]

    if symbol_losses:

        recent = symbol_losses[:RECENT_LOSSES]

        descriptions = []

        for trade in recent:

            context = _failure_context(trade)

            setup_name = (
                trade.get("setup_type")
                or "?"
            )

            side = (
                trade.get("side")
                or trade.get("direction")
                or ""
            )

            exit_reason = (
                trade.get("exit_reason")
                or "unknown exit"
            )

            description = (
                f"{setup_name} {side} "
                f"-> {exit_reason} "
                f"({trade.get('r_multiple')}R)"
            )

            if context:

                description += (
                    " | context: "
                    + "; ".join(context[:5])
                )

            descriptions.append(description)

        lines.append(
            f"Recent losses on {symbol}: "
            + " || ".join(descriptions)
        )

    # ---------------------------------------------------------
    # 4. REPEATED FAILURE PATTERN
    #
    # IMPORTANT:
    # This is now diagnostic only.
    # ---------------------------------------------------------

    combo_losses = {}

    for trade in history:

        if trade.get("symbol") != symbol:
            continue

        if trade.get("setup_type") != setup:
            continue

        if (
            (trade.get("r_multiple") or 0) <= 0
            or
            (trade.get("pnl") or 0) < 0
        ):

            combo_losses.setdefault(
                setup,
                [],
            ).append(trade)

    repeated = combo_losses.get(setup, [])

    if len(repeated) >= REPEAT_SAMPLE:

        lines.append(
            f"REPEATED FAILURE PATTERN: "
            f"{symbol} + {setup} lost "
            f"{len(repeated)} times. "
            "This is a warning to compare the current "
            "conditions with previous failures; "
            "it is NOT an automatic veto."
        )

        contexts = []

        for loss in repeated[-REPEAT_SAMPLE:]:

            context = _failure_context(loss)

            if context:
                contexts.append(
                    "; ".join(context[:6])
                )

        if contexts:

            lines.append(
                "Recorded failure conditions: "
                + " || ".join(contexts)
            )

    # ---------------------------------------------------------
    # 5. CURRENT CONDITION MATCHING
    # ---------------------------------------------------------

    matching = []

    for loss in symbol_losses[:12]:

        score, known = _context_similarity(
            setup,
            direction,
            regime,
            loss,
        )

        if score > 0:
            matching.append(
                (score, known, loss)
            )

    strong_matches = [
        item
        for item in matching
        if item[0] >= 2 and item[1] >= 2
    ]

    if strong_matches:

        lines.append(
            "Current-condition comparison: "
            f"{len(strong_matches)} recent loss(es) "
            "share at least 2 known context fields "
            "with this candidate."
        )

    else:

        lines.append(
            "Current-condition comparison: "
            "no measured evidence that this candidate "
            "repeats a prior loss context."
        )

    # ---------------------------------------------------------
    # 6. POSITION MANAGER EXITS
    # ---------------------------------------------------------

    pm_exits = [
        trade
        for trade in history
        if trade.get("exit_reason") in (
            "THESIS_FLIP",
            "TIME_STOP",
            "STRUCTURE_BROKEN",
            "EMERGENCY",
        )
    ]

    if pm_exits:

        pm_losses = [
            trade
            for trade in pm_exits
            if (trade.get("pnl") or 0) < 0
        ]

        lines.append(
            "Position-manager exits: "
            f"{len(pm_exits)} total, "
            f"{len(pm_losses)} negative. "
            "Use their recorded conditions to detect "
            "repetition; do not veto solely from the count."
        )

    return "\n".join(
        f"- {line}"
        for line in lines
     )

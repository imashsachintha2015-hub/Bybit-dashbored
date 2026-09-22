"""Cross-device autonomous-execution arm state.

The dashboard has no login/session system -- it's a single shared demo
account, and the entire decision engine (regime read, setup grading, the
analyst panel, position management) runs client-side in whichever browser
tab has the page open.

This persists the arm flag, position-sizing configuration, and strategy mode
through kv.py so every browser tab/device sees the same selected strategy.
"""
from .kv import kv_get_json, kv_set_json

STATE_KEY = "auto_trade_state"
VALID_STRATEGY_MODES = {"standard", "scalp", "sureshot"}

DEFAULT_STATE = {
    "armed": False,
    "virtualEquity": 10.0,
    "riskPerTradePct": 5.0,
    "sizingMode": "usdt",
    "fixedUsdtSize": 10,
    "targetNotional": 100.0,
    "dailyGrossTarget": 5.0,
    "maxConcurrentPositions": 1,
    "leverage": 10,
    "marginMode": "cross",
    "strategyMode": "standard",
    "scalpMode": False,
    "sureShotMode": False,
    "theses": {},
}


def _normalize_strategy_mode(state):
    mode = str(state.get("strategyMode") or "").lower().strip()

    if mode not in VALID_STRATEGY_MODES:
        if state.get("sureShotMode"):
            mode = "sureshot"
        elif state.get("scalpMode"):
            mode = "scalp"
        else:
            mode = "standard"

    state["strategyMode"] = mode
    state["scalpMode"] = mode in ("scalp", "sureshot")
    state["sureShotMode"] = mode == "sureshot"
    return state


def load():
    state = kv_get_json(STATE_KEY, None)
    if not state:
        return dict(DEFAULT_STATE)

    return _normalize_strategy_mode({**DEFAULT_STATE, **state})


def save(
    armed=None,
    risk_per_trade_pct=None,
    sizing_mode=None,
    fixed_usdt_size=None,
    leverage=None,
    margin_mode=None,
    theses=None,
    daily_gross_target=None,
    target_notional=None,
    virtual_equity=None,
    max_concurrent_positions=None,
    strategy_mode=None,
    scalp_mode=None,
    sure_shot_mode=None,
):
    current = load()

    merged_theses = dict(current.get("theses", {}) or {})
    if isinstance(theses, dict):
        for key, value in theses.items():
            if value is None:
                merged_theses.pop(key, None)
            else:
                merged_theses[key] = value

    requested_mode = (
        str(strategy_mode).lower().strip()
        if strategy_mode is not None
        else None
    )

    # IMPORTANT: Only an explicit strategy_mode, or an explicit positive
    # scalp/sureshot flag, may change the persisted strategy. Legacy callers
    # often POST sizing/arm/thesis fields without strategyMode. Those calls
    # must preserve the user's selected mode instead of silently reverting to
    # Swing/standard.
    if requested_mode not in VALID_STRATEGY_MODES:
        if sure_shot_mode is True:
            requested_mode = "sureshot"
        elif scalp_mode is True:
            requested_mode = "scalp"
        else:
            requested_mode = current.get("strategyMode", "standard")

    state = {
        "armed": bool(armed) if armed is not None else current.get("armed", False),
        "virtualEquity": float(virtual_equity) if virtual_equity is not None else current.get("virtualEquity", 10.0),
        "riskPerTradePct": float(risk_per_trade_pct) if risk_per_trade_pct is not None else current.get("riskPerTradePct", 5.0),
        "sizingMode": sizing_mode if sizing_mode in ("risk", "usdt", "min") else current.get("sizingMode", "usdt"),
        "fixedUsdtSize": float(fixed_usdt_size) if fixed_usdt_size is not None else current.get("fixedUsdtSize", 10),
        "targetNotional": float(target_notional) if target_notional is not None else current.get("targetNotional", 100.0),
        "dailyGrossTarget": float(daily_gross_target) if daily_gross_target is not None else current.get("dailyGrossTarget", 5.0),
        "maxConcurrentPositions": int(max_concurrent_positions) if max_concurrent_positions is not None else current.get("maxConcurrentPositions", 1),
        "leverage": int(leverage) if leverage and int(leverage) > 0 else current.get("leverage", 10),
        "marginMode": str(margin_mode).lower() if str(margin_mode).lower() in ("cross", "isolated") else current.get("marginMode", "cross"),
        "strategyMode": requested_mode,
        "scalpMode": requested_mode in ("scalp", "sureshot"),
        "sureShotMode": requested_mode == "sureshot",
        "theses": merged_theses,
    }

    kv_set_json(STATE_KEY, state)
    return state

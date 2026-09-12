"""Cross-device autonomous-execution arm state.

The dashboard has no login/session system -- it's a single shared demo
account, and the entire decision engine (regime read, setup grading, the
analyst panel, position management) runs client-side in whichever browser
tab has the page open. Before this, whether auto-trading was ARMED or
STOPPED lived only in that one tab's JS memory (a plain `let`), so opening
the dashboard on a different device, or even just reloading, always came
back up STOPPED regardless of what was armed a moment ago elsewhere.

This persists the arm flag and the position-sizing configuration (risk % of
equity, or a fixed USDT notional per trade -- see agents/risk-governor.js's
two sizing modes) through kv.py, so every device shows the same, current
state. It does NOT make it safe to have two tabs both actively armed at
once: each tab's engine runs and places orders independently, with no
shared lock between them, so two tabs seeing the same signal in the same
cycle can both fire an order. Treat this as "which device is currently
driving trades, sized how" staying visible and consistent across devices,
not as multi-device coordination.
"""
from .kv import kv_get_json, kv_set_json

STATE_KEY = "auto_trade_state"

DEFAULT_STATE = {"armed": False, "riskPerTradePct": 0.5, "sizingMode": "risk", "fixedUsdtSize": 100}


def load():
    state = kv_get_json(STATE_KEY, None)
    if not state:
        return dict(DEFAULT_STATE)
    return {**DEFAULT_STATE, **state}


def save(armed, risk_per_trade_pct, sizing_mode=None, fixed_usdt_size=None):
    state = {
        "armed": bool(armed),
        "riskPerTradePct": float(risk_per_trade_pct) if risk_per_trade_pct is not None else DEFAULT_STATE["riskPerTradePct"],
        "sizingMode": sizing_mode if sizing_mode in ("risk", "usdt") else DEFAULT_STATE["sizingMode"],
        "fixedUsdtSize": float(fixed_usdt_size) if fixed_usdt_size else DEFAULT_STATE["fixedUsdtSize"],
    }
    kv_set_json(STATE_KEY, state)
    return state

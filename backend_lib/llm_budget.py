"""Hard ceiling on model spend, persisted via kv.py.

The old build had no budget and three uncapped call sites -- on the order of
2,400 calls a day, the large majority re-analysing a state that had not
changed. Here every call is counted, attributed to a caller, and refused
past the ceiling; refusal is a normal operating state, not a degradation --
the engine's decisions are made locally and the model only acts as an
optional second opinion.

This counter has to survive across requests to do its job at all. Without a
KV store configured (see kv.py), it can only track calls made to the SAME
warm function instance and resets far more often than once a day -- the
budget effectively stops capping anything. Attach Vercel KV for this to mean
what it says.
"""
import os
import time

from .kv import kv_get_json, kv_set_json

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
# `.get(name, default)` only falls back when the var is UNSET, not when it's
# set to "" -- an env var added with a blank value would otherwise crash
# int() at import time and take the whole function down. `or` catches both.
DAILY_BUDGET = int(os.environ.get("DEEPSEEK_DAILY_CALL_BUDGET") or "2000")

BUDGET_KEY = "llm_budget"


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load():
    state = kv_get_json(BUDGET_KEY, None)
    if not state or state.get("day") != _today():
        state = {"day": _today(), "calls": 0, "by_caller": {}, "refused": 0, "cache_hits": 0}
    return state


def _api_key():
    return os.environ.get("DEEPSEEK_API_KEY")


def _daily_budget():
    return int(os.environ.get("DEEPSEEK_DAILY_CALL_BUDGET") or "2000")


def take(caller):
    """Reserves one call against today's budget. False means do not call."""
    if not _api_key():
        return False
    b = _daily_budget()
    state = _load()
    if b > 0 and state["calls"] >= b:
        state["refused"] += 1
        kv_set_json(BUDGET_KEY, state)
        return False
    state["calls"] += 1
    state["by_caller"][caller] = state["by_caller"].get(caller, 0) + 1
    kv_set_json(BUDGET_KEY, state)
    return True


def note_cache_hit():
    state = _load()
    state["cache_hits"] += 1
    kv_set_json(BUDGET_KEY, state)


def reset():
    state = {"day": _today(), "calls": 0, "by_caller": {}, "refused": 0, "cache_hits": 0}
    kv_set_json(BUDGET_KEY, state)
    return state


def status():
    state = _load()
    b = _daily_budget()
    return {
        "day": state["day"],
        "calls_today": state["calls"],
        "daily_budget": b,
        "remaining": max(0, b - state["calls"]) if b > 0 else 999999,
        "by_caller": dict(state["by_caller"]),
        "refused": state["refused"],
        "cache_hits": state["cache_hits"],
        "configured": bool(_api_key()),
    }

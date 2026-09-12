"""Locally-recorded trade reasoning -- NOT the money ledger.

Bybit's closed-PnL feed (see api/performance.py) is the single source of
truth for P&L; this only stores what Bybit has no way to know about a closed
trade -- which setup fired, what grade it scored, why it was exited, and the
realised R -- keyed so it can be matched back to Bybit's rows by symbol,
side and time.

Needs the same durable storage as the LLM budget to mean anything across
serverless invocations -- see kv.py's module docstring.
"""
from .kv import kv_get_json, kv_set_json

STATS_KEY = "trade_stats"

DEFAULT_STATS = {
    "win_count": 0,
    "loss_count": 0,
    "gross_profit": 0.0,
    "gross_loss": 0.0,
    "trade_history": []
}


def load():
    stats = kv_get_json(STATS_KEY, None)
    if not stats:
        return dict(DEFAULT_STATS, trade_history=[])
    stats.setdefault("trade_history", [])
    return stats


def save(stats):
    kv_set_json(STATS_KEY, stats)

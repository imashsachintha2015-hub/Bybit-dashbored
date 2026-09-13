"""Every suggestion the engine raised -- taken, rejected, or vetoed.

The trade log (trade_stats.py) only ever held CLOSED POSITIONS, which makes
the most useful question about the system unanswerable: of everything it
wanted to do, what did it actually do, and was skipping the rest correct?
A gate that rejects good setups and a gate that rejects bad ones look
identical when only the survivors are recorded.

So this records the candidate at the moment of decision -- its geometry, the
regime it fired in, the order-flow state, the supervisor's verdict if one was
sought -- together with what happened to it. Rejections are first-class rows
here, not absences.

Two things keep this from growing without bound or drowning in duplicates:

  1. A hard cap on retained rows. Serverless KV is not a data warehouse; this
     is a rolling window, and the paging API reads from it.

  2. Caller-side de-duplication via `fingerprint`. A live engine re-evaluates
     the same symbol every few seconds and would otherwise write the same
     unchanged candidate hundreds of times. A row whose fingerprint matches
     the newest row for that symbol updates it in place instead of appending,
     so a candidate that persists for an hour stays one row and keeps its
     first-seen timestamp.
"""
import time

from . import klines as klines_mod
from .kv import kv_get_json, kv_set_json

SIGNALS_KEY = "signal_log"
# Outcomes where the engine did NOT end up in the trade, and where a shadow
# verdict is therefore meaningful. For a TAKEN row the trade record is the
# outcome and a shadow beside it would be noise.
UNTAKEN = ("NOT_TRADED", "REJECTED_RISK", "REJECTED_EXCHANGE", "NO_FILL")
MAX_ROWS = 500


def load():
    data = kv_get_json(SIGNALS_KEY, None)
    if not data or not isinstance(data, dict):
        return {"signals": []}
    data.setdefault("signals", [])
    return data


def save(data):
    kv_set_json(SIGNALS_KEY, data)


def record(body):
    """Append a suggestion, or update the newest matching one in place."""
    data = load()
    rows = data["signals"]
    now_ms = int(time.time() * 1000)
    fingerprint = body.get("fingerprint") or ""
    symbol = body.get("symbol", "")

    row = {
        "id": body.get("id") or f"SIG-{now_ms % 10000000}",
        "recorded_at": now_ms,
        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        "fingerprint": fingerprint,
        "symbol": symbol,
        "direction": body.get("direction", ""),
        "setup_type": body.get("setup_type", ""),
        "grade": body.get("grade", ""),
        "score": body.get("score"),
        "regime": body.get("regime", ""),
        "bias": body.get("bias", ""),
        "entry": body.get("entry"),
        "stop": body.get("stop"),
        "targets": body.get("targets"),
        "risk_reward": body.get("risk_reward"),
        # TAKEN | REJECTED | VETOED | NO_FILL | CANCELLED
        "outcome": body.get("outcome", ""),
        "reject_reason": body.get("reject_reason", ""),
        "flow": body.get("flow"),
        "supervisor": body.get("supervisor"),
        "evidence": body.get("evidence"),
        # Vetoes the analyst panel raised in observe mode without acting on
        # them. Paired with this row's shadow verdict, this is what settles
        # whether obeying them would have helped.
        "observed_vetoes": body.get("observed_vetoes"),
        # Which maker-offset arm this entry used, and what it actually filled
        # at -- the live counterpart to the backtest's fill assumption.
        "maker_offset_bps": body.get("maker_offset_bps"),
        "fill_price": body.get("fill_price"),
    }

    # Update the newest row for this symbol when the candidate is unchanged,
    # so a long-lived suggestion does not become hundreds of identical rows.
    if fingerprint:
        for i, existing in enumerate(rows):
            if existing.get("symbol") != symbol:
                continue
            if existing.get("fingerprint") == fingerprint:
                row["recorded_at"] = existing.get("recorded_at", now_ms)
                row["time"] = existing.get("time", row["time"])
                row["id"] = existing.get("id", row["id"])
                # Grade moves around while a setup is watched, so keep the range
                # rather than only the latest read: "was C, peaked at A" says
                # more about a candidate than whichever value it happened to
                # hold when it was last evaluated.
                grades = [g for g in (existing.get("grade_best"), existing.get("grade_worst"),
                                      existing.get("grade"), row.get("grade")) if g]
                if grades:
                    row["grade_best"] = sorted(grades)[0]      # A sorts before D
                    row["grade_worst"] = sorted(grades)[-1]
                # A verdict already settled on this suggestion must survive the
                # fold, or a late re-evaluation would silently erase it.
                for k in ("shadow_outcome", "shadow_exit", "shadow_resolved_at",
                          "shadow_held_ms", "shadow_source",
                          "maker_offset_bps", "fill_price", "observed_vetoes"):
                    if existing.get(k) is not None and row.get(k) is None:
                        row[k] = existing[k]
                row["first_seen"] = existing.get("first_seen") or existing.get("recorded_at")
                row["last_seen"] = now_ms
                row["seen_count"] = int(existing.get("seen_count") or 1) + 1
                rows[i] = row
                save(data)
                return row
            break  # only compare against the newest row for this symbol

    row["first_seen"] = now_ms
    row["last_seen"] = now_ms
    row["seen_count"] = 1
    rows.insert(0, row)
    del rows[MAX_ROWS:]
    save(data)
    return row


def resolve(body):
    """Attach the shadow outcome to a suggestion that was never traded.

    The point of recording declined suggestions is to find out whether
    declining them was right, and that is only answerable once the setup has
    played out. The engine already tracks each candidate's original entry,
    stop and target until one of them is reached; this stores that verdict on
    the row so it survives a reload and can be read back later.

    Matched on the newest row for the symbol carrying the fingerprint, since
    that is the row the live candidate folded into.
    """
    data = load()
    rows = data["signals"]
    fingerprint = body.get("fingerprint") or ""
    symbol = body.get("symbol", "")
    # The fingerprint carries grade and decision, both of which drift while a
    # candidate is being watched: a setup logged at grade D may be at grade B by
    # the time it resolves, and the log will have opened a new row for it. So
    # match the fingerprint first, then fall back to the newest unresolved
    # untaken row for the symbol, which is the one the shadow was following.
    target = None
    for row in rows:
        if row.get("symbol") == symbol and fingerprint and row.get("fingerprint") == fingerprint:
            target = row
            break
    if target is None:
        for row in rows:
            if (row.get("symbol") == symbol
                    and not row.get("shadow_outcome")
                    and row.get("outcome") in UNTAKEN):
                target = row
                break
    for row in ([target] if target else []):
        row["shadow_outcome"] = body.get("shadow_outcome", "")
        row["shadow_exit"] = body.get("exit_price")
        row["shadow_resolved_at"] = int(time.time() * 1000)
        row["shadow_held_ms"] = body.get("held_ms")
        save(data)
        return row
    return None


EXPIRE_AFTER_MS = 12 * 60 * 60 * 1000


def settle_pending(max_rows=4):
    """Settle a few outstanding suggestions from candles, browser-free.

    Bounded per call because this runs inside a page load: each row costs one
    upstream request, so a handful at a time keeps the monitor responsive while
    the backlog still drains over successive refreshes. Oldest first, so nothing
    is starved.

    Anything still unresolved after EXPIRE_AFTER_MS is marked EXPIRED rather
    than retried forever -- a setup that has reached neither its target nor its
    stop in twelve hours has no verdict worth waiting for.
    """
    data = load()
    rows = data["signals"]
    now_ms = int(time.time() * 1000)
    pending = [r for r in rows
               if r.get("outcome") in UNTAKEN and not r.get("shadow_outcome")
               and r.get("entry") and r.get("stop") and r.get("targets")]
    pending.sort(key=lambda r: r.get("recorded_at") or 0)

    changed = 0
    for row in pending[:max_rows]:
        verdict = None
        try:
            verdict = klines_mod.settle(row, now_ms=now_ms)
        except Exception:
            verdict = None
        if verdict is None:
            if now_ms - (row.get("recorded_at") or now_ms) > EXPIRE_AFTER_MS:
                row["shadow_outcome"] = "EXPIRED"
                row["shadow_resolved_at"] = now_ms
                changed += 1
            continue
        row["shadow_outcome"] = verdict
        row["shadow_resolved_at"] = now_ms
        row["shadow_held_ms"] = now_ms - (row.get("recorded_at") or now_ms)
        row["shadow_source"] = "server"
        changed += 1
    if changed:
        save(data)
    return changed


def page(page_num=1, limit=25, symbol=None, outcome=None):
    rows = load()["signals"]
    if symbol:
        rows = [r for r in rows if r.get("symbol") == symbol]
    if outcome:
        rows = [r for r in rows if r.get("outcome") == outcome]
    total = len(rows)
    limit = max(1, min(int(limit or 25), 100))
    page_num = max(1, int(page_num or 1))
    start = (page_num - 1) * limit
    return {
        "items": rows[start:start + limit],
        "page": page_num,
        "limit": limit,
        "total": total,
        "pages": max(1, (total + limit - 1) // limit),
        "counts": _counts(load()["signals"]),
        "arms": _arm_stats(load()["signals"]),
    }


def _arm_stats(rows):
    """Fill rate per maker-offset arm, over the whole log rather than a page.

    Only orders that actually reached the book count: an entry rejected by the
    risk governor never tested the offset, and including it would dilute both
    arms with outcomes the offset had no part in.
    """
    arms = {}
    for r in rows:
        bps = r.get("maker_offset_bps")
        if bps is None or r.get("outcome") not in ("ORDER_PLACED", "TAKEN", "NO_FILL"):
            continue
        a = arms.setdefault(str(bps), {"placed": 0, "filled": 0, "missed": 0})
        if r["outcome"] == "TAKEN":
            a["filled"] += 1
        elif r["outcome"] == "NO_FILL":
            a["missed"] += 1
        else:
            a["placed"] += 1
    for a in arms.values():
        settled = a["filled"] + a["missed"]
        a["fill_rate"] = round(100 * a["filled"] / settled) if settled else None
        a["settled"] = settled
    return arms


def _counts(rows):
    out = {}
    for r in rows:
        k = r.get("outcome") or "UNKNOWN"
        out[k] = out.get(k, 0) + 1
    return out

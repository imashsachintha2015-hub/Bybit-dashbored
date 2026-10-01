"""Trade lifecycle shared by both strategies: entry (sizing, order, fail-safe stop check),
reconciliation against the exchange, exit bookkeeping and the signal journal."""
import time

from .core import CFG, M15, now_ms, size_qty, stop_price, target_price


def journal(ctx, strategy, sym, side, action, reason="", **kw):
    """Keep the last signals (taken or skipped) for the status page."""
    with ctx.state.lock:
        s = ctx.state["signals"]
        s.append({"ts": now_ms(), "strategy": strategy, "symbol": sym, "side": "LONG" if side == 1 else "SHORT", "action": action, "reason": reason, **kw})
        del s[:-80]


def link_id(strategy, sym, bar_t):
    """Deterministic client order id: a retry of the same signal can never open a second position."""
    base = sym[:-4] if sym.endswith("USDT") else sym
    return f"{'t4' if strategy == 'trend4h' else 'sb'}-{base}-{int(bar_t // 60000)}"[:36]


def open_position(ctx, strategy, sym, side, stop_dist, target_dist, bar_t, meta=None):
    """Size, check the account limits, send the order with its stop attached, verify, record.
    Returns the position record or None."""
    ex, gov, st, log = ctx.ex, ctx.gov, ctx.state, ctx.log
    meta = meta or {}
    key = f"{strategy}:{sym}:{bar_t}"
    gov.fresh()                               # a scan can run for minutes: decide on the account as it is now
    sp, tk = ex.spec(sym), ex.ticker(sym)
    if not sp or not tk:
        log("skip", strategy=strategy, symbol=sym, side=side, reason="no market data / instrument spec", bar=bar_t)
        return None
    px = tk["ask"] if side == 1 else tk["bid"]
    stop = stop_price(px - side * stop_dist, side, sp["tick"])
    tp = target_price(px + side * target_dist, side, sp["tick"]) if target_dist else None
    risk_frac = CFG["risk_trend"] if strategy == "trend4h" else CFG["risk_snap"]
    qty = size_qty(gov.equity, risk_frac, px, stop, sp, CFG["max_pos_x"])
    if qty <= 0:
        log.once(key, "skip", strategy=strategy, symbol=sym, side=side, reason=f"cannot size within exchange limits (equity {gov.equity:.0f})", bar=bar_t)
        journal(ctx, strategy, sym, side, "skipped", "below exchange minimum")
        return None
    risk_usd, notional = qty * abs(px - stop), qty * px
    ok, why = gov.can_open(strategy, sym, risk_usd, notional)
    if not ok:
        log.once(key, "skip", strategy=strategy, symbol=sym, side=side, reason=why, bar=bar_t)
        journal(ctx, strategy, sym, side, "skipped", why)
        return None
    ex.ensure_leverage(sym)
    link = link_id(strategy, sym, bar_t)
    ok, ret, msg = ex.market_order(sym, side, qty, stop, tp, link)
    if not ok:
        log("error", strategy=strategy, symbol=sym, what="order rejected", ret=ret, msg=msg, qty=qty, stop=stop, tp=tp)
        journal(ctx, strategy, sym, side, "error", f"order rejected: {msg}")
        return None
    pos = None
    for _ in range(12):                       # wait for the fill to show up in the account
        snap = ex.positions() or {}
        if sym in snap:
            pos = snap[sym]
            break
        time.sleep(0.5)
    if pos is None:
        log("error", strategy=strategy, symbol=sym, what="order accepted but no position found", link=link, ret=ret)
        journal(ctx, strategy, sym, side, "error", "no position after order")
        return None
    entry, qty_f = pos["avg"], pos["size"]
    exact_stop = stop_price(entry - side * stop_dist, side, sp["tick"])
    exact_tp = target_price(entry + side * target_dist, side, sp["tick"]) if target_dist else None
    has_stop = pos["stop"] is not None and abs(pos["stop"] - exact_stop) <= 2 * sp["tick"]
    has_tp = (not target_dist) or (pos["tp"] is not None and abs(pos["tp"] - exact_tp) <= 2 * sp["tick"])
    if not (has_stop and has_tp):
        fixed = False
        for _ in range(3):
            fixed = ex.set_stop(sym, sl=exact_stop, tp=exact_tp)
            if fixed:
                break
            time.sleep(0.5)
        if not fixed and not has_stop:
            # an unprotected position is worse than a closed one
            closed = ex.close(sym, side, qty_f)
            log("critical", strategy=strategy, symbol=sym, what="could not set the stop; position closed", closed=closed, link=link)
            journal(ctx, strategy, sym, side, "error", "stop could not be set; closed")
            return None
    rec = {"id": link, "strategy": strategy, "sym": sym, "side": side, "entry": entry, "qty": qty_f, "stop": exact_stop, "target": exact_tp,
           "risk": abs(entry - exact_stop), "risk_usd": qty_f * abs(entry - exact_stop), "opened": now_ms(), "signal_bar": bar_t,
           "best": entry, "paper": ex.paper, **meta}
    with st.lock:
        ctx.pos[sym] = rec
        st.save()
    log("entry", strategy=strategy, symbol=sym, side="LONG" if side == 1 else "SHORT", entry=entry, qty=qty_f, stop=exact_stop, target=exact_tp,
        risk_usd=round(rec["risk_usd"], 2), equity=round(gov.equity, 2), link=link, **meta)
    journal(ctx, strategy, sym, side, "taken", "", entry=entry)
    gov.snapshot["positions"][sym] = pos       # keep this cycle's view current
    return rec


def exit_position(ctx, sym, how):
    """Close at market (reduce-only).  The reconcile pass books the result when the position is gone."""
    p = ctx.pos.get(sym)
    if not p:
        return False
    p["exit_how"] = how
    ok = ctx.ex.close(sym, p["side"], p["qty"])
    ctx.log("exit_order", strategy=p["strategy"], symbol=sym, how=how, ok=ok)
    return ok


def record_trade(state, rec):
    """Lifetime aggregates per strategy / account / group (the history list is capped, these are not)."""
    if rec.get("R_net") is None:
        return
    key = f"{rec['strategy']}|{'paper' if rec.get('paper') else 'live'}|{rec.get('group') or '-'}"
    with state.lock:
        a = state["agg"].setdefault(key, {"n": 0, "wins": 0, "sum": 0.0, "sum2": 0.0})
        a["n"] += 1
        a["wins"] += 1 if rec["R_net"] > 0 else 0
        a["sum"] += rec["R_net"]
        a["sum2"] += rec["R_net"] ** 2


def _book(ctx, p, exit_px, pnl_usd, how):
    R_gross = p["side"] * (exit_px - p["entry"]) / p["risk"] if exit_px else None
    rec = {"strategy": p["strategy"], "sym": p["sym"], "side": p["side"], "entry": p["entry"], "exit": exit_px, "qty": p["qty"],
           "R_gross": round(R_gross, 3) if R_gross is not None else None, "pnl_usd": round(pnl_usd, 4) if pnl_usd is not None else None,
           "R_net": round(pnl_usd / p["risk_usd"], 3) if (pnl_usd is not None and p["risk_usd"]) else None,
           "held_h": round((now_ms() - p["opened"]) / 3_600_000, 2), "how": how, "opened": p["opened"], "closed": now_ms(),
           "rules": p.get("rules"), "group": p.get("group"), "paper": p.get("paper", False)}
    with ctx.state.lock:
        ctx.state["history"].append(rec)
        del ctx.state["history"][:-300]      # the full record is in the log file
    record_trade(ctx.state, rec)
    ctx.log("exit", **rec)
    return rec


def reconcile(ctx):
    """Compare our records with the exchange: book stop/target exits, notice outside changes."""
    snap = ctx.gov.snapshot
    if not snap or not snap["ok"]:
        return
    now = now_ms()
    for sym, p in list(ctx.pos.items()):
        live = snap["positions"].get(sym)
        if live is None:
            p.setdefault("gone_since", now)
            rows = ctx.ex.closed_pnl(sym, p["opened"] - 60_000)
            if not rows and now - p["gone_since"] < 3 * 60_000:
                continue                                 # closed-pnl lags the fill by a few seconds
            if rows:
                last = max(rows, key=lambda r: r["ts"])
                exit_px, pnl = last["exit"], sum(r["pnl"] for r in rows)
                how = p.get("exit_how") or _classify(p, exit_px)
            else:
                exit_px, pnl, how = None, None, p.get("exit_how") or "closed (no record found)"
            _book(ctx, p, exit_px, pnl, how)
            with ctx.state.lock:
                ctx.pos.pop(sym, None)
                ctx.state.save()
        elif live["side"] != p["side"] or abs(live["size"] - p["qty"]) > max(1e-9, p["qty"] * 1e-6):
            ctx.log("external_change", symbol=sym, strategy=p["strategy"], expected={"side": p["side"], "qty": p["qty"]},
                    found={"side": live["side"], "size": live["size"]}, action="stopped managing this position")
            with ctx.state.lock:
                ctx.pos.pop(sym, None)
                ctx.state.save()
        else:
            p.pop("gone_since", None)


def _classify(p, exit_px):
    near = lambda a, b: a is not None and abs(a - b) <= 0.15 * p["risk"]
    if p.get("target") and near(p["target"], exit_px):
        return "target"
    if near(p["stop"], exit_px):
        if p["strategy"] == "trend4h" and p["side"] * (p["stop"] - p["entry"]) > 0:
            return "trailing stop"
        return "stop"
    return "stop/other"

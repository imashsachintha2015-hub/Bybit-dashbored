"""What actually happened, summarised for the supervisor.

The supervisor was stateless: every call re-read one candidate cold, with no
knowledge of how similar setups had gone or whether its own past verdicts were
any good. It could repeat the same mistake indefinitely and never know.

This assembles the record it was missing, from data the system already keeps:
closed trades (trade_stats) and the suggestion log with its settled shadow
outcomes (signal_log). Three things go in, in descending order of usefulness:

 1. The supervisor's OWN calibration -- did trades it confirmed do better than
    trades it downgraded? If they did not, its verdicts carry no information
    and it should know that about itself.
 2. How this specific setup type has actually performed.
 3. The most recent losses on this symbol, as concrete cases rather than rates.

Everything is capped and rounded: this rides in a prompt on every call, so it
has to stay small, and precision beyond whole percents would be false anyway at
these sample sizes. Sample sizes are always shown, because a 100% win rate on
three trades should not read like a fact.
"""
from . import signal_log as signal_log_mod
from . import trade_stats as trade_stats_mod

MIN_SAMPLE = 5          # below this, report the count and draw no conclusion
RECENT_LOSSES = 3


def _rate(rows):
    if not rows:
        return None
    wins = sum(1 for r in rows if (r.get("r_multiple") or 0) > 0)
    rs = [r.get("r_multiple") or 0 for r in rows]
    return {"n": len(rows), "wr": round(100 * wins / len(rows)), "avg_r": round(sum(rs) / len(rs), 2)}


def build(symbol, setup):
    """Returns a short plain-text block, or '' when there is nothing to say."""
    lines = []

    try:
        history = trade_stats_mod.load().get("trade_history", []) or []
    except Exception:
        history = []
    try:
        signals = signal_log_mod.load().get("signals", []) or []
    except Exception:
        signals = []

    # 1. Is the supervisor's own opinion worth anything so far?
    by_verdict = {"CONFIRM": [], "DOWNGRADE": [], "VETO": []}
    for row in signals:
        v = ((row.get("supervisor") or {}).get("verdict") or "").upper()
        out = row.get("shadow_outcome")
        if v in by_verdict and out in ("WIN", "LOSS"):
            by_verdict[v].append(1 if out == "WIN" else 0)
    scored = {k: v for k, v in by_verdict.items() if v}
    if scored:
        parts = [f"{k} {round(100*sum(v)/len(v))}% (n={len(v)})" for k, v in scored.items()]
        lines.append("Your own past verdicts, by what the setup then did: " + ", ".join(parts))
        c, d = by_verdict["CONFIRM"], by_verdict["DOWNGRADE"] + by_verdict["VETO"]
        if len(c) >= MIN_SAMPLE and len(d) >= MIN_SAMPLE:
            cw, dw = sum(c) / len(c), sum(d) / len(d)
            if cw <= dw:
                lines.append("NOTE: setups you confirmed have NOT done better than ones you doubted. "
                             "Treat your prior confidence with suspicion here.")

    # 2. How this setup type has actually gone.
    same = [t for t in history if t.get("setup_type") == setup]
    st = _rate(same)
    if st:
        if st["n"] >= MIN_SAMPLE:
            lines.append(f"{setup} history: {st['n']} closed trades, {st['wr']}% won, average {st['avg_r']}R")
        else:
            lines.append(f"{setup} history: only {st['n']} closed trades so far — too few to draw on")
    overall = _rate(history)
    if overall and overall["n"] >= MIN_SAMPLE:
        lines.append(f"All setups: {overall['n']} closed trades, {overall['wr']}% won, average {overall['avg_r']}R")

    # 3. Concrete recent failures on this symbol beat any aggregate.
    losses = [t for t in history if t.get("symbol") == symbol and (t.get("r_multiple") or 0) <= 0]
    if losses:
        recent = losses[:RECENT_LOSSES]
        desc = "; ".join(
            f"{t.get('setup_type') or '?'} {t.get('side') or ''} exited {t.get('exit_reason') or '?'} at {t.get('r_multiple')}R"
            for t in recent
        )
        lines.append(f"Recent losses on {symbol}: {desc}")

    return "\n".join(f"- {l}" for l in lines)

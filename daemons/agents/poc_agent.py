"""
POC Pathfinder Agent — Volume Profile POC Obstruction Analysis
CME-X5 Model B Agent Layer

Analyzes the Volume Profile Point of Control (POC) position relative
to the trade direction. POC acts as a price "magnet" — price tends to
gravitate toward and consolidate around POC. If POC sits between entry
and TP, the trade faces a consolidation barrier.

IMPORTANT — Mean-Reversion Magnet Rule:
When price is significantly extended away from POC (>1.5% below POC for
a SHORT, or >1.5% above POC for a LONG), the POC acts as a MAGNET pulling
price back, not a resistive cap. Trades in extension from value have high
mean-reversion risk and must be penalised accordingly.

Weight in Confluence Scorer: 30% (volume-based consolidation analysis)
"""


class POCPathfinderAgent:
    """
    Checks whether the Volume Profile POC obstructs the trade's
    profit path, or whether the entry is too extended from value
    (creating mean-reversion risk toward the POC).
    """

    # If entry is this % or more away from POC in the wrong direction,
    # classify the POC as a mean-reversion magnet and penalise the trade.
    EXTENSION_WARN_PCT = 0.010   # 1.0% — moderate warning
    EXTENSION_FATAL_PCT = 0.020  # 2.0% — reject (POC is a strong mean-rev magnet)

    def __init__(self):
        self.name = "poc"

    def analyze(self, symbol, signal, vp_data):
        """
        Analyze POC impact on a given signal.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            signal: Signal dict with entry_p, stop_p, target_p, direction
            vp_data: Volume profile dict with poc, val, vah (or None)

        Returns:
            dict: {score: 0.0-1.0, adjustment: None|dict, reason: str}
        """
        if not vp_data or "poc" not in vp_data:
            return {
                "score": 0.8,
                "adjustment": None,
                "reason": "No VP data available — defaulting to neutral pass"
            }

        poc = vp_data["poc"]
        val = vp_data.get("val", 0)
        vah = vp_data.get("vah", 0)
        entry_p = signal["entry_p"]
        target_p = signal["target_p"]
        stop_p = signal["stop_p"]
        direction = signal["direction"]
        sl_dist = abs(entry_p - stop_p)

        # ── FIX: Mean-Reversion Magnet Check ─────────────────────────────
        # If a SHORT entry is significantly BELOW the POC, the POC is a
        # powerful upward magnet — price tends to snap back toward value,
        # not continue lower. Vice-versa for LONGs above the POC.
        if direction == "SHORT":
            poc_dist_pct = (poc - entry_p) / max(poc, 1e-8)  # positive = entry below POC
            if poc_dist_pct >= self.EXTENSION_FATAL_PCT:
                return {
                    "score": 0.0,
                    "adjustment": None,
                    "reason": (
                        f"MEAN-REVERSION FATAL: SHORT entry {entry_p:.4f} is {poc_dist_pct*100:.1f}% "
                        f"BELOW POC at {poc:.4f}. POC is a strong upward magnet — shorting "
                        f"in deep oversold extension is extremely high-risk. REJECT"
                    )
                }
            if poc_dist_pct >= self.EXTENSION_WARN_PCT:
                return {
                    "score": 0.25,
                    "adjustment": None,
                    "reason": (
                        f"MEAN-REVERSION RISK: SHORT entry {entry_p:.4f} is {poc_dist_pct*100:.1f}% "
                        f"below POC at {poc:.4f}. High mean-reversion risk toward value"
                    )
                }

        if direction == "LONG":
            poc_dist_pct = (entry_p - poc) / max(poc, 1e-8)  # positive = entry above POC
            if poc_dist_pct >= self.EXTENSION_FATAL_PCT:
                return {
                    "score": 0.0,
                    "adjustment": None,
                    "reason": (
                        f"MEAN-REVERSION FATAL: LONG entry {entry_p:.4f} is {poc_dist_pct*100:.1f}% "
                        f"ABOVE POC at {poc:.4f}. POC is a strong downward magnet — buying "
                        f"in deep overbought extension is extremely high-risk. REJECT"
                    )
                }
            if poc_dist_pct >= self.EXTENSION_WARN_PCT:
                return {
                    "score": 0.25,
                    "adjustment": None,
                    "reason": (
                        f"MEAN-REVERSION RISK: LONG entry {entry_p:.4f} is {poc_dist_pct*100:.1f}% "
                        f"above POC at {poc:.4f}. High mean-reversion risk toward value"
                    )
                }

        # ── Check POC position relative to trade ──────────────────────────
        poc_between = False
        if direction == "LONG" and entry_p < poc < target_p:
            poc_between = True
        elif direction == "SHORT" and target_p < poc < entry_p:
            poc_between = True

        if poc_between:
            # POC is in the path — consolidation barrier expected
            poc_margin = 0.002  # 20 bps before POC
            if direction == "LONG":
                adjusted_tp = poc * (1 - poc_margin)
            else:
                adjusted_tp = poc * (1 + poc_margin)

            new_reward = abs(adjusted_tp - entry_p)

            # If adjusted TP gives < 0.8R, REJECT (not worth the risk)
            if new_reward < sl_dist * 0.8:
                return {
                    "score": 0.15,
                    "adjustment": None,
                    "reason": (
                        f"POC at {poc:.4f} blocks path. "
                        f"Adjusted RRR={new_reward / sl_dist:.2f} < 0.8 — too risky"
                    )
                }

            # Viable but reduced — moderate penalty
            new_rrr = new_reward / sl_dist
            score = min(0.55, 0.25 + new_rrr * 0.15)
            return {
                "score": round(score, 2),
                "adjustment": {"target_p": round(adjusted_tp, 8)},
                "reason": (
                    f"POC at {poc:.4f} between entry and TP. "
                    f"TP tightened to {adjusted_tp:.4f} (RRR={new_rrr:.2f})"
                )
            }

        # ── POC behind entry (supportive) ─────────────────────────────────
        # If POC is behind us, it acts as magnetic support
        if direction == "LONG" and poc <= entry_p:
            # POC below entry for a LONG = supportive base
            dist_pct = (entry_p - poc) / entry_p
            if dist_pct < 0.005:
                # Very close POC support — strong
                return {
                    "score": 1.0,
                    "adjustment": None,
                    "reason": f"POC at {poc:.4f} just below entry — strong magnetic support for LONG"
                }
            return {
                "score": 0.9,
                "adjustment": None,
                "reason": f"POC at {poc:.4f} below entry — supportive base for LONG"
            }

        if direction == "SHORT" and poc >= entry_p:
            dist_pct = (poc - entry_p) / entry_p
            if dist_pct < 0.005:
                return {
                    "score": 1.0,
                    "adjustment": None,
                    "reason": f"POC at {poc:.4f} just above entry — strong magnetic resistance for SHORT"
                }
            return {
                "score": 0.9,
                "adjustment": None,
                "reason": f"POC at {poc:.4f} above entry — resistive cap for SHORT"
            }

        # ── Value Area Analysis ───────────────────────────────────────────
        # Check if entry is inside the Value Area (VA)
        inside_va = val <= entry_p <= vah if val and vah else False
        if inside_va:
            # Trading from inside VA — less directional conviction
            return {
                "score": 0.65,
                "adjustment": None,
                "reason": (
                    f"Entry inside Value Area ({val:.4f}-{vah:.4f}). "
                    f"POC at {poc:.4f} — lower directional conviction"
                )
            }

        # POC is beyond TP — no obstruction, slight tailwind
        return {
            "score": 0.85,
            "adjustment": None,
            "reason": f"POC at {poc:.4f} beyond TP — no obstruction"
        }

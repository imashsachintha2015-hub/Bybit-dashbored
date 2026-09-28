"""
FVG Impact Agent — Fair Value Gap Analysis
CME-X5 Model B Agent Layer

Detects unfilled Fair Value Gaps (FVGs) in the kline data and assesses
their impact on the trade:

- Aligned FVGs (bullish FVG for LONG, bearish FVG for SHORT) between
  entry and TP act as acceleration zones — price rushes to fill them.
- Opposing FVGs between entry and TP act as potential reversal zones.
- FVGs behind the stop loss are protective (not penalized).

Weight in Confluence Scorer: 30% (momentum/gap dynamics)
"""


class FVGImpactAgent:
    """
    Scans price bars for Fair Value Gaps and evaluates their
    alignment with the proposed trade direction.
    """

    def __init__(self):
        self.name = "fvg"

    # ── FVG Detection ─────────────────────────────────────────────────────
    @staticmethod
    def detect_fvgs(bars, lookback=30):
        """
        Detect Fair Value Gaps in the most recent `lookback` bars.

        Correct FVG definitions (3-candle pattern: bar i-2, i-1, i):
          - Bullish FVG (demand / gap-up imbalance):
              bar[i-2].high < bar[i].low
              A rapid upward move left a gap below — unfilled demand zone.
              Zone: bottom=bar[i-2].high, top=bar[i].low
          - Bearish FVG (supply / gap-down imbalance):
              bar[i-2].low > bar[i].high
              A rapid downward move left a gap above — unfilled supply zone.
              Zone: bottom=bar[i].high, top=bar[i-2].low

        Check fill status: if any subsequent bar's range overlaps
        the FVG zone, it's considered filled.

        Returns:
            list of dicts: [{'type': 'BULLISH'|'BEARISH', 'top': float,
                             'bottom': float, 'mid': float, 'filled': bool,
                             'bar_index': int}]
        """
        fvgs = []
        n = len(bars)
        if n < 3:
            return fvgs

        scan_start = max(2, n - lookback)
        for i in range(scan_start, n):
            bar_2ago = bars[i - 2]
            bar_now = bars[i]

            # ── Bullish FVG: gap-up imbalance (demand zone below current price) ──
            # bar[i-2].high < bar[i].low  →  rapid rally left unfilled demand gap
            if bar_2ago["high"] < bar_now["low"]:
                bottom = bar_2ago["high"]
                top = bar_now["low"]
                mid = (top + bottom) / 2.0

                # Check if filled by subsequent bars
                filled = False
                for j in range(i + 1, n):
                    if bars[j]["low"] <= top and bars[j]["high"] >= bottom:
                        filled = True
                        break

                fvgs.append({
                    "type": "BULLISH",
                    "top": top,
                    "bottom": bottom,
                    "mid": mid,
                    "filled": filled,
                    "bar_index": i
                })

            # ── Bearish FVG: gap-down imbalance (supply zone above current price) ──
            # bar[i-2].low > bar[i].high  →  rapid sell-off left unfilled supply gap
            if bar_2ago["low"] > bar_now["high"]:
                top = bar_2ago["low"]
                bottom = bar_now["high"]
                mid = (top + bottom) / 2.0

                filled = False
                for j in range(i + 1, n):
                    if bars[j]["low"] <= top and bars[j]["high"] >= bottom:
                        filled = True
                        break

                fvgs.append({
                    "type": "BEARISH",
                    "top": top,
                    "bottom": bottom,
                    "mid": mid,
                    "filled": filled,
                    "bar_index": i
                })

        return fvgs

    # ── Main Analysis ─────────────────────────────────────────────────────
    def analyze(self, symbol, signal, bars):
        """
        Analyze FVG impact on a given signal.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            signal: Signal dict with entry_p, stop_p, target_p, direction
            bars:   15m kline bars (list of dicts)

        Returns:
            dict: {score: 0.0-1.0, adjustment: None|dict, reason: str}
        """
        entry_p = signal["entry_p"]
        target_p = signal["target_p"]
        stop_p = signal["stop_p"]
        direction = signal["direction"]

        fvgs = self.detect_fvgs(bars, lookback=30)

        # Filter to only unfilled FVGs (filled ones are inert)
        unfilled = [f for f in fvgs if not f["filled"]]

        if not unfilled:
            return {
                "score": 0.7,
                "adjustment": None,
                "reason": "No unfilled FVGs detected — neutral"
            }

        # Classify FVGs by alignment with trade direction
        aligned_fvgs = []
        opposing_fvgs = []
        protective_fvgs = []

        path_min = min(entry_p, target_p)
        path_max = max(entry_p, target_p)

        for fvg in unfilled:
            fvg_mid = fvg["mid"]

            # Is FVG in the profit path?
            in_path = path_min < fvg_mid < path_max

            if in_path:
                # Aligned: Bullish FVG in path for LONG (demand pulls price up)
                # Aligned: Bearish FVG in path for SHORT (supply pulls price down)
                if direction == "LONG" and fvg["type"] == "BULLISH":
                    aligned_fvgs.append(fvg)
                elif direction == "SHORT" and fvg["type"] == "BEARISH":
                    aligned_fvgs.append(fvg)
                else:
                    # Opposing: Bearish FVG (supply) in LONG path = headwind
                    # Opposing: Bullish FVG (demand) in SHORT path = headwind
                    opposing_fvgs.append(fvg)
            else:
                # Check if FVG is behind stop (protective)
                if direction == "LONG" and fvg_mid < stop_p:
                    protective_fvgs.append(fvg)
                elif direction == "SHORT" and fvg_mid > stop_p:
                    protective_fvgs.append(fvg)

        # ── Scoring ───────────────────────────────────────────────────────
        score = 0.7  # baseline
        reasons = []

        # Aligned FVGs = tailwind (price accelerates to fill them)
        if aligned_fvgs:
            bonus = min(0.25, len(aligned_fvgs) * 0.10)
            score += bonus
            reasons.append(
                f"{len(aligned_fvgs)} aligned FVG(s) — "
                f"price acceleration expected toward fill"
            )

        # Opposing FVGs = headwind (potential reversal zones)
        if opposing_fvgs:
            penalty = min(0.35, len(opposing_fvgs) * 0.15)
            score -= penalty

            nearest_opp = min(opposing_fvgs, key=lambda f: abs(f["mid"] - entry_p))
            dist_pct = abs(nearest_opp["mid"] - entry_p) / max(entry_p, 1e-8)

            reasons.append(
                f"{len(opposing_fvgs)} opposing FVG(s) — "
                f"nearest at {nearest_opp['mid']:.4f} may cause reversal"
            )

            # Extra penalty if opposing FVG is very close to entry
            if dist_pct < 0.003:
                score -= 0.15
                reasons.append(
                    f"WARNING: Opposing FVG within 30bps of entry ({dist_pct * 100:.1f}%)"
                )

        # Protective FVGs behind SL = slight bonus
        if protective_fvgs:
            bonus = min(0.1, len(protective_fvgs) * 0.05)
            score += bonus
            reasons.append(
                f"{len(protective_fvgs)} protective FVG(s) behind SL — additional support"
            )

        # Clamp score
        score = max(0.0, min(1.0, score))
        reason = " | ".join(reasons) if reasons else "FVG analysis neutral"

        return {
            "score": round(score, 2),
            "adjustment": None,
            "reason": reason
        }

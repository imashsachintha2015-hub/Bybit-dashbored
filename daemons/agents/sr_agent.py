"""
S/R Agent — Support/Resistance Guardian
CME-X5 Model B Agent Layer

Scans multi-timeframe structure (1H + 4H klines) to identify major
swing-high/swing-low levels. If TP is blocked by a major S/R level,
the agent either adjusts TP to just before the level or rejects the
trade entirely if the adjusted RRR is insufficient.

Weight in Confluence Scorer: 40% (structural — most important)
"""


class SRAgent:
    """
    Detects swing-high and swing-low pivots on 1H and 4H timeframes,
    then checks whether the trade's target price path is obstructed
    by any of those levels.
    """

    def __init__(self):
        self.name = "sr"

    # ── Swing-Level Detection ─────────────────────────────────────────────
    @staticmethod
    def _detect_swing_levels(bars, lookback=50, sensitivity=3):
        """
        Identify swing highs and swing lows using a left-right window.
        A swing high is a bar whose high is the highest of `sensitivity`
        bars on either side. Same logic (inverted) for swing lows.

        Returns list of dicts: [{'price': float, 'type': 'HIGH'|'LOW', 'strength': int}]
        """
        levels = []
        if len(bars) < sensitivity * 2 + 1:
            return levels

        end = min(lookback, len(bars))
        for i in range(sensitivity, end - sensitivity):
            bar = bars[i]

            # Swing High
            is_high = True
            for j in range(1, sensitivity + 1):
                if bars[i - j]["high"] >= bar["high"] or bars[i + j]["high"] >= bar["high"]:
                    is_high = False
                    break
            if is_high:
                # Strength = how many nearby bars it dominates (more = stronger level)
                strength = 0
                for j in range(1, min(sensitivity * 2, end - i)):
                    if bars[i + j]["high"] < bar["high"]:
                        strength += 1
                    else:
                        break
                levels.append({"price": bar["high"], "type": "HIGH", "strength": strength})

            # Swing Low
            is_low = True
            for j in range(1, sensitivity + 1):
                if bars[i - j]["low"] <= bar["low"] or bars[i + j]["low"] <= bar["low"]:
                    is_low = False
                    break
            if is_low:
                strength = 0
                for j in range(1, min(sensitivity * 2, end - i)):
                    if bars[i + j]["low"] > bar["low"]:
                        strength += 1
                    else:
                        break
                levels.append({"price": bar["low"], "type": "LOW", "strength": strength})

        return levels

    @staticmethod
    def _cluster_levels(levels, tolerance_pct=0.003):
        """
        Merge nearby levels into clusters. If two levels are within
        tolerance_pct of each other, keep the stronger one.
        """
        if not levels:
            return []
        sorted_levels = sorted(levels, key=lambda l: l["price"])
        clustered = [sorted_levels[0]]
        for lvl in sorted_levels[1:]:
            prev = clustered[-1]
            if abs(lvl["price"] - prev["price"]) / max(prev["price"], 1e-8) < tolerance_pct:
                # Merge — keep stronger
                if lvl["strength"] > prev["strength"]:
                    clustered[-1] = lvl
            else:
                clustered.append(lvl)
        return clustered

    # ── Main Analysis ─────────────────────────────────────────────────────
    def analyze(self, symbol, signal, k1h, k4h):
        """
        Analyze S/R obstruction for a given signal.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            signal: Signal dict with entry_p, stop_p, target_p, direction
            k1h:    1H kline bars (list of dicts)
            k4h:    4H kline bars (list of dicts)

        Returns:
            dict: {score: 0.0-1.0, adjustment: None|dict, reason: str}
        """
        entry_p = signal["entry_p"]
        target_p = signal["target_p"]
        stop_p = signal["stop_p"]
        direction = signal["direction"]
        sl_dist = abs(entry_p - stop_p)

        # Detect levels on both timeframes
        major_levels = self._detect_swing_levels(k4h, lookback=50, sensitivity=3) if len(k4h) >= 7 else []
        minor_levels = self._detect_swing_levels(k1h, lookback=30, sensitivity=2) if len(k1h) >= 5 else []

        # Cluster to remove duplicates
        all_levels = self._cluster_levels(major_levels + minor_levels)

        if not all_levels:
            return {
                "score": 0.85,
                "adjustment": None,
                "reason": "No swing levels detected — defaulting to pass"
            }

        # Find levels blocking the path from entry to TP
        blocking_major = []
        blocking_minor = []
        for lvl in all_levels:
            price = lvl["price"]
            in_path = False
            if direction == "LONG" and entry_p < price < target_p:
                in_path = True
            elif direction == "SHORT" and target_p < price < entry_p:
                in_path = True

            if in_path:
                # Check if it's from 4H data (major) or 1H (minor)
                is_major = lvl in major_levels or lvl["strength"] >= 3
                if is_major:
                    blocking_major.append(lvl)
                else:
                    blocking_minor.append(lvl)

        # No blockers — clear path
        if not blocking_major and not blocking_minor:
            return {
                "score": 1.0,
                "adjustment": None,
                "reason": "Clear path to TP — no S/R obstruction"
            }

        # Minor-only blockers — slight penalty, no adjustment
        if not blocking_major and blocking_minor:
            penalty = min(0.2, len(blocking_minor) * 0.05)
            return {
                "score": round(0.85 - penalty, 2),
                "adjustment": None,
                "reason": f"{len(blocking_minor)} minor S/R level(s) in path (1H swing pivots)"
            }

        # Major blockers — adjust TP or reject
        nearest = min(blocking_major, key=lambda l: abs(l["price"] - entry_p))
        margin = 0.002  # 20 bps before the level
        if direction == "LONG":
            adjusted_tp = nearest["price"] * (1 - margin)
        else:
            adjusted_tp = nearest["price"] * (1 + margin)

        new_reward = abs(adjusted_tp - entry_p)

        # If adjusted TP gives < 1.0R, REJECT
        if new_reward < sl_dist:
            return {
                "score": 0.0,
                "adjustment": None,
                "reason": (
                    f"Major S/R at {nearest['price']:.4f} blocks TP. "
                    f"Adjusted RRR={new_reward / sl_dist:.2f} < 1.0 — FATAL"
                )
            }

        # Adjusted TP is viable but reduced
        new_rrr = new_reward / sl_dist
        score = min(0.65, 0.3 + new_rrr * 0.15)
        return {
            "score": round(score, 2),
            "adjustment": {"target_p": round(adjusted_tp, 8)},
            "reason": (
                f"Major S/R at {nearest['price']:.4f} in path. "
                f"TP adjusted to {adjusted_tp:.4f} (RRR={new_rrr:.2f})"
            )
        }

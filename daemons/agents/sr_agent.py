"""
S/R Agent — Support/Resistance Guardian
CME-X5 Model B Agent Layer

Scans multi-timeframe structure (4H macro + 1H swing + 15M intraday klines)
to identify major swing-high and swing-low levels.

Functions:
1. Target Obstruction Protection: If TP is blocked by a major S/R level,
   adjusts TP to just before the level (20 bps buffer) so price can hit it
   and take profit. If adjusted RRR is < 1.0, rejects the trade entirely (FATAL).
2. Entry Proximity Check: Rejects trades where the entry is placed within 0.4%
   of a major opposing S/R level (e.g., shorting directly into major support,
   or buying directly under a resistance ceiling).

Weight in Confluence Scorer: 40% (structural — most important)
"""


class SRAgent:
    """
    Detects swing-high and swing-low pivots on 4H, 1H, and 15M timeframes,
    then checks whether the trade's target price path is obstructed
    by any of those levels, and whether the entry itself is sitting
    on top of a major opposing S/R level.
    """

    # Entry must not be within this % of a major opposing level
    ENTRY_PROXIMITY_PCT = 0.004  # 40 bps

    def __init__(self):
        self.name = "sr"

    # ── Swing-Level Detection ─────────────────────────────────────────────
    @staticmethod
    def _detect_swing_levels(bars, lookback=50, sensitivity=2):
        """
        Identify swing highs and swing lows using a left-right window.
        Correctly scans the MOST RECENT `lookback` bars up to current price.

        A swing high is a bar whose high is >= high of `sensitivity`
        bars on either side. Same logic (inverted) for swing lows.

        Returns list of dicts: [{'price': float, 'type': 'HIGH'|'LOW', 'strength': int}]
        """
        levels = []
        n = len(bars)
        if n < sensitivity * 2 + 1:
            return levels

        # Scan the most recent lookback bars up to n - sensitivity
        start = max(sensitivity, n - lookback)
        end = n - sensitivity
        for i in range(start, end):
            bar = bars[i]

            # Swing High
            is_high = True
            for j in range(1, sensitivity + 1):
                if bars[i - j]["high"] > bar["high"] or bars[i + j]["high"] > bar["high"]:
                    is_high = False
                    break
            if is_high:
                # Strength = how many subsequent bars remained below this high
                strength = 0
                for j in range(1, min(sensitivity * 2, n - i)):
                    if bars[i + j]["high"] < bar["high"]:
                        strength += 1
                    else:
                        break
                levels.append({"price": bar["high"], "type": "HIGH", "strength": strength})

            # Swing Low
            is_low = True
            for j in range(1, sensitivity + 1):
                if bars[i - j]["low"] < bar["low"] or bars[i + j]["low"] < bar["low"]:
                    is_low = False
                    break
            if is_low:
                # Strength = how many subsequent bars remained above this low
                strength = 0
                for j in range(1, min(sensitivity * 2, n - i)):
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
                if lvl["strength"] > prev["strength"]:
                    clustered[-1] = lvl
            else:
                clustered.append(lvl)
        return clustered

    # ── Main Analysis ─────────────────────────────────────────────────────
    def analyze(self, symbol, signal, k1h, k4h, k15=None):
        """
        Analyze S/R obstruction for a given signal across 4H, 1H, and 15M timeframes.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            signal: Signal dict with entry_p, stop_p, target_p, direction
            k1h:    1H kline bars (list of dicts)
            k4h:    4H kline bars (list of dicts)
            k15:    15M kline bars (list of dicts, optional but recommended)

        Returns:
            dict: {score: 0.0-1.0, adjustment: None|dict, reason: str}
        """
        entry_p = signal["entry_p"]
        target_p = signal["target_p"]
        stop_p = signal["stop_p"]
        direction = signal["direction"]
        sl_dist = abs(entry_p - stop_p)

        # Detect levels across all available timeframes
        major_levels = self._detect_swing_levels(k4h, lookback=50, sensitivity=3) if k4h and len(k4h) >= 7 else []
        inter_levels = self._detect_swing_levels(k1h, lookback=40, sensitivity=2) if k1h and len(k1h) >= 5 else []
        minor_levels = self._detect_swing_levels(k15, lookback=50, sensitivity=2) if k15 and len(k15) >= 5 else []

        all_detected = major_levels + inter_levels + minor_levels
        all_levels = self._cluster_levels(all_detected)

        if not all_levels:
            return {
                "score": 0.85,
                "adjustment": None,
                "reason": "No swing levels detected — defaulting to pass"
            }

        # ── Entry Proximity Check ─────────────────────────────────────────
        # Reject if entry is placed directly on top of a major opposing S/R level.
        # LONG: entry must not be within ENTRY_PROXIMITY_PCT of a major SWING HIGH (resistance)
        # SHORT: entry must not be within ENTRY_PROXIMITY_PCT of a major SWING LOW (support)
        for lvl in all_levels:
            price = lvl["price"]
            is_major = (lvl in major_levels) or (lvl in inter_levels) or (lvl.get("strength", 0) >= 3)
            if not is_major:
                continue

            dist_pct = abs(entry_p - price) / max(price, 1e-8)
            if dist_pct > self.ENTRY_PROXIMITY_PCT:
                continue

            if direction == "LONG" and lvl["type"] == "HIGH":
                return {
                    "score": 0.0,
                    "adjustment": None,
                    "reason": (
                        f"ENTRY PROXIMITY FATAL: LONG entry {entry_p:.4f} is within "
                        f"{dist_pct * 100:.1f}% of major resistance at {price:.4f}. "
                        f"Buying into a ceiling — REJECT"
                    )
                }

            if direction == "SHORT" and lvl["type"] == "LOW":
                return {
                    "score": 0.0,
                    "adjustment": None,
                    "reason": (
                        f"ENTRY PROXIMITY FATAL: SHORT entry {entry_p:.4f} is within "
                        f"{dist_pct * 100:.1f}% of major support at {price:.4f}. "
                        f"Shorting into a floor — REJECT"
                    )
                }

        # ── Find levels blocking the path from entry to TP ─────────────────
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
                is_major = (lvl in major_levels) or (lvl in inter_levels) or (lvl.get("strength", 0) >= 3)
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
                "reason": f"{len(blocking_minor)} minor intraday S/R level(s) in path"
            }

        # Major blockers — adjust TP to just before the nearest blocking level
        # For a SHORT: nearest support below entry (highest price among blockers)
        # For a LONG:  nearest resistance above entry (lowest price among blockers)
        nearest = min(blocking_major, key=lambda l: abs(l["price"] - entry_p))
        margin = 0.002  # 20 bps buffer before the level
        if direction == "LONG":
            adjusted_tp = nearest["price"] * (1 - margin)
        else:
            adjusted_tp = nearest["price"] * (1 + margin)

        new_reward = abs(adjusted_tp - entry_p)

        # If adjusted TP gives < 1.0R, REJECT (insufficient reward for the risk)
        if new_reward < sl_dist:
            return {
                "score": 0.0,
                "adjustment": None,
                "reason": (
                    f"Major S/R at {nearest['price']:.4f} blocks TP. "
                    f"Adjusted RRR={new_reward / sl_dist:.2f} < 1.0 — FATAL"
                )
            }

        # Adjusted TP is viable — reward trade with adjusted TP
        new_rrr = new_reward / sl_dist
        score = min(0.65, 0.3 + new_rrr * 0.15)
        return {
            "score": round(score, 2),
            "adjustment": {"target_p": round(adjusted_tp, 8)},
            "reason": (
                f"Major S/R at {nearest['price']:.4f} ({nearest['type']}) in path. "
                f"TP adjusted to {adjusted_tp:.4f} (RRR={new_rrr:.2f})"
            )
        }

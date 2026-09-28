"""
Confluence Scorer — Agent Aggregator
CME-X5 Model B Agent Layer

Combines all agent scores into a single GO/ADJUST/REJECT decision
using weighted importance:

  S/R Agent:       40% weight (structural — most important)
  POC Pathfinder:  30% weight (volume-based consolidation)
  FVG Impact:      30% weight (momentum/gap dynamics)

Decision Thresholds:
  >= 0.60  →  PASS    (execute trade, possibly with adjusted TP/SL)
  0.40-0.59 → ADJUST  (execute with mandatory TP/SL adjustments)
  < 0.40   →  REJECT  (skip trade — agents blocked it)
"""


class ConfluenceScorer:
    """
    Weighted aggregator for pre-trade agent intelligence.
    Merges individual agent scores and adjustments into a
    single actionable verdict.
    """

    WEIGHTS = {
        "sr": 0.40,
        "poc": 0.30,
        "fvg": 0.30,
    }

    THRESHOLD_PASS = 0.60
    THRESHOLD_ADJUST = 0.40

    def __init__(self):
        self.name = "confluence_scorer"

    def evaluate(self, agent_results):
        """
        Aggregate agent results into a single verdict.

        Args:
            agent_results: dict of {agent_name: {score, adjustment, reason}}
                           Keys must match WEIGHTS keys: 'sr', 'poc', 'fvg'

        Returns:
            dict: {
                decision:    'PASS' | 'ADJUST' | 'REJECT',
                final_score: float (0.0 - 1.0),
                adjustments: dict of merged TP/SL adjustments,
                report:      str (multi-line human-readable report),
                breakdown:   dict of {agent: weighted_score}
            }
        """
        total_score = 0.0
        adjustments = {}
        report_lines = []
        breakdown = {}

        for agent_name, weight in self.WEIGHTS.items():
            result = agent_results.get(agent_name, {"score": 0.5, "reason": "Agent not run"})
            raw_score = result.get("score", 0.5)
            weighted = raw_score * weight
            total_score += weighted
            breakdown[agent_name] = round(weighted, 3)

            icon = self._score_icon(raw_score)
            report_lines.append(
                f"  {icon} [{agent_name.upper():3s}] "
                f"raw={raw_score:.2f} x{weight:.0%} = {weighted:.3f}  "
                f"| {result.get('reason', 'N/A')}"
            )

            # Merge adjustments (later agents override earlier ones for same key)
            adj = result.get("adjustment")
            if adj and isinstance(adj, dict):
                adjustments.update(adj)

        # Decision
        if total_score >= self.THRESHOLD_PASS:
            decision = "PASS"
        elif total_score >= self.THRESHOLD_ADJUST:
            decision = "ADJUST"
        else:
            decision = "REJECT"

        # Summary line
        decision_icon = {"PASS": "GO", "ADJUST": "ADJ", "REJECT": "NO"}[decision]
        summary = (
            f"  [{decision_icon}] Final Score: {total_score:.3f} -> {decision}"
        )
        report_lines.append(summary)

        if adjustments:
            adj_str = ", ".join(f"{k}={v}" for k, v in adjustments.items())
            report_lines.append(f"  [ADJ] Adjustments: {adj_str}")

        return {
            "decision": decision,
            "final_score": round(total_score, 3),
            "adjustments": adjustments,
            "report": "\n".join(report_lines),
            "breakdown": breakdown,
        }

    @staticmethod
    def _score_icon(score):
        """Return a visual indicator for the score level."""
        if score >= 0.8:
            return "+++"
        elif score >= 0.6:
            return " + "
        elif score >= 0.4:
            return " ~ "
        elif score >= 0.2:
            return " - "
        else:
            return "---"

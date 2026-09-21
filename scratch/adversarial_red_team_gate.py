"""
Adversarial Market Agent & Red Team Veto Gate (DIV-14)
Structured Opposition Layer for MASIS Quantitative Trading.

Instead of seeking confirmation to justify a trade, the Red Team acts as the Devil's Advocate
to actively FALSIFY and VETO flawed setups before orders can reach Bybit:
1. Cost & Fee Friction Falsification (Veto if fees + slippage > 25% of expected target).
2. Bear/Bull Trap & Exhaustion Wick Detection.
3. Structural Overhang & Liquidity Wall Clearance (Minimum runway enforcement).
4. Macro Alignment & Archaeologist Anti-Pattern Enforcement.
"""

import os
import sys
import json
import time
import math
from datetime import datetime

DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DIRECTORY)

from backend_lib.measurement_journal import (
    mj,
    BYBIT_TAKER_FEE_RATE,
    DEFAULT_SLIPPAGE_RATE,
    TOTAL_FRICTION_RATE
)

class AdversarialRedTeamGate:
    def __init__(self):
        self.friction_rate = TOTAL_FRICTION_RATE  # 0.150% roundtrip (0.11% fees + 0.04% slippage)
        self.max_fee_friction_ratio = 0.25        # Fees must NOT exceed 25% of target runway

    def evaluate(self, candidate, sr_data=None, btc_macro=None, klines_15m=None):
        """
        Runs comprehensive adversarial falsification on candidate setup.
        Returns:
            {
                "passed": bool,
                "vetoed": bool,
                "code": str,             # e.g., 'CLEARED', 'VETO_FEE_FRICTION', 'VETO_TRAP_WICK', 'VETO_RUNWAY_CHOKED'
                "reason": str,
                "fee_friction_pct": float,
                "target_runway_pct": float,
                "fee_to_target_ratio": float,
                "adversarial_score": int # 0-100 (higher = safer, < 70 = vetoed)
            }
        """
        sym = candidate.get("symbol", "UNKNOWN")
        direction = str(candidate.get("direction") or "BUY").upper()
        cur_p = float(candidate.get("price") or candidate.get("entry") or 0.0)
        setup_type = candidate.get("setup") or candidate.get("setup_type") or "UNKNOWN"
        strategy_mode = candidate.get("strategy_mode", "SWING_RUNNER")

        sr = sr_data or {}
        btc = btc_macro or {}

        # ── 1. Target Runway Calculation ──────────────────────────────────
        if direction == "SELL":
            sup = float(sr.get("nearest_sup") or (cur_p * 0.96))
            target_runway_pct = round(((cur_p - sup) / cur_p) * 100.0, 2) if cur_p > 0 else 2.5
        else:
            res = float(sr.get("nearest_res") or (cur_p * 1.04))
            target_runway_pct = round(((res - cur_p) / cur_p) * 100.0, 2) if cur_p > 0 else 2.5

        # For scalps with specific tight targets, use the planned target
        if "targets" in candidate and candidate["targets"]:
            targets_val = candidate["targets"]
            planned_target = None
            if isinstance(targets_val, list) and len(targets_val) > 0:
                try:
                    planned_target = float(targets_val[0])
                except (ValueError, TypeError):
                    pass
            elif isinstance(targets_val, dict) and targets_val:
                try:
                    val = targets_val.get("tp1") or targets_val.get("target") or list(targets_val.values())[0]
                    planned_target = float(val)
                except (ValueError, TypeError, IndexError):
                    pass

            if planned_target and planned_target > 0:
                if direction == "BUY" and planned_target > cur_p:
                    target_runway_pct = min(target_runway_pct, round(((planned_target - cur_p) / cur_p) * 100.0, 2))
                elif direction == "SELL" and planned_target < cur_p:
                    target_runway_pct = min(target_runway_pct, round(((cur_p - planned_target) / cur_p) * 100.0, 2))

        # Absolute minimum target runway floor
        target_runway_pct = max(0.01, target_runway_pct)
        friction_pct = round(self.friction_rate * 100.0, 3)  # 0.150%
        fee_ratio = round(friction_pct / target_runway_pct, 3)

        # ── 2. Check 1: Fee & Slippage Friction Gate (The 25% Rule) ──────
        if fee_ratio > self.max_fee_friction_ratio:
            reason = (
                f"Taker fees + slippage ({friction_pct:.3f}%) consume {fee_ratio * 100.0:.1f}% "
                f"of target runway (+{target_runway_pct:.2f}%). Exceeds 25% fee cap! "
                f"Target must be >= {friction_pct / self.max_fee_friction_ratio:.2f}% to justify execution cost."
            )
            return {
                "passed": False,
                "vetoed": True,
                "code": "VETO_FEE_FRICTION",
                "reason": reason,
                "fee_friction_pct": friction_pct,
                "target_runway_pct": target_runway_pct,
                "fee_to_target_ratio": fee_ratio,
                "adversarial_score": 20
            }

        # ── 3. Check 2: Structural Runway Clearance ──────────────────────
        # Adaptive runway: tighter threshold in ranging/consolidating markets
        btc_5m_abs = abs(float(btc.get("btc_chg_5m") or 0.0))
        is_tight_consolidation = btc_5m_abs < 0.10  # BTC barely moving = tight range
        candidate_score = int(candidate.get("score", 0))

        if strategy_mode == "SWING_RUNNER":
            # 1.20% base; relaxes to 1.00% in tight consolidation for high-score setups
            min_runway = 1.00 if (is_tight_consolidation and candidate_score >= 80) else 1.20
        else:
            min_runway = 0.60  # Scalp mode

        if target_runway_pct < min_runway:
            obstacle = "nearest support floor" if direction == "SELL" else "nearest structural resistance"
            reason = (
                f"Runway choked: Only {target_runway_pct:.2f}% clearance to {obstacle} "
                f"(Required: >= {min_runway:.2f}%). Asymmetry is too unfavorable."
            )
            return {
                "passed": False,
                "vetoed": True,
                "code": "VETO_RUNWAY_CHOKED",
                "reason": reason,
                "fee_friction_pct": friction_pct,
                "target_runway_pct": target_runway_pct,
                "fee_to_target_ratio": fee_ratio,
                "adversarial_score": 35
            }

        # ── 4. Check 3: Exhaustion Wick / Trap Pattern Falsification ──────
        if klines_15m and len(klines_15m) >= 2:
            last_c = klines_15m[-1]
            c_high = float(last_c.get("high", 0))
            c_low = float(last_c.get("low", 0))
            c_open = float(last_c.get("open", 0))
            c_close = float(last_c.get("close", 0))
            candle_range = c_high - c_low

            if candle_range > 0:
                if direction == "BUY":
                    # Upper wick rejection (buyers trapped at top)
                    upper_wick = c_high - max(c_open, c_close)
                    wick_ratio = upper_wick / candle_range
                    if wick_ratio >= 0.55 and c_close < c_open:
                        reason = (
                            f"Bearish Trap rejection detected: 15m candle formed {wick_ratio * 100:.0f}% upper wick "
                            f"rejection into resistance. Buying here risks immediate reversal trap."
                        )
                        return {
                            "passed": False,
                            "vetoed": True,
                            "code": "VETO_BEAR_TRAP_WICK",
                            "reason": reason,
                            "fee_friction_pct": friction_pct,
                            "target_runway_pct": target_runway_pct,
                            "fee_to_target_ratio": fee_ratio,
                            "adversarial_score": 25
                        }
                else: # SELL
                    # Lower wick absorption (sellers absorbed at bottom)
                    lower_wick = min(c_open, c_close) - c_low
                    wick_ratio = lower_wick / candle_range
                    if wick_ratio >= 0.55 and c_close > c_open:
                        reason = (
                            f"Bullish Absorption detected: 15m candle formed {wick_ratio * 100:.0f}% lower wick "
                            f"absorption at support. Shorting here risks selling the exact local floor."
                        )
                        return {
                            "passed": False,
                            "vetoed": True,
                            "code": "VETO_BULL_TRAP_WICK",
                            "reason": reason,
                            "fee_friction_pct": friction_pct,
                            "target_runway_pct": target_runway_pct,
                            "fee_to_target_ratio": fee_ratio,
                            "adversarial_score": 25
                        }

        # ── 5. Check 4: Macro Bitcoin Alignment Falsification ────────────
        btc_5m = float(btc.get("btc_chg_5m") or 0.0)
        btc_regime = str(btc.get("regime") or "CONSOLIDATING")

        if direction == "BUY" and (btc_5m <= -0.20 or btc_regime in ["BTC_FLASH_CRASH", "BTC_SEVERE_FLUSH"]):
            reason = f"Adversarial Macro Veto: Altcoin Long into flushing Bitcoin ({btc_5m:+.2f}% 5m, {btc_regime}). Downside contagion hazard."
            return {
                "passed": False,
                "vetoed": True,
                "code": "VETO_BTC_MACRO_FLUSH",
                "reason": reason,
                "fee_friction_pct": friction_pct,
                "target_runway_pct": target_runway_pct,
                "fee_to_target_ratio": fee_ratio,
                "adversarial_score": 30
            }

        if direction == "SELL" and (btc_5m >= +0.30 or btc_regime == "BTC_BULL_PUMPING"):
            reason = f"Adversarial Macro Veto: Altcoin Short into surging Bitcoin ({btc_5m:+.2f}% 5m, {btc_regime}). Short squeeze hazard."
            return {
                "passed": False,
                "vetoed": True,
                "code": "VETO_BTC_MACRO_PUMP",
                "reason": reason,
                "fee_friction_pct": friction_pct,
                "target_runway_pct": target_runway_pct,
                "fee_to_target_ratio": fee_ratio,
                "adversarial_score": 30
            }

        # ── 6. Passed All Adversarial Opposition ─────────────────────────
        return {
            "passed": True,
            "vetoed": False,
            "code": "CLEARED_BY_RED_TEAM",
            "reason": f"Surpassed all adversarial checks: Fee ratio {fee_ratio * 100:.1f}% <= 25%, Runway +{target_runway_pct:.2f}%, No trap wicks.",
            "fee_friction_pct": friction_pct,
            "target_runway_pct": target_runway_pct,
            "fee_to_target_ratio": fee_ratio,
            "adversarial_score": 92
        }


# Singleton instance
red_team = AdversarialRedTeamGate()

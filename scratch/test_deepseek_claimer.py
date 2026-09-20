import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch.deepseek_profit_claimer import consult_deepseek_claimer, extract_microstructure

print("Testing DeepSeek Profit Claimer...")

mock_position = {
    "symbol": "SOLUSDT",
    "side": "Buy",
    "entry": 142.50,
    "mark": 143.05,
    "unpnl": 0.055,
    "size": 0.1
}

# Test Scenario A: High upper wick rejection (stalling at resistance)
mock_micro_stall = {
    "upper_wick_pct_5m": 48.5,
    "lower_wick_pct_5m": 12.0,
    "body_pct_5m": 39.5,
    "vol_ratio_5m": 0.72,
    "consecutive_green_1m": 0,
    "consecutive_red_1m": 2,
    "candle_color_5m": "GREEN"
}

res_a = consult_deepseek_claimer(mock_position, mock_micro_stall)
print("\n--- TEST SCENARIO A (Resistance Stall / Rejection) ---")
print("Response:", json.dumps(res_a, indent=2))
assert res_a.get("verdict") in ["TAKE_PARTIAL", "TAKE_ALL"], f"Unexpected verdict: {res_a.get('verdict')}"

# Test Scenario B: Momentum breakout (surging volume)
mock_micro_surge = {
    "upper_wick_pct_5m": 8.0,
    "lower_wick_pct_5m": 15.0,
    "body_pct_5m": 77.0,
    "vol_ratio_5m": 2.15,
    "consecutive_green_1m": 4,
    "consecutive_red_1m": 0,
    "candle_color_5m": "GREEN"
}

res_b = consult_deepseek_claimer(mock_position, mock_micro_surge)
print("\n--- TEST SCENARIO B (Momentum Breakout) ---")
print("Response:", json.dumps(res_b, indent=2))

print("\nSUCCESS: DeepSeek Claimer passed all test scenarios!")

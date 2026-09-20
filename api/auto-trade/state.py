import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib import auto_trade_state
from backend_lib.bybit_client import get_client
from backend_lib.market_knowledge import kb


class handler(JsonApiHandler):
    def do_GET(self):
        q = self._query()
        action = (q.get("_action") or [""])[0]

        if action == "target_mode":
            eq = None
            client, err = get_client()
            if not err and client:
                try:
                    wb = client.get_wallet_balance()
                    coins = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
                    usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
                    if usdt:
                        eq = float(usdt.get("equity") or 0.0)
                except Exception as e:
                    print(f"[api/auto-trade/state?_action=target_mode] balance warning: {e}")
            self._send_json(200, kb.get_target_state(current_equity=eq))
            return

        self._send_json(200, auto_trade_state.load())

    def do_POST(self):
        body = self._read_json_body()
        q = self._query()
        action = (q.get("_action") or [""])[0]

        if action == "target_mode":
            target_eq = body.get("target_equity")
            is_armed = body.get("is_armed")
            status = body.get("status")
            kb.set_target_state(target_equity=target_eq, is_armed=is_armed, status=status)

            eq = None
            client, err = get_client()
            if not err and client:
                try:
                    wb = client.get_wallet_balance()
                    coins = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
                    usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
                    if usdt:
                        eq = float(usdt.get("equity") or 0.0)
                except Exception:
                    pass
            self._send_json(200, kb.get_target_state(current_equity=eq))
            return

        try:
            state = auto_trade_state.save(
                body.get("armed"),
                body.get("riskPerTradePct"),
                body.get("sizingMode"),
                body.get("fixedUsdtSize"),
                body.get("leverage"),
                body.get("marginMode"),
                theses=body.get("theses"),
                daily_gross_target=body.get("dailyGrossTarget"),
                target_notional=body.get("targetNotional"),
                virtual_equity=body.get("virtualEquity"),
                max_concurrent_positions=body.get("maxConcurrentPositions"),
            )
            self._send_json(200, state)
        except Exception as e:
            print(f"[POST /api/auto-trade/state] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib.bybit_client import get_client
from backend_lib.market_knowledge import kb


class handler(JsonApiHandler):
    def do_GET(self):
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
                print(f"[api/agent/target-mode] balance fetch warning: {e}")

        state = kb.get_target_state(current_equity=eq)
        self._send_json(200, state)

    def do_POST(self):
        body = self._read_json_body()
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

        state = kb.get_target_state(current_equity=eq)
        self._send_json(200, state)

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_lib.bybit_client import get_client
from backend_lib.http_utils import JsonApiHandler


class handler(JsonApiHandler):
    def do_GET(self):
        client, err = get_client()
        if err:
            self._send_json(500, {"retCode": -1, "retMsg": err})
            return
        self._send_json(200, client.get_wallet_balance())

    # One-time demo-funds top-up: brings total equity to `target` (default
    # 10000 USDT) via Bybit's real demo-apply-money endpoint -- adds USDT if
    # currently below target, reduces USDT if above. Not a UI feature, just
    # a POST to hit once; see bybit_client.apply_demo_funds for the actual
    # Bybit call.
    def do_POST(self):
        body = self._read_json_body()
        try:
            client, err = get_client()
            if err:
                self._send_json(500, {"retCode": -1, "retMsg": err})
                return

            target = float(body.get("target", 10000))
            bal = client.get_wallet_balance()
            rows = bal.get("result", {}).get("list", []) or []
            before = float(rows[0].get("totalEquity", 0)) if rows else 0.0
            delta = round(target - before, 2)

            if abs(delta) < 1:
                self._send_json(200, {"before": before, "target": target, "delta": 0, "note": "Already within $1 of target; nothing applied."})
                return

            res = client.apply_demo_funds("USDT", abs(delta), reduce=delta < 0)
            self._send_json(200, {"before": before, "target": target, "delta": delta, "bybit": res})
        except Exception as e:
            print(f"[POST /api/account] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

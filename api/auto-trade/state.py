import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib import auto_trade_state


class handler(JsonApiHandler):
    def do_GET(self):
        self._send_json(200, auto_trade_state.load())

    def do_POST(self):
        body = self._read_json_body()
        try:
            state = auto_trade_state.save(
                body.get("armed"),
                body.get("riskPerTradePct"),
                body.get("sizingMode"),
                body.get("fixedUsdtSize"),
                body.get("leverage"),
                body.get("marginMode"),
                theses=body.get("theses"),
            )
            self._send_json(200, state)
        except Exception as e:
            print(f"[POST /api/auto-trade/state] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

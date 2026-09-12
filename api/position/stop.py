import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.bybit_client import get_client
from backend_lib.http_utils import JsonApiHandler


class handler(JsonApiHandler):
    def do_POST(self):
        body = self._read_json_body()
        try:
            client, err = get_client()
            if err:
                self._send_json(500, {"retCode": -1, "retMsg": err})
                return
            res = client.set_trading_stop(
                body.get("category", "linear"),
                body.get("symbol", "BTCUSDT"),
                stop_loss=body.get("stopLoss"),
                take_profit=body.get("takeProfit"),
                position_idx=body.get("positionIdx", 0),
            )
            self._send_json(200, res)
        except Exception as e:
            print(f"[POST /api/position/stop] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

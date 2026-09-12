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
            category = body.get("category", "linear")
            symbol = body.get("symbol", "BTCUSDT")
            side = body.get("side", "Buy")
            order_type = body.get("orderType", "Market")
            qty = body.get("qty", 0.001)
            price = body.get("price")
            tp = body.get("takeProfit")
            sl = body.get("stopLoss")
            res = client.place_order(category, symbol, side, order_type, qty, price, tp, sl)
            self._send_json(200, res)
        except Exception as e:
            print(f"[POST /api/order/place] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

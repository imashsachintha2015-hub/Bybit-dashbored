import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.bybit_client import get_client
from backend_lib.http_utils import JsonApiHandler


class handler(JsonApiHandler):
    # Also serves /api/order/close and /api/position/stop, via vercel.json
    # rewrites to /api/order/place?_action=close|stop. The Hobby plan caps a
    # deployment at 12 Serverless Functions -- three tiny order/position POST
    # routes sharing one function keeps the project under that without
    # changing any frontend URL. A rewrite's destination is what the
    # function actually sees on self.path (per Vercel's own rewrite docs,
    # which show a source path's segments arriving at the destination as
    # query params), so branching on _action here is reliable.
    def do_POST(self):
        action = parse_qs(urlparse(self.path).query).get("_action", ["place"])[0]
        body = self._read_json_body()
        try:
            client, err = get_client()
            if err:
                self._send_json(500, {"retCode": -1, "retMsg": err})
                return

            if action == "close":
                category = body.get("category", "linear")
                symbol = body.get("symbol", "BTCUSDT")
                side = body.get("side", "Buy")
                qty = body.get("qty", 0.001)
                res = client.close_position(category, symbol, side, qty)
            elif action == "stop":
                res = client.set_trading_stop(
                    body.get("category", "linear"),
                    body.get("symbol", "BTCUSDT"),
                    stop_loss=body.get("stopLoss"),
                    take_profit=body.get("takeProfit"),
                    position_idx=body.get("positionIdx", 0),
                )
            else:
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
            print(f"[POST {self.path}] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

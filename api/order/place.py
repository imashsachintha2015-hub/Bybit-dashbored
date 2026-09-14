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
    #
    # _action=cancel is called directly as /api/order/place?_action=cancel
    # (no rewrite needed -- there's no pre-existing frontend URL to preserve
    # for a brand-new action) to cancel a resting patient-maker entry that
    # hasn't filled within its timeout. See app.js's pendingEntries.
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
            elif action == "cancel":
                res = client.cancel_order(
                    body.get("category", "linear"),
                    body.get("symbol", "BTCUSDT"),
                    body.get("orderId"),
                )
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
                leverage = body.get("leverage")
                margin_mode = body.get("marginMode")

                # ── Same-coin duplicate guard ──
                try:
                    pos_res = client.get_positions(symbol)
                    positions = pos_res.get("result", {}).get("list", [])
                    live = [p for p in positions if float(p.get("size", 0)) > 0]
                    if live:
                        self._send_json(200, {
                            "retCode": -1,
                            "retMsg": f"Already holding a live {symbol} position — duplicate blocked"
                        })
                        return

                    orders_res = client.get_open_orders(symbol)
                    pending = orders_res.get("result", {}).get("list", [])
                    if pending:
                        self._send_json(200, {
                            "retCode": -1,
                            "retMsg": f"A limit order for {symbol} is already resting — duplicate blocked"
                        })
                        return
                except Exception as e:
                    print(f"[duplicate-guard] Check failed (proceeding): {e}")

                # ── Apply leverage and margin mode ──
                if margin_mode:
                    try:
                        mm_res = client.switch_margin_mode(symbol, margin_mode, leverage or 10)
                        rc = mm_res.get("retCode", 0)
                        if rc not in (0, 110026):
                            print(f"[margin-mode] {symbol}: retCode={rc} {mm_res.get('retMsg','')}")
                    except Exception as e:
                        print(f"[margin-mode] {symbol} switch failed: {e}")

                if leverage:
                    try:
                        lev_res = client.set_leverage(symbol, leverage)
                        rc = lev_res.get("retCode", 0)
                        if rc not in (0, 110043):
                            print(f"[leverage] {symbol}: retCode={rc} {lev_res.get('retMsg','')}")
                    except Exception as e:
                        print(f"[leverage] {symbol} set failed: {e}")

                res = client.place_order(category, symbol, side, order_type, qty, price, tp, sl)

            self._send_json(200, res)
        except Exception as e:
            print(f"[POST {self.path}] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

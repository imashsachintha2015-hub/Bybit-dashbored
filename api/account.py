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

    # One-time demo-funds drawdown to `target` (default 10000 USDT), via
    # Bybit's real demo-apply-money endpoint -- not a UI feature, just a
    # POST to hit repeatedly (see below) until it reports done.
    #
    # A fresh Bybit demo account is seeded across FOUR coins at once (a real
    # account looks like: 1 BTC, 1 ETH, 50000 USDC, ~50000 USDT -- total
    # ~$180k), and demo-apply-money only ever adjusts ONE coin per call, by
    # a coin-denominated amount that can't exceed what that coin currently
    # holds (asking to remove more USDT than the account actually holds in
    # USDT fails, even though total equity across all coins is far higher).
    # It's also rate-limited to 1 request/minute. So reaching an exact
    # target reliably means: zero BTC, then ETH, then USDC (one full-balance
    # reduction each, one per call), then trim USDT to land exactly on
    # target -- each POST here does exactly the next one of those steps and
    # reports what's left, rather than assuming USDT alone can cover it.
    DRAWDOWN_ORDER = ["BTC", "ETH", "USDC"]
    MAX_PER_REQUEST = {"BTC": 15, "ETH": 200, "USDT": 100000, "USDC": 100000}

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
            if not rows:
                self._send_json(200, {"note": "No wallet balance row returned; nothing to adjust."})
                return

            row = rows[0]
            total_equity = float(row.get("totalEquity", 0))
            coins = {c["coin"]: c for c in row.get("coin", [])}

            if abs(total_equity - target) < 1:
                self._send_json(200, {"before": total_equity, "target": target, "note": "Already within $1 of target; nothing applied."})
                return

            # Step 1: zero out non-USDT coins first, one per call.
            for coin_name in self.DRAWDOWN_ORDER:
                c = coins.get(coin_name)
                held = float(c.get("equity", 0)) if c else 0.0
                if held > 1e-8:
                    amount = min(held, self.MAX_PER_REQUEST[coin_name])
                    res = client.apply_demo_funds(coin_name, amount, reduce=True)
                    self._send_json(200, {
                        "before": total_equity, "target": target,
                        "step": f"reduced {coin_name} by {amount}",
                        "bybit": res,
                        "note": "Bybit rate-limits this to 1/minute -- POST again in 60s+ to continue.",
                    })
                    return

            # Step 2: only USDT left -- trim (or top up) to land on target.
            usdt_equity = float(coins.get("USDT", {}).get("equity", 0))
            delta = round(target - usdt_equity, 2)
            if abs(delta) < 1:
                self._send_json(200, {"before": total_equity, "target": target, "note": "Done."})
                return
            step = min(abs(delta), self.MAX_PER_REQUEST["USDT"])
            res = client.apply_demo_funds("USDT", step, reduce=delta < 0)
            remaining = round(abs(delta) - step, 2)
            self._send_json(200, {
                "before": total_equity, "target": target,
                "step": f"{'reduced' if delta < 0 else 'added'} USDT by {step}",
                "remaining": remaining,
                "bybit": res,
                "note": "POST again in 60s+ if remaining > 0." if remaining > 0 else "Done.",
            })
        except Exception as e:
            print(f"[POST /api/account] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

"""Bybit V5 demo-trading signed client.

Ported from server.py's BybitDemoClient with no logic changes -- request
signing, retries and error shaping are identical. What's new is get_client():
server.py built one client at import time and exited the whole process if
credentials were missing, which is the right failure mode for a long-lived
server. A serverless function shouldn't crash the same way on every cold
start just because env vars aren't set yet -- it should say so, once, in the
JSON response for that one request.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request


class BybitDemoClient:
    def __init__(self, key, secret, base_url):
        self.key = key
        self.secret = secret
        self.base_url = base_url
        self.time_offset = 0
        self.last_sync = 0
        self.sync_time()

    def sync_time(self):
        try:
            req = urllib.request.Request(f"{self.base_url}/v5/market/time", headers={"User-Agent": "MASIS/3.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode())
                server_ts = int(data["result"]["timeNano"]) // 1000000
                local_ts = int(time.time() * 1000)
                self.time_offset = server_ts - local_ts
                self.last_sync = time.time()
                print(f"[Bybit] Synced server time. Offset: {self.time_offset} ms")
        except Exception as e:
            print(f"[Bybit] Failed to sync server time: {e}")

    def signed_request(self, method, path, params=None, body=None):
        if time.time() - self.last_sync > 300:  # re-sync every 5 min
            self.sync_time()

        ts = str(int(time.time() * 1000) + self.time_offset)
        recv_window = "20000"

        query_str = ""
        if params:
            query_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        body_str = json.dumps(body) if body is not None else ""
        payload = ts + self.key + recv_window + (query_str if method == "GET" else body_str)
        signature = hmac.new(self.secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

        url = f"{self.base_url}{path}" + (f"?{query_str}" if query_str else "")
        try:
            # Building the Request is not just bookkeeping: constructing it
            # parses the URL and raises ValueError immediately for anything
            # without a recognised scheme (e.g. a blank BYBIT_BASE_URL). That
            # has to be inside the same try as the network call below, or a
            # bad base_url crashes the whole request uncaught instead of
            # coming back as a normal {retCode: -1, retMsg: ...} response.
            req = urllib.request.Request(url, data=body_str.encode("utf-8") if body_str else None, method=method)
            req.add_header("X-BAPI-API-KEY", self.key)
            req.add_header("X-BAPI-TIMESTAMP", ts)
            req.add_header("X-BAPI-SIGN", signature)
            req.add_header("X-BAPI-RECV-WINDOW", recv_window)
            req.add_header("User-Agent", "MASIS/3.0")
            if body_str:
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"[Bybit HTTPError {e.code}] {err_body}")
            try:
                return json.loads(err_body)
            except Exception:
                return {"retCode": e.code, "retMsg": err_body}
        except Exception as e:
            print(f"[Bybit Error] {e}")
            return {"retCode": -1, "retMsg": str(e)}

    def get_wallet_balance(self):
        return self.signed_request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})

    def apply_demo_funds(self, coin, amount, reduce=False):
        """Demo-trading-only: credits (or, with reduce=True, debits) virtual
        balance via Bybit's demo-apply-money endpoint. Real Bybit API, real
        virtual funds -- not something this app fabricates. Amount is a
        string per Bybit's spec; max per request is coin-dependent (e.g.
        100000 for USDT), and adding is rejected once total equity is
        already above 10,000 USDT (Bybit's own rule, not this app's)."""
        body = {
            "adjustType": 1 if reduce else 0,
            "utaDemoApplyMoney": [{"coin": coin, "amountStr": str(amount)}],
        }
        return self.signed_request("POST", "/v5/account/demo-apply-money", body=body)

    def get_positions(self, symbol=None):
        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        return self.signed_request("GET", "/v5/position/list", params)

    def get_closed_pnl(self, limit=50):
        return self.signed_request("GET", "/v5/position/closed-pnl", {"category": "linear", "limit": str(limit)})

    def place_order(self, category, symbol, side, order_type, qty, price=None, tp=None, sl=None):
        body = {
            "category": category,
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty": str(qty),
            "timeInForce": "GTC"
        }
        if price and order_type.lower() == "limit":
            body["price"] = str(price)
        if tp:
            body["takeProfit"] = str(tp)
        if sl:
            body["stopLoss"] = str(sl)
        return self.signed_request("POST", "/v5/order/create", body=body)

    def set_trading_stop(self, category, symbol, stop_loss=None, take_profit=None, position_idx=0):
        """Moves the broker-side stop on an open position.

        The position manager uses this to ratchet the stop to break-even after
        the first target and to trail it afterwards. It matters that this is
        broker-side: a stop that only exists in the browser tab is not a stop.
        """
        body = {"category": category, "symbol": symbol, "positionIdx": position_idx}
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)
        return self.signed_request("POST", "/v5/position/trading-stop", body=body)

    def cancel_order(self, category, symbol, order_id):
        """Cancels a resting (unfilled) order -- used for the patient-maker
        entry's timeout: if a limit order placed at an offset from market
        hasn't filled within its window, the entry is abandoned rather than
        left resting indefinitely against a setup that may no longer be
        valid. Bybit returns an error if the order already filled or was
        already cancelled; the caller treats that as "check for a fill next
        cycle" rather than a hard failure, since a fill and a cancel racing
        each other is expected, not exceptional.
        """
        body = {"category": category, "symbol": symbol, "orderId": order_id}
        return self.signed_request("POST", "/v5/order/cancel", body=body)

    def close_position(self, category, symbol, side, qty):
        # To close a position, place an opposing reduceOnly market order
        close_side = "Sell" if side.lower() == "buy" else "Buy"
        body = {
            "category": category,
            "symbol": symbol,
            "side": close_side,
            "orderType": "Market",
            "qty": str(qty),
            "reduceOnly": True,
            "timeInForce": "IOC"
        }
        return self.signed_request("POST", "/v5/order/create", body=body)


_client = None


def get_client():
    """Returns (client, error). Memoized per warm function instance: Vercel
    can reuse the same process for a burst of requests to the same endpoint
    file, so this avoids re-syncing Bybit's clock on every single call the
    way constructing a fresh client each time would."""
    global _client
    if _client is not None:
        return _client, None
    # .strip() matters here, not just as hygiene: a dashboard env-var field
    # that picked up a stray tab/newline from a copy-paste (observed in
    # production -- BYBIT_BASE_URL came through as "api-demo.bybit.com\t")
    # makes urllib refuse the URL outright ("URL can't contain control
    # characters"), and would just as silently break HMAC signing if it
    # were in the key or secret instead.
    key = (os.environ.get("BYBIT_API_KEY") or "").strip()
    secret = (os.environ.get("BYBIT_API_SECRET") or "").strip()
    if not key or not secret:
        return None, "BYBIT_API_KEY / BYBIT_API_SECRET not set in the environment"
    # `.get(name, default)` only substitutes the default when the var is
    # UNSET, not when it's set to an empty string -- an env var added in a
    # host's dashboard with a blank value (BYBIT_BASE_URL="") would otherwise
    # silently produce a schemeless URL. `or` treats both cases the same.
    base_url = (os.environ.get("BYBIT_BASE_URL") or "https://api-demo.bybit.com").strip()
    # A base URL saved without its scheme (e.g. "api-demo.bybit.com") is
    # another silent, otherwise-hard-to-notice way to end up with a request
    # urllib refuses to send at all.
    if base_url and not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    _client = BybitDemoClient(key, secret, base_url)
    return _client, None

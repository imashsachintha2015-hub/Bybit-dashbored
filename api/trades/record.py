import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib import trade_stats as trade_stats_mod


class handler(JsonApiHandler):
    # Records the reasoning behind a close: which setup fired, what grade it
    # scored, why the position was exited, and the realised R. It no longer
    # maintains its own win/loss/profit tallies -- those came from here AND
    # from Bybit, which double-counted every trade (see api/performance.py).
    def do_POST(self):
        body = self._read_json_body()
        try:
            try:
                pnl = float(body.get("pnl", 0))
            except (TypeError, ValueError):
                pnl = 0.0

            stats = trade_stats_mod.load()
            stats["trade_history"].insert(0, {
                "id": f"TRD-{int(time.time()) % 100000}",
                "recorded_at": int(time.time() * 1000),
                "time": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
                "symbol": body.get("symbol", ""),
                "side": str(body.get("side", "")).upper(),
                "entry": float(body.get("entry", 0) or 0),
                "exit": float(body.get("exit", 0) or 0),
                "pnl": pnl,
                "status": "WIN" if pnl > 0 else "LOSS",
                "setup_type": body.get("setup_type", ""),
                "grade": body.get("grade", ""),
                "score": body.get("score"),
                "r_multiple": body.get("r_multiple"),
                "exit_reason": body.get("exit_reason", ""),
                "reason": body.get("reason", "")
            })
            if len(stats["trade_history"]) > 200:
                stats["trade_history"].pop()
            trade_stats_mod.save(stats)
            self._send_json(200, {"success": True})
        except Exception as e:
            print(f"[POST /api/trades/record] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

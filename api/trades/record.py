import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib import trade_stats as trade_stats_mod
from backend_lib import signal_log as signal_log_mod
from backend_lib.kv import kv_configured


class handler(JsonApiHandler):
    # Diagnostic only -- lets us confirm from outside the container whether
    # a Redis/KV store is actually attached and whether writes are landing
    # in it, without needing a real closed trade to test with. This is what
    # caught the original bug: every one of 17 production trades came back
    # with blank setup_type/grade because there was no durable store behind
    # this at all (see kv.py's module docstring).
    # Actions are dispatched on a query param rather than split into separate
    # files: this deployment is one function short of the plan's route cap, so
    # a new endpoint would cost a deploy slot the project does not have.
    #   (no action)          -> the persistence diagnostic, unchanged
    #   ?_action=signals     -> paged suggestion history (every candidate)
    #   ?_action=trades      -> paged closed-trade history
    def do_GET(self):
        q = self._query()
        action = (q.get("_action") or [""])[0]

        if action == "signals":
            # Settle a few outstanding suggestions before rendering. The
            # browser-side watcher only runs while a dashboard tab is awake,
            # which on a phone is almost never -- background tabs are suspended,
            # so setups resolved while the screen was off would stay pending
            # forever. Doing it here means opening the monitor is enough.
            try:
                signal_log_mod.settle_pending(max_rows=4)
            except Exception as e:
                print(f"[signals] settle_pending failed: {e}")
            self._send_json(200, signal_log_mod.page(
                page_num=(q.get("page") or ["1"])[0],
                limit=(q.get("limit") or ["25"])[0],
                symbol=(q.get("symbol") or [None])[0],
                outcome=(q.get("outcome") or [None])[0],
            ))
            return

        if action == "trades":
            history = trade_stats_mod.load().get("trade_history", [])
            try:
                limit = max(1, min(int((q.get("limit") or ["25"])[0]), 100))
                page_num = max(1, int((q.get("page") or ["1"])[0]))
            except (TypeError, ValueError):
                limit, page_num = 25, 1
            start = (page_num - 1) * limit
            self._send_json(200, {
                "items": history[start:start + limit],
                "page": page_num, "limit": limit, "total": len(history),
                "pages": max(1, (len(history) + limit - 1) // limit),
            })
            return

        stats = trade_stats_mod.load()
        history = stats.get("trade_history", [])
        self._send_json(200, {
            "kv_configured": kv_configured(),
            "trade_count": len(history),
            "signal_count": len(signal_log_mod.load().get("signals", [])),
            "most_recent": history[0] if history else None
        })

    # Records the reasoning behind a close: which setup fired, what grade it
    # scored, why the position was exited, and the realised R. It no longer
    # maintains its own win/loss/profit tallies -- those came from here AND
    # from Bybit, which double-counted every trade (see api/performance.py).
    def do_POST(self):
        body = self._read_json_body()
        q = self._query()
        if (q.get("_action") or [""])[0] == "signal_resolve":
            try:
                self._send_json(200, {"success": True, "row": signal_log_mod.resolve(body)})
            except Exception as e:
                print(f"[POST /api/trades/record?_action=signal_resolve] error: {e}")
                self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})
            return
        if (q.get("_action") or [""])[0] == "signal":
            try:
                self._send_json(200, {"success": True, "row": signal_log_mod.record(body)})
            except Exception as e:
                print(f"[POST /api/trades/record?_action=signal] Unhandled error: {e}")
                self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})
            return
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
            try:
                signal_log_mod.record_trade_outcome(body)
            except Exception as e:
                print(f"[trades/record] signal_log update failed: {e}")
            self._send_json(200, {"success": True})
        except Exception as e:
            print(f"[POST /api/trades/record] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

    # Narrow cleanup valve for exactly the synthetic rows the GET/POST above
    # are used to write during a persistence check (setup_type
    # "KV_PERSISTENCE_TEST") -- never touches a real trade, so it's safe to
    # leave wired in rather than one-shot it and rip it back out.
    def do_DELETE(self):
        stats = trade_stats_mod.load()
        before = len(stats.get("trade_history", []))
        stats["trade_history"] = [
            t for t in stats.get("trade_history", [])
            if t.get("setup_type") != "KV_PERSISTENCE_TEST"
        ]
        removed = before - len(stats["trade_history"])
        if removed:
            trade_stats_mod.save(stats)
        self._send_json(200, {"removed": removed})

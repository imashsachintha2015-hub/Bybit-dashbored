import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib import auto_trade_state
from backend_lib.bybit_client import get_client
from backend_lib.market_knowledge import kb


class handler(JsonApiHandler):
    def do_GET(self):
        try:
            q = self._query()
            action = (q.get("_action") or [""])[0]

            if action == "target_mode":
                eq = None
                client, err = get_client()
                if not err and client:
                    try:
                        wb = client.get_wallet_balance()
                        coins = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
                        usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
                        if usdt:
                            eq = float(usdt.get("equity") or 0.0)
                    except Exception as e:
                        print(f"[api/auto-trade/state?_action=target_mode] balance warning: {e}")
                self._send_json(200, kb.get_target_state(current_equity=eq))
                return

            if action == "gatekeeper_decisions":
                decisions = []
                # 1. Local scratch file (instant, 0 egress on Railway)
                dec_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scratch", "deepseek_pre_trade_decisions.json")
                if os.path.exists(dec_path):
                    try:
                        import json
                        with open(dec_path, "r", encoding="utf-8") as f:
                            raw = json.load(f)
                        decisions = raw[-20:] if isinstance(raw, list) else []
                    except Exception:
                        pass
                # 2. Supabase journal table fallback (compact rows, ~2KB total)
                if not decisions:
                    try:
                        from backend_lib.supabase_client import supabase_get
                        rows = supabase_get("signal_decision_journal", {"order": "recorded_at.desc", "limit": "20"})
                        if rows and isinstance(rows, list):
                            for r in rows:
                                approved = (r.get("decision") == "EXECUTED")
                                decisions.append({
                                    "approved": approved,
                                    "conviction_score": r.get("score") or 75,
                                    "symbol": r.get("symbol", ""),
                                    "direction": r.get("direction", "BUY"),
                                    "price": float(r.get("entry_price") or 0.0),
                                    "setup": r.get("setup_type", "SWING_PULLBACK"),
                                    "rationale": r.get("adversarial_veto_reason") or ("Approved by gatekeeper" if approved else "Vetoed"),
                                    "timestamp": int(r.get("recorded_at") or 0) // 1000
                                })
                    except Exception:
                        pass
                self._send_json(200, {"decisions": list(reversed(decisions)), "count": len(decisions)})
                return

            self._send_json(200, auto_trade_state.load())
        except Exception as e:
            print(f"[api/auto-trade/state GET error]: {e}")
            self._send_json(200, auto_trade_state.DEFAULT_STATE)

    def do_POST(self):
        body = self._read_json_body()
        q = self._query()
        action = (q.get("_action") or [""])[0]

        if action == "target_mode":
            target_eq = body.get("target_equity")
            is_armed = body.get("is_armed")
            status = body.get("status")
            strategy_mode = body.get("strategy_mode")
            time_horizon_hours = body.get("time_horizon_hours")
            start_equity = body.get("start_equity")
            kb.set_target_state(
                target_equity=target_eq,
                is_armed=is_armed,
                status=status,
                strategy_mode=strategy_mode,
                time_horizon_hours=time_horizon_hours,
                start_equity=start_equity
            )

            eq = None
            client, err = get_client()
            if not err and client:
                try:
                    wb = client.get_wallet_balance()
                    coins = wb.get("result", {}).get("list", [{}])[0].get("coin", [])
                    usdt = next((c for c in coins if c.get("coin") == "USDT"), {})
                    if usdt:
                        eq = float(usdt.get("equity") or 0.0)
                except Exception:
                    pass
            self._send_json(200, kb.get_target_state(current_equity=eq))
            return

        try:
            state = auto_trade_state.save(
                body.get("armed"),
                body.get("riskPerTradePct"),
                body.get("sizingMode"),
                body.get("fixedUsdtSize"),
                body.get("leverage"),
                body.get("marginMode"),
                theses=body.get("theses"),
                daily_gross_target=body.get("dailyGrossTarget"),
                target_notional=body.get("targetNotional"),
                virtual_equity=body.get("virtualEquity"),
                max_concurrent_positions=body.get("maxConcurrentPositions"),
                strategy_mode=body.get("strategyMode"),
                scalp_mode=body.get("scalpMode"),
                sure_shot_mode=body.get("sureShotMode"),
            )
            self._send_json(200, state)
        except Exception as e:
            print(f"[POST /api/auto-trade/state] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

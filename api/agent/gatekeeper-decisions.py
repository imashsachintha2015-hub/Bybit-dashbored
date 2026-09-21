import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler

class handler(JsonApiHandler):
    def do_GET(self):
        decisions = []
        try:
            from backend_lib.supabase_client import supabase_kv_get
            decisions = supabase_kv_get("deepseek_pre_trade_decisions") or []
        except Exception:
            pass

        if not decisions:
            dec_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scratch", "deepseek_pre_trade_decisions.json")
            if os.path.exists(dec_path):
                try:
                    with open(dec_path, "r", encoding="utf-8") as f:
                        decisions = json.load(f)
                except Exception:
                    pass

        self._send_json(200, {"decisions": list(reversed(decisions)), "count": len(decisions)})

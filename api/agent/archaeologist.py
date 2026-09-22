import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib.supabase_client import supabase_get, SUPABASE_URL, SUPABASE_KEY

DIRECTORY = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(JsonApiHandler):
    def do_GET(self):
        super_trades = []
        anti_rules = []

        # 1. Load from scratch/historical_super_trades.json
        scratch_path = os.path.join(DIRECTORY, "scratch", "historical_super_trades.json")
        if os.path.exists(scratch_path):
            try:
                with open(scratch_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    super_trades = data.get("super_trades", [])
                    anti_rules = data.get("anti_rules", [])
            except Exception:
                pass

        # 2. Augment anti-rules from Supabase cloud database if available
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                cloud_rules = supabase_get("anti_pattern_rules", {"select": "rule_id,title,direction,condition_trigger,action,rationale,is_active"})
                if cloud_rules and isinstance(cloud_rules, list) and len(cloud_rules) > 0:
                    anti_rules = cloud_rules
            except Exception:
                pass

        payload = {
            "super_trades": super_trades,
            "anti_rules": anti_rules,
            "super_trades_count": len(super_trades)
        }
        self._send_json(200, payload)

    def do_POST(self):
        # Trigger scan acknowledgment
        self._send_json(200, {"success": True, "message": "Archaeology scan triggered"})

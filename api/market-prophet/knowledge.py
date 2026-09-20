import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib.market_knowledge import kb


class handler(JsonApiHandler):
    def do_GET(self):
        q = self._query()
        symbol = (q.get("symbol") or [None])[0]
        if symbol:
            rules = kb.get_relevant_knowledge(symbol=symbol)
            self._send_json(200, {"rules": rules})
            return

        summary = kb.get_knowledge_summary()
        self._send_json(200, summary)

    def do_POST(self):
        body = self._read_json_body()
        res = kb.record_trade_close(body, micro_data=body.get("microstructure"))
        self._send_json(200, {"success": True, "result": res})

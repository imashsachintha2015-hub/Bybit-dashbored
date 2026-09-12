import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib.supervisor import run_supervisor_verdict


class handler(JsonApiHandler):
    def do_POST(self):
        body = self._read_json_body()
        try:
            self._send_json(200, run_supervisor_verdict(body))
        except Exception as e:
            print(f"[POST /api/supervisor/verdict] Unhandled error: {e}")
            self._send_json(500, {"retCode": -1, "retMsg": f"Server error: {e}"})

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler
from backend_lib import llm_budget


class handler(JsonApiHandler):
    def do_GET(self):
        self._send_json(200, llm_budget.status())

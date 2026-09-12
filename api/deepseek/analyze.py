import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_lib.http_utils import JsonApiHandler


class handler(JsonApiHandler):
    # Legacy prose-synthesis route, kept so an older cached frontend cannot
    # 404, but it no longer calls the model — it returns the local synthesis
    # only. The prose was never read by any code path.
    def do_POST(self):
        self._read_json_body()  # drain the body even though it's unused
        self._send_json(200, {
            "success": True,
            "is_fallback": True,
            "fallback_reason": "Prose synthesis retired in V3. Use POST /api/supervisor/verdict, which returns a structured verdict the engine actually consumes.",
            "model": "MASIS local",
            "analysis": "The free-text synthesis endpoint has been retired. It generated ~700 tokens per call on a timer, no code parsed the result, and it could not change any decision. See /api/llm/status for current model spend.",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        })

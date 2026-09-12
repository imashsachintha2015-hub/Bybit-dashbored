"""Shared response/CORS/body-parsing helpers for the api/*.py serverless
functions.

Vercel's Python runtime instantiates a class named `handler` per request the
same way http.server does locally, so this is one shared base class doing
what server.py's DashboardHandler did as a single object -- every endpoint
file below subclasses JsonApiHandler and only implements do_GET/do_POST.
"""
import json
import math
from http.server import BaseHTTPRequestHandler


def sanitize_for_json(obj):
    """Recursively replaces NaN/Infinity/-Infinity (valid Python floats, not
    valid JSON) with None, and anything json.dumps can't otherwise handle
    with its str(). Guarantees _send_json always emits parseable JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


class JsonApiHandler(BaseHTTPRequestHandler):
    """Base class for every api/*.py endpoint: JSON responses, permissive
    CORS (the frontend can be opened from a Vercel preview URL, a custom
    domain, or localhost -- there's no single fixed origin to allow), and a
    body parser that never raises on a malformed/empty request body."""

    def _send_json(self, status, payload):
        body = json.dumps(sanitize_for_json(payload)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        content_len = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(content_len) if content_len > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def log_message(self, format, *args):
        pass

"""Tiny persistence helper for the two things this app actually needs to
remember across requests: the local trade-reasoning ledger (trade_stats.py)
and the daily LLM call budget (llm_budget.py).

server.py could keep these in a module-level dict and a JSON file on disk
because it was one long-lived process. A Vercel serverless function is not:
every invocation can land on a different, short-lived instance, and anything
written to /tmp disappears the moment that instance is recycled. Neither
counter means anything if it silently resets on the next cold start.

So this reaches for Upstash Redis's REST API when it's configured, and
falls back to an in-process dict mirrored to /tmp otherwise. The fallback
is fine for local testing; it is NOT real persistence in production --
confirmed the hard way once already (every trade recorded in production
came back with blank setup_type/grade/exit_reason, because the write from
POST /api/trades/record and the read from GET /api/performance landed on
different serverless instances with no shared memory or KV attached).

Attach a Redis store to the Vercel project (Storage tab -> Marketplace ->
Upstash, or equivalent) for the trade ledger and the daily model-call cap
to actually hold across requests. The original "Vercel KV" product was
folded into this Marketplace/Upstash integration; env var naming has
shifted with it, so both the legacy KV_REST_API_* names and Upstash's own
UPSTASH_REDIS_REST_* names are checked here, whichever the integration
actually injects.
"""
import json
import os
import urllib.request

KV_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")

_memory_store = {}


def kv_configured():
    return bool(KV_URL and KV_TOKEN)


def kv_get_json(key, default):
    if kv_configured():
        try:
            req = urllib.request.Request(
                f"{KV_URL}/get/{key}",
                headers={"Authorization": f"Bearer {KV_TOKEN}"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode())
            raw = res.get("result")
            return json.loads(raw) if raw else default
        except Exception as e:
            print(f"[KV] get({key}) failed, falling back to process-local state: {e}")

    if key in _memory_store:
        return _memory_store[key]
    tmp_path = f"/tmp/{key}.json"
    if os.path.exists(tmp_path):
        try:
            with open(tmp_path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def kv_set_json(key, value):
    if kv_configured():
        try:
            body = json.dumps(value).encode("utf-8")
            req = urllib.request.Request(
                f"{KV_URL}/set/{key}", data=body, method="POST",
                headers={"Authorization": f"Bearer {KV_TOKEN}"}
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            return
        except Exception as e:
            print(f"[KV] set({key}) failed, falling back to process-local state: {e}")

    _memory_store[key] = value
    try:
        with open(f"/tmp/{key}.json", "w") as f:
            json.dump(value, f)
    except Exception:
        pass

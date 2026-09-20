"""
Persistence helper for the MASIS V3 trading dashboard and serverless runtime.
Uses Supabase (https://rzizfjujdlmevywutjvv.supabase.co) as the primary unified cloud database.
Also supports Upstash Redis fallback and in-process cache.

Ensures that Vercel serverless functions and local daemons share the exact same
durable state across cold starts and device reloads.
"""
import json
import os
import urllib.request

from .supabase_client import supabase_kv_get, supabase_kv_set, SUPABASE_URL, SUPABASE_KEY

KV_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")

_memory_store = {}


def kv_configured():
    """True if Supabase or Upstash Redis is configured."""
    return bool(SUPABASE_URL and SUPABASE_KEY) or bool(KV_URL and KV_TOKEN)


def kv_get_json(key, default):
    """
    Retrieve JSON data by key.
    Checks Supabase cloud database first, then Redis, then in-memory/tmp.
    """
    # 1. Supabase Cloud Database (Primary)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            val = supabase_kv_get(key, default=None)
            if val is not None:
                _memory_store[key] = val
                return val
        except Exception as e:
            print(f"[Supabase KV] get({key}) failed: {e}")

    # 2. Redis KV (Secondary fallback)
    if KV_URL and KV_TOKEN:
        try:
            req = urllib.request.Request(
                f"{KV_URL}/get/{key}",
                headers={"Authorization": f"Bearer {KV_TOKEN}"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode())
            raw = res.get("result")
            if raw:
                parsed = json.loads(raw)
                _memory_store[key] = parsed
                return parsed
        except Exception as e:
            print(f"[Redis KV] get({key}) failed: {e}")

    # 3. Process memory or local /tmp cache
    if key in _memory_store:
        return _memory_store[key]
    tmp_path = f"/tmp/{key}.json"
    if os.path.exists(tmp_path):
        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def kv_set_json(key, value):
    """
    Persist JSON data by key.
    Writes to Supabase cloud database, Redis (if configured), and in-memory cache.
    """
    _memory_store[key] = value

    # Write to local tmp file as emergency backup
    try:
        with open(f"/tmp/{key}.json", "w", encoding="utf-8") as f:
            json.dump(value, f)
    except Exception:
        pass

    # 1. Supabase Cloud Database (Primary)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase_kv_set(key, value)
        except Exception as e:
            print(f"[Supabase KV] set({key}) failed: {e}")

    # 2. Redis KV (Secondary fallback)
    if KV_URL and KV_TOKEN:
        try:
            body = json.dumps(value).encode("utf-8")
            req = urllib.request.Request(
                f"{KV_URL}/set/{key}", data=body, method="POST",
                headers={"Authorization": f"Bearer {KV_TOKEN}"}
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception as e:
            print(f"[Redis KV] set({key}) failed: {e}")

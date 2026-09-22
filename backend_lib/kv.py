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

import time

_memory_store = {}
_memory_ts = {}
CACHE_TTL = 30  # 30-second memory cache to eliminate repetitive network polling

# Keys that must NEVER be written to Supabase KV (they are heavy blobs with dedicated tables or local files)
EXCLUDE_FROM_CLOUD_KV = {
    "signal_log",
    "deepseek_pre_trade_decisions",
    "historical_super_trades",
    "live_market_state",
    "deepseek_claimer_decisions",
}

SCRATCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch")
os.makedirs(SCRATCH_DIR, exist_ok=True)


def kv_configured():
    """True if Supabase or Upstash Redis is configured."""
    return bool(SUPABASE_URL and SUPABASE_KEY) or bool(KV_URL and KV_TOKEN)


def _get_local_file(key):
    # Try scratch/ first, then /tmp/
    scratch_p = os.path.join(SCRATCH_DIR, f"{key}.json")
    if os.path.exists(scratch_p):
        try:
            with open(scratch_p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    tmp_path = f"/tmp/{key}.json"
    if os.path.exists(tmp_path):
        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _set_local_file(key, value):
    scratch_p = os.path.join(SCRATCH_DIR, f"{key}.json")
    try:
        with open(scratch_p, "w", encoding="utf-8") as f:
            json.dump(value, f)
    except Exception:
        pass
    try:
        with open(f"/tmp/{key}.json", "w", encoding="utf-8") as f:
            json.dump(value, f)
    except Exception:
        pass


def kv_get_json(key, default):
    """
    Retrieve JSON data by key.
    Uses memory cache first, then local scratch file for large blobs, then Supabase.
    """
    now = time.time()

    # 1. In-memory cache hit (0ms, 0 bytes egress)
    if key in _memory_store and (now - _memory_ts.get(key, 0) < CACHE_TTL):
        return _memory_store[key]

    # 2. Excluded heavy keys: read local scratch file (0 egress)
    if key in EXCLUDE_FROM_CLOUD_KV:
        val = _get_local_file(key)
        if val is not None:
            _memory_store[key] = val
            _memory_ts[key] = now
            return val
        return _memory_store.get(key, default)

    # 3. Supabase Cloud Database (for small configs only)
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            val = supabase_kv_get(key, default=None)
            if val is not None:
                _memory_store[key] = val
                _memory_ts[key] = now
                return val
        except Exception as e:
            print(f"[Supabase KV] get({key}) failed: {e}")

    # 4. Redis KV (Secondary fallback)
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
                _memory_ts[key] = now
                return parsed
        except Exception as e:
            print(f"[Redis KV] get({key}) failed: {e}")

    # 5. Local file fallback
    val = _get_local_file(key)
    if val is not None:
        _memory_store[key] = val
        _memory_ts[key] = now
        return val

    return default


def kv_set_json(key, value):
    """
    Persist JSON data by key.
    Writes to memory, local scratch file, and cloud KV (only for lightweight configs).
    """
    now = time.time()
    _memory_store[key] = value
    _memory_ts[key] = now

    # Write to local file
    _set_local_file(key, value)

    # Do NOT write heavy blobs to Cloud KV
    if key in EXCLUDE_FROM_CLOUD_KV:
        return

    # 1. Supabase Cloud Database (Primary for small configs)
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

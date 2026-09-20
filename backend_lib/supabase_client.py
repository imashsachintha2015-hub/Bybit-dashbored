"""
Supabase REST API Client for MASIS V3 & Vercel Dashboard Functions.
Lightweight client with zero third-party dependencies (uses standard library urllib).
Connects seamlessly from both Vercel serverless runtime and local daemons to:
https://rzizfjujdlmevywutjvv.supabase.co
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error

# Project credentials
DEFAULT_SUPABASE_URL = "https://rzizfjujdlmevywutjvv.supabase.co"
DEFAULT_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ6aXpmanVqZGxtZXZ5d3V0anZ2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk5NTg1MjAsImV4cCI6MjEwNTUzNDUyMH0."
    "TsjzP8znL73zL88c8M-nlDD58yTB2T46aaPc3mj_p8I"
)

SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or DEFAULT_SUPABASE_URL
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY") or DEFAULT_ANON_KEY

# Normalize base URL
SUPABASE_URL = SUPABASE_URL.rstrip("/")
REST_BASE = f"{SUPABASE_URL}/rest/v1"


def _headers(prefer=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def supabase_get(table, query_params=None, timeout=6):
    """
    Perform a GET query against a Supabase table.
    query_params can be a dict (e.g. {'select': '*', 'limit': '10', 'key': 'eq.trade_stats'})
    """
    url = f"{REST_BASE}/{table}"
    if query_params:
        query_string = urllib.parse.urlencode(query_params)
        url = f"{url}?{query_string}"
    
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else []
    except Exception as e:
        print(f"[Supabase GET {table}] error: {e}")
        return None


def supabase_post(table, data, prefer="resolution=merge-duplicates,return=representation", timeout=6):
    """
    Perform an INSERT or UPSERT against a Supabase table.
    data can be a dict (single row) or list of dicts (batch rows).
    """
    url = f"{REST_BASE}/{table}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_headers(prefer=prefer), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else str(e)
        print(f"[Supabase POST {table}] HTTP {e.code}: {err_body}")
        return None
    except Exception as e:
        print(f"[Supabase POST {table}] error: {e}")
        return None


def supabase_patch(table, filter_param, data, prefer="return=representation", timeout=6):
    """
    Perform an UPDATE/PATCH on matching rows.
    filter_param should be a dict like {'id': 'eq.1'} or a querystring.
    """
    url = f"{REST_BASE}/{table}"
    if isinstance(filter_param, dict):
        url = f"{url}?{urllib.parse.urlencode(filter_param)}"
    elif filter_param:
        url = f"{url}?{filter_param}"

    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_headers(prefer=prefer), method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else True
    except Exception as e:
        print(f"[Supabase PATCH {table}] error: {e}")
        return None


def supabase_delete(table, filter_param, timeout=6):
    """
    Perform a DELETE on matching rows.
    """
    url = f"{REST_BASE}/{table}"
    if isinstance(filter_param, dict):
        url = f"{url}?{urllib.parse.urlencode(filter_param)}"
    elif filter_param:
        url = f"{url}?{filter_param}"

    req = urllib.request.Request(url, headers=_headers(), method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True
    except Exception as e:
        print(f"[Supabase DELETE {table}] error: {e}")
        return False


# ── KV helpers using public.app_state ──────────────────────────────────────────

def supabase_kv_get(key, default=None):
    """
    Fetch a JSON value for key from public.app_state.
    """
    rows = supabase_get("app_state", {"key": f"eq.{key}", "select": "value"})
    if rows and isinstance(rows, list) and len(rows) > 0:
        return rows[0].get("value", default)
    return default


def supabase_kv_set(key, value):
    """
    Upsert a JSON value for key into public.app_state.
    """
    payload = {
        "key": key,
        "value": value
    }
    # PostgREST upsert requires Prefer: resolution=merge-duplicates
    return supabase_post("app_state", payload, prefer="resolution=merge-duplicates,return=minimal")

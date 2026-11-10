#!/usr/bin/env python3
"""Copy every file in the private 'supplier-documents' bucket from the old project to the new one.
Zero dependencies. Needs the SERVICE ROLE keys (Dashboard -> Settings -> API Keys). Never commit them.
  OLD_URL=https://droywqsxnojflxjroaiw.supabase.co OLD_SERVICE_KEY=... \
  NEW_URL=https://<newref>.supabase.co            NEW_SERVICE_KEY=... python3 copy_storage.py
Files are re-uploaded byte-for-byte, so document encryption is preserved (the key is in your app env)."""
import json, os, urllib.parse, urllib.request

BUCKET = "supplier-documents"
OLD, OLDK = os.environ["OLD_URL"].rstrip("/"), os.environ["OLD_SERVICE_KEY"]
NEW, NEWK = os.environ["NEW_URL"].rstrip("/"), os.environ["NEW_SERVICE_KEY"]

def call(method, url, key, body=None, ctype="application/json", extra=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": ctype, **(extra or {})}
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()

def list_prefix(prefix=""):
    """Recursively list object paths in the old bucket."""
    out, offset = [], 0
    while True:
        body = json.dumps({"prefix": prefix, "limit": 100, "offset": offset}).encode()
        items = json.loads(call("POST", f"{OLD}/storage/v1/object/list/{BUCKET}", OLDK, body))
        for it in items:
            path = f"{prefix}{it['name']}"
            if it.get("id") is None:               # folder
                out += list_prefix(path + "/")
            else:
                out.append((path, (it.get("metadata") or {}).get("mimetype", "application/octet-stream")))
        if len(items) < 100:
            return out
        offset += 100

files = list_prefix()
print(f"{len(files)} files to copy (expected 16, ~5.1 MB)")
for path, mime in files:
    q = urllib.parse.quote(path)
    data = call("GET", f"{OLD}/storage/v1/object/{BUCKET}/{q}", OLDK)
    call("POST", f"{NEW}/storage/v1/object/{BUCKET}/{q}", NEWK, data, ctype=mime, extra={"x-upsert": "true"})
    print("copied", path, len(data), "bytes")
print("done")

"""Mock API for checking the dashboard's Kalman mode in a browser: serves the repo's static files, the Kalman engine
files produced by the replay (ui_sample/), historical candles, and an in-memory auto-trade state. Port 8070."""
import os, sys, json, re, http.server, socketserver, urllib.parse
REPO = "/home/user/Bybit-dashbored"
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "ui_sample")
sys.path.insert(0, REPO)
from backend_lib import kalman_trend as KT

STATE = {"strategyMode": "championship", "championshipMode": True, "armed": False}
SETTINGS = {}
POSTS = []
END_MS = json.load(open(os.path.join(SAMPLE, "kalman_trend_live.json")))["last_bar_close"]

def candles(sym, interval, limit):
    base = sym.replace("USDT", "")
    if interval == "240":
        rows = []
        for f in ("d5", "d4"):
            p = os.path.join(HERE, f, f"{base}_1H.json")
            if os.path.exists(p): rows += json.load(open(p))
        rows = sorted({r[0]: r for r in rows}.values())
        out, block = [], {}
        for r in rows:
            k = r[0] // KT.H4 * KT.H4
            block.setdefault(k, []).append(r)
        for k in sorted(block):
            b = block[k]
            if len(b) == 4: out.append(dict(start=k, open=b[0][1], high=max(x[2] for x in b), low=min(x[3] for x in b), close=b[-1][4], volume=sum(x[5] for x in b)))
    else:
        p = os.path.join(HERE, "d4", f"{base}_1H.json")
        rows = json.load(open(p)) if os.path.exists(p) else []
        out = [dict(start=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]) for r in rows]
    out = [c for c in out if c["start"] < END_MS]
    return out[-limit:]

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=REPO, **k)
    def log_message(self, *a): pass
    def _json(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if u.path == "/index_orig.html":
            b = open(os.path.join(HERE, "index_before_kalman.html"), "rb").read()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(b); return
        if u.path == "/api/kalman/status":
            d = json.load(open(os.path.join(SAMPLE, "kalman_trend_live.json")))
            d["engine_running"] = True; d["settings"] = KT.clean_settings(SETTINGS); d["live_env"] = False
            d["mode_selected"] = STATE.get("strategyMode") == "kalman"
            return self._json(200, d)
        if u.path == "/api/kalman/indicator":
            sym = (q.get("symbol") or ["BTCUSDT"])[0].upper()
            p = os.path.join(SAMPLE, "kalman", sym + ".json")
            return self._json(200, json.load(open(p))) if os.path.exists(p) else self._json(404, {"error": "no data"})
        if u.path == "/api/kline":
            sym = (q.get("symbol") or ["BTCUSDT"])[0].upper(); iv = (q.get("interval") or ["15"])[0]
            lim = int((q.get("limit") or ["120"])[0])
            return self._json(200, {"list": candles(sym, iv, lim)})
        if u.path in ("/api/auto-trade/state", "/api/mode", "/api/cme_x5/mode"):
            return self._json(200, STATE)
        if u.path == "/api/_posts":
            return self._json(200, POSTS)
        if u.path.startswith("/api/"):
            return self._json(200, {})
        return super().do_GET()
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        POSTS.append({"path": self.path, "body": body})
        if self.path == "/api/auto-trade/state":
            m = body.get("strategyMode") or body.get("mode")
            if m in ("championship", "standard", "kalman"):
                STATE.update(strategyMode=m, championshipMode=(m == "championship"))
            return self._json(200, STATE)
        if self.path == "/api/kalman/settings":
            SETTINGS.update({k: v for k, v in body.items() if k in KT.DEFAULT_SETTINGS})
            return self._json(200, {"settings": KT.clean_settings(SETTINGS), "live_env": False})
        return self._json(200, {})

class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True; daemon_threads = True

if __name__ == "__main__":
    TS(("127.0.0.1", 8070), H).serve_forever()

#!/usr/bin/env python3
"""
CME-X5 MODEL B — FINAL AUTONOMOUS EXECUTION ENGINE
Runs autonomously on Railway server-side without requiring any browser tab.

Trading Systems:
  S2 POC Reclaim         -> +0.683R Net EV  (PRIMARY PROFIT ENGINE)
  AMD FVG Setup          -> Accumulation/Manipulation/Distribution + FVG
  S7 Gated Continuation  -> +0.34R  Net EV  (SECONDARY, strict gate)

Agent Layer (Pre-Trade Intelligence):
  S/R Agent (40%)        -> Swing-level obstruction analysis on 1H+4H
  POC Pathfinder (30%)   -> Volume Profile POC path analysis
  FVG Impact (30%)       -> Fair Value Gap alignment/opposition scoring
  Confluence Scorer      -> Weighted aggregator: PASS/ADJUST/REJECT

Integrations:
  - Bybit V5 Linear Demo API (Auto order placement with broker-side SL/TP)
  - Railway Headless 24/7 Loop
  - Railway Monitor Tool (monitor.html) synchronizer via auto_trade_state
"""

import os, sys, json, math, time, sqlite3, uuid, urllib.request
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

# ─── Load Environment ─────────────────────────────────────────────────────────
def _load_env():
    env_file = os.path.join(ROOT_DIR, ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("\"'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

_load_env()

from backend_lib.bybit_client import get_client
from backend_lib import auto_trade_state
from backend_lib.championship_engine import ChampionshipDualRegimeEngine
from backend_lib.trading_utils import round_qty, round_price, compute_order_sizing

# Model B Agent Layer
from daemons.agents import SRAgent, POCPathfinderAgent, FVGImpactAgent, ConfluenceScorer

# ─── Configuration ────────────────────────────────────────────────────────────
BASE_URL      = os.environ.get("BYBIT_BASE_URL", "https://api-demo.bybit.com")
POLL_INTERVAL = 10
LOG_FILE      = os.path.join(ROOT_DIR, "scratch", "cme_x5_engine.log")
DB_PATH       = os.path.join(ROOT_DIR, "cme_x5_model_b.db")
SNAPSHOT_PATH = os.path.join(ROOT_DIR, "scratch", "cme_x5_live.json")

SYMBOLS = [
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","LINKUSDT",
    "SEIUSDT","AVAXUSDT","DOGEUSDT","BNBUSDT","ADAUSDT",
    "DOTUSDT","POLUSDT","LTCUSDT","NEARUSDT","APTUSDT"
]

FRICTION_PCT   = 0.0014   # 14.0 bps round-trip
MAX_SPREAD_BPS = 8.0      # reject illiquid books
MIN_RISK_PCT   = 0.0030   # min risk distance to enter
VOL_SURGE_RAT  = 1.10     # bar volume > 1.10x 5-bar avg
VP_LOOKBACK    = 36       # bars for volume profile
VP_BINS        = 30
VP_VALUE_AREA  = 0.70     # 70% of volume = Value Area
ME14_MIN_S7    = 0.50     # Market Efficiency gate for S7
EMA_SEP_MIN_S7 = 0.0025   # EMA21/50 separation gate for S7
TARGET_NOTIONAL_USDT = 50.0  # Conservative institutional size ($5 margin @ 10x)
MAX_CONCURRENT_POS   = 3     # Maximum simultaneous open positions

os.makedirs(os.path.join(ROOT_DIR, "scratch"), exist_ok=True)

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_STRATEGY_LABEL = "CME-X5"

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def log(msg, level="INFO"):
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = f"[{LOG_STRATEGY_LABEL} {level} {ts}] {msg}"
    try:
        print(out, flush=True)
    except Exception:
        try:
            print(out.encode("ascii", errors="replace").decode("ascii"), flush=True)
        except Exception:
            pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(out + "\n")
    except Exception:
        pass

# ─── Market Data ──────────────────────────────────────────────────────────────
def _http(path, retries=2):
    req = urllib.request.Request(BASE_URL + path, headers={"User-Agent":"MASIS-CME-X5/5.0"})
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=7) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == retries: return {}
            time.sleep(0.3)
    return {}

def fetch_klines(sym, interval, limit=100):
    raw = _http(f"/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}")
    bars = []
    for b in reversed(raw.get("result",{}).get("list",[])):
        bars.append({"start":int(b[0]),"open":float(b[1]),"high":float(b[2]),
                     "low":float(b[3]),"close":float(b[4]),"volume":float(b[5])})
    return bars

def fetch_book(sym):
    r = _http(f"/v5/market/orderbook?category=linear&symbol={sym}&limit=5").get("result",{})
    bids, asks = r.get("b",[]), r.get("a",[])
    if not bids or not asks: return None
    bid, ask = float(bids[0][0]), float(asks[0][0])
    mid = (bid+ask)/2
    return {"bid":bid,"ask":ask,"mid":mid,"spread_bps":(ask-bid)/mid*10000 if mid>0 else 0}

# ─── Indicators ───────────────────────────────────────────────────────────────
def ema_series(closes, period):
    if not closes: return []
    k = 2.0/(period+1); res = [closes[0]]*len(closes)
    for i in range(1, len(closes)): res[i] = closes[i]*k + res[i-1]*(1-k)
    return res

def ema_last(closes, period):
    s = ema_series(closes, period); return s[-1] if s else 0.0

def me14(closes, window=14):
    if len(closes) < window+1: return 0.5
    net  = abs(closes[-1] - closes[-window-1])
    path = sum(abs(closes[i]-closes[i-1]) for i in range(len(closes)-window, len(closes)))
    return (net/path) if path>0 else 0.5

def volume_profile(bars):
    if len(bars) < 5: return None
    lows  = [b["low"]  for b in bars]
    highs = [b["high"] for b in bars]
    mn, mx = min(lows), max(highs)
    if mx <= mn: return None
    bs = (mx-mn)/VP_BINS
    bv = [0.0]*VP_BINS
    bc = [mn+(i+0.5)*bs for i in range(VP_BINS)]
    tv = 0.0
    for b in bars:
        vol = b["volume"]; tv += vol
        cr  = max(1e-8, b["high"]-b["low"])
        for i in range(VP_BINS):
            bl, bh = mn+i*bs, mn+(i+1)*bs
            ol, oh = max(b["low"],bl), min(b["high"],bh)
            if oh>ol: bv[i] += vol*(oh-ol)/cr
    if tv<=0: return None
    pi  = max(range(VP_BINS), key=lambda i: bv[i])
    poc = bc[pi]
    tgt = tv*VP_VALUE_AREA; cur = bv[pi]; lo = hi = pi
    while cur<tgt and (lo>0 or hi<VP_BINS-1):
        vu = bv[hi+1] if hi<VP_BINS-1 else 0.0
        vd = bv[lo-1] if lo>0 else 0.0
        if vu>=vd and hi<VP_BINS-1: hi+=1; cur+=vu
        elif lo>0: lo-=1; cur+=vd
        else: break
    return {"poc":poc, "val":bc[lo]-bs/2, "vah":bc[hi]+bs/2}

# ─── Signal Detectors ─────────────────────────────────────────────────────────
def detect_s2(sym, k15, book):
    """S2 POC Reclaim -- Value Area Rotation (PRIMARY ENGINE, +0.683R EV)"""
    if len(k15) < VP_LOOKBACK+5: return None
    closes = [b["close"] for b in k15]
    cur_p  = closes[-1]
    vp     = volume_profile(k15[-VP_LOOKBACK:])
    if not vp: return None
    poc, val, vah = vp["poc"], vp["val"], vp["vah"]
    prev_close    = closes[-2]
    prev_low      = min(b["low"]  for b in k15[-5:-1])
    prev_high     = max(b["high"] for b in k15[-5:-1])
    last          = k15[-1]
    avg_vol       = sum(b["volume"] for b in k15[-6:-1]) / 5.0
    if last["volume"] <= avg_vol * VOL_SURGE_RAT: return None

    # LONG: swept below VAL, now reclaims POC
    if prev_low < val and prev_close <= poc and cur_p > poc:
        stop_p = prev_low * 0.9985
        sl_dist = abs(cur_p - stop_p)
        risk = sl_dist / cur_p
        # Target: Value Area High if viable (>=1.0R), else standard 1.5R extension
        reward_to_vah = (vah - cur_p) if vah else 0
        if reward_to_vah >= sl_dist:
            target_p = vah * 0.999
        else:
            target_p = cur_p + 1.5 * sl_dist

        if target_p > cur_p and risk >= MIN_RISK_PCT:
            return {"situation":"S2_POC_RECLAIM","direction":"LONG","entry_p":cur_p,
                    "stop_p":stop_p,"target_p":target_p,"risk_pct":risk,
                    "poc":poc,"val":val,"vah":vah,"me14":None,"ema21_15m":None,"ema50_15m":None}

    # SHORT: swept above VAH, now reclaims POC
    if prev_high > vah and prev_close >= poc and cur_p < poc:
        stop_p = prev_high * 1.0015
        sl_dist = abs(cur_p - stop_p)
        risk = sl_dist / cur_p
        # Target: Value Area Low if viable (>=1.0R), else standard 1.5R extension
        reward_to_val = (cur_p - val) if val else 0
        if reward_to_val >= sl_dist:
            target_p = val * 1.001
        else:
            target_p = cur_p - 1.5 * sl_dist

        if target_p < cur_p and risk >= MIN_RISK_PCT:
            return {"situation":"S2_POC_RECLAIM","direction":"SHORT","entry_p":cur_p,
                    "stop_p":stop_p,"target_p":target_p,"risk_pct":risk,
                    "poc":poc,"val":val,"vah":vah,"me14":None,"ema21_15m":None,"ema50_15m":None}
    return None

def detect_amd_fvg(sym, k15, book):
    """
    AMD (Accumulation-Manipulation-Distribution) + Fair Value Gap (FVG)
    Confluence with Volume Profile Point of Control (POC).
    """
    if len(k15) < 45: return None
    closes = [b["close"] for b in k15]
    highs  = [b["high"]  for b in k15]
    lows   = [b["low"]   for b in k15]
    cur_p  = closes[-1]
    
    # 1. Accumulation Phase: past 30 bars (excluding recent 5 bars)
    acc_window = k15[-35:-5]
    acc_high   = max(b["high"] for b in acc_window)
    acc_low    = min(b["low"]  for b in acc_window)
    acc_range  = (acc_high - acc_low) / acc_low * 100
    
    if acc_range > 2.5: return None
    
    # 2. ATR(14)
    atrs = []
    for i in range(1, len(k15)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        atrs.append(tr)
    atr = sum(atrs[-14:]) / 14.0 if len(atrs) >= 14 else (highs[-1] - lows[-1])
    if atr <= 0: return None
    
    vp = volume_profile(k15[-VP_LOOKBACK:]) if len(k15) >= VP_LOOKBACK else None
    poc = vp["poc"] if vp else None
    val = vp["val"] if vp else None
    vah = vp["vah"] if vp else None
    
    # 3. Manipulation & FVG
    manip_high = max(highs[-5:])
    if manip_high > acc_high and cur_p < acc_high:
        fvg_gap = lows[-3] - highs[-1]
        if fvg_gap > (atr * 0.10):
            sl_dist  = atr * 2.0
            stop_p   = cur_p + sl_dist
            target_p = cur_p - (sl_dist * 2.0)
            risk     = abs(cur_p - stop_p) / cur_p
            if risk >= MIN_RISK_PCT:
                return {"situation":"AMD_FVG_BEAR","direction":"SHORT","entry_p":cur_p,
                        "stop_p":stop_p,"target_p":target_p,"risk_pct":risk,
                        "poc":poc,"val":val,"vah":vah,"me14":None,"ema21_15m":None,"ema50_15m":None}

    manip_low = min(lows[-5:])
    if manip_low < acc_low and cur_p > acc_low:
        fvg_gap = lows[-1] - highs[-3]
        if fvg_gap > (atr * 0.10):
            sl_dist  = atr * 2.0
            stop_p   = cur_p - sl_dist
            target_p = cur_p + (sl_dist * 2.0)
            risk     = abs(cur_p - stop_p) / cur_p
            if risk >= MIN_RISK_PCT:
                return {"situation":"AMD_FVG_BULL","direction":"LONG","entry_p":cur_p,
                        "stop_p":stop_p,"target_p":target_p,"risk_pct":risk,
                        "poc":poc,"val":val,"vah":vah,"me14":None,"ema21_15m":None,"ema50_15m":None}
    return None

def detect_s7(sym, k15, book):
    """S7 Gated EMA Continuation -- strictly ME14>=0.50 (SECONDARY ENGINE, +0.34R EV)"""
    if len(k15) < 60: return None
    closes = [b["close"] for b in k15]
    cur_p  = closes[-1]
    me     = me14(closes)
    if me < ME14_MIN_S7: return None
    e21    = ema_last(closes, 21)
    e50    = ema_last(closes, 50)
    if abs(e21-e50)/cur_p < EMA_SEP_MIN_S7: return None
    last   = k15[-1]

    # LONG: price > EMA21 > EMA50, bar dipped to EMA21 and reclaimed
    if cur_p > e21 > e50 and last["low"] <= e21 and cur_p >= e21 and cur_p > last["open"]:
        stop_p = e50*0.998; risk = abs(cur_p-stop_p)/cur_p
        if risk >= MIN_RISK_PCT:
            return {"situation":"S7_CME_GATED_CONTINUATION","direction":"LONG","entry_p":cur_p,
                    "stop_p":stop_p,"target_p":cur_p+2*abs(cur_p-stop_p),"risk_pct":risk,
                    "poc":None,"val":None,"vah":None,"me14":me,"ema21_15m":e21,"ema50_15m":e50}
    # SHORT: price < EMA21 < EMA50, bar spiked to EMA21 and rejected
    if cur_p < e21 < e50 and last["high"] >= e21 and cur_p <= e21 and cur_p < last["open"]:
        stop_p = e50*1.002; risk = abs(cur_p-stop_p)/cur_p
        if risk >= MIN_RISK_PCT:
            return {"situation":"S7_CME_GATED_CONTINUATION","direction":"SHORT","entry_p":cur_p,
                    "stop_p":stop_p,"target_p":cur_p-2*abs(cur_p-stop_p),"risk_pct":risk,
                    "poc":None,"val":None,"vah":None,"me14":me,"ema21_15m":e21,"ema50_15m":e50}
    return None

# ─── Database ──────────────────────────────────────────────────────────────────
def db_init():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    c    = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS x5_candidates (
        cand_id TEXT PRIMARY KEY, symbol TEXT, direction TEXT, situation TEXT,
        created_at TEXT, updated_at TEXT, state TEXT,
        entry_p REAL, stop_p REAL, target_p REAL, risk_pct REAL,
        poc REAL, val REAL, vah REAL, me14 REAL, ema21 REAL, ema50 REAL,
        rejection_reason TEXT, payload TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS x5_outcomes (
        trade_id TEXT PRIMARY KEY, cand_id TEXT, symbol TEXT,
        direction TEXT, situation TEXT, entry_time TEXT,
        entry_p REAL, stop_p REAL, target_p REAL, risk_pct REAL,
        exit_time TEXT, exit_p REAL, exit_reason TEXT,
        realized_r REAL DEFAULT 0, is_win INTEGER,
        harvest_hit INTEGER DEFAULT 0, mfe_pct REAL DEFAULT 0,
        mae_pct REAL DEFAULT 0, duration_sec INTEGER DEFAULT 0,
        status TEXT DEFAULT 'ACTIVE')""")
    conn.commit(); conn.close()

def db_upsert(table, row, pk):
    conn = sqlite3.connect(DB_PATH, timeout=15)
    c    = conn.cursor()
    cols = list(row.keys())
    vals = list(row.values())
    placeholders = ",".join(["?"]*len(cols))
    updates = ",".join([f"{k}=excluded.{k}" for k in cols if k != pk])
    c.execute(f"""INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})
                 ON CONFLICT({pk}) DO UPDATE SET {updates}""", vals)
    conn.commit(); conn.close()

# ─── Position Manager ──────────────────────────────────────────────────────────
class PositionManager:
    def __init__(self):
        self.positions   = {}
        self.daily_r     = 0.0
        self.trade_count = 0
        self.client, self.client_err = get_client()
        if self.client:
            log(f"[24H EXECUTOR] Bybit Demo Client connected to {self.client.base_url}")
            try:
                wb = self.client.get_wallet_balance()
                eq = wb.get("result", {}).get("list", [{}])[0].get("totalEquity", "N/A")
                log(f"[24H EXECUTOR] Bybit Live Equity: ${eq} USDT")
            except Exception as e:
                log(f"[24H EXECUTOR] Balance check: {e}")
        else:
            log(f"[24H EXECUTOR WARN] Bybit Client error: {self.client_err}", "WARN")

        # Do not overwrite the user's selected strategy mode here.
        # The dashboard/API persists the authoritative mode in shared KV,
        # and the engine reads it every cycle.

    def is_occupied(self, sym): return sym in self.positions

    def open(self, sym, sig, now_ts):
        # 0. Unified production executor.
        # The active strategy is selected by shared dashboard state; this
        # manager executes whichever strategy generated the candidate.
        # 1. Concurrency & duplicate guards
        if len(self.positions) >= MAX_CONCURRENT_POS:
            log(f"[24H EXECUTOR] Max concurrent positions ({MAX_CONCURRENT_POS}) reached. Skipping {sym}.")
            return

        cur_p = sig["entry_p"]
        side = "Buy" if sig["direction"] == "LONG" else "Sell"

        if self.client:
            try:
                pos_chk = self.client.get_positions(sym)
                live_list = [p for p in pos_chk.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
                if live_list:
                    log(f"[24H EXECUTOR] Already holding live Bybit position for {sym}. Skipping duplicate.")
                    return
            except Exception as e:
                log(f"[24H EXECUTOR] Position check notice: {e}", "WARN")

        # 2. Risk & sizing calculations
        risk_pct   = sig["risk_pct"]
        friction_r = FRICTION_PCT / risk_pct
        cand_id    = f"X5-{sym[:3]}-{now_ts}-{uuid.uuid4().hex[:6].upper()}"
        now_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        qty = compute_order_sizing(sym, cur_p, target_notional=TARGET_NOTIONAL_USDT)
        sl_rounded = round_price(sym, sig["stop_p"])
        tp_rounded = round_price(sym, sig["target_p"])

        # Broker-side SL/TP validity guard
        if sig["direction"] == "LONG":
            if sl_rounded >= cur_p: sl_rounded = round_price(sym, cur_p * 0.985)
            if tp_rounded <= cur_p: tp_rounded = round_price(sym, cur_p * 1.030)
        else:
            if sl_rounded <= cur_p: sl_rounded = round_price(sym, cur_p * 1.015)
            if tp_rounded >= cur_p: tp_rounded = round_price(sym, cur_p * 0.970)

        # 3. Direct 24/7 Bybit Order Execution
        bybit_order_id = None
        if self.client:
            try:
                self.client.set_leverage(sym, 10)
                order_res = self.client.place_order(
                    category="linear",
                    symbol=sym,
                    side=side,
                    order_type="Market",
                    qty=qty,
                    sl=sl_rounded,
                    tp=tp_rounded
                )
                rc = order_res.get("retCode", -1)
                if rc == 0:
                    bybit_order_id = order_res.get("result", {}).get("orderId")
                    log(f"[24H EXECUTOR] ✅ LIVE BYBIT ORDER PLACED! {sym} {side} Qty={qty} SL={sl_rounded} TP={tp_rounded} OrderId={bybit_order_id}")
                    
                    # Update auto_trade_state theses for Railway monitoring tool (monitor.html)
                    try:
                        key = f"{sym}-{side}"
                        theses = auto_trade_state.load().get("theses", {}) or {}
                        theses[key] = {
                            "symbol": sym,
                            "side": side,
                            "direction": sig["direction"],
                            "entryPrice": cur_p,
                            "stopLoss": sl_rounded,
                            "targets": [tp_rounded],
                            "setupName": sig["situation"],
                            "openedAt": int(time.time() * 1000),
                            "isScalp": "S7" in sig["situation"]
                        }
                        auto_trade_state.save(theses=theses, armed=True)
                    except Exception as e_th:
                        log(f"[AUTO_TRADE_STATE] Thesis save note: {e_th}", "WARN")
                else:
                    log(f"[24H EXECUTOR] ❌ Bybit Order Rejected: retCode={rc} retMsg={order_res.get('retMsg')}", "WARN")
            except Exception as e_ord:
                log(f"[24H EXECUTOR ERR] Bybit place_order failed: {e_ord}", "ERROR")

            # Never create a local "active trade" when a live broker order was
            # rejected or its response was unavailable. This prevents phantom
            # Railway positions/trade counts when Bybit declines the order.
            if self.client and not bybit_order_id:
                log(f"[24H EXECUTOR] Local registration aborted for {sym}; "
                    f"no confirmed Bybit order id.", "WARN")
                return

        cand = {"cand_id":cand_id,"symbol":sym,"direction":sig["direction"],
                "situation":sig["situation"],"created_at":now_str,"updated_at":now_str,
                "state":"ACTIVE","entry_p":cur_p,"stop_p":sl_rounded,
                "target_p":tp_rounded,"risk_pct":round(risk_pct,6),
                "poc":sig.get("poc"),"val":sig.get("val"),"vah":sig.get("vah"),
                "me14":sig.get("me14"),"ema21":sig.get("ema21_15m"),"ema50":sig.get("ema50_15m"),
                "rejection_reason":None,"payload":json.dumps(sig)}
        trade = {"trade_id":f"TRD-{cand_id}","cand_id":cand_id,"symbol":sym,
                 "direction":sig["direction"],"situation":sig["situation"],"entry_time":now_str,
                 "entry_p":cur_p,"stop_p":sl_rounded,"target_p":tp_rounded,
                 "risk_pct":round(risk_pct,6),"exit_time":None,"exit_p":None,
                 "exit_reason":None,"realized_r":0.0,"is_win":None,
                 "harvest_hit":0,"mfe_pct":0.0,"mae_pct":0.0,"duration_sec":0,"status":"ACTIVE"}

        try:
            db_upsert("x5_candidates", cand, "cand_id")
            db_upsert("x5_outcomes",   trade, "trade_id")
        except Exception as e:
            log(f"[DB] Open {sym}: {e}", "WARN")

        self.positions[sym] = {
            "cand":cand,"trade":trade,"start_ts":time.time(),
            "entry_p":cur_p,"stop_p":sl_rounded,"target_p":tp_rounded,
            "direction":sig["direction"],"situation":sig["situation"],
            "risk_pct":risk_pct,"friction_r":friction_r,"qty":qty,
            "bybit_order_id":bybit_order_id,
            "best_fav":0.0,"best_adv":0.0,"harvest_hit":False,"bars_held":0
        }
        log(f"  ACTIVE {sig['situation']} {sig['direction']} {sym} | "
            f"entry={cur_p:.5f} stop={sl_rounded:.5f} "
            f"target={tp_rounded:.5f} risk={risk_pct*100:.2f}% | BybitId={bybit_order_id or 'OFFLINE'}")

    def _close(self, sym, pos, cur_p, reason, realized_r, is_win):
        elapsed = int(time.time() - pos["start_ts"])
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        trade   = pos["trade"]
        trade.update({"exit_time":now_str,"exit_p":cur_p,"exit_reason":reason,
                      "realized_r":round(realized_r,4),"is_win":1 if is_win else 0,
                      "harvest_hit":1 if pos["harvest_hit"] else 0,
                      "mfe_pct":round(pos["best_fav"]*100,3),
                      "mae_pct":round(pos["best_adv"]*100,3),
                      "duration_sec":elapsed,"status":"CLOSED"})
        cand = pos["cand"]
        cand["state"] = "CLOSED_WIN" if is_win else "CLOSED_LOSS"
        cand["updated_at"] = now_str
        try:
            db_upsert("x5_candidates", cand, "cand_id")
            db_upsert("x5_outcomes",   trade, "trade_id")
        except Exception as e:
            log(f"[DB] Close {sym}: {e}", "WARN")

        # Close on Bybit if still open
        if self.client:
            try:
                side = "Buy" if pos["direction"] == "LONG" else "Sell"
                p_res = self.client.get_positions(sym)
                live_list = [p for p in p_res.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
                if live_list:
                    sz = live_list[0].get("size")
                    c_res = self.client.close_position("linear", sym, side, sz)
                    log(f"[24H EXECUTOR] 🛑 Closed Bybit {sym} position: {c_res.get('retMsg')}")
                
                # Remove thesis from auto_trade_state
                key = f"{sym}-{side}"
                theses = auto_trade_state.load().get("theses", {}) or {}
                if key in theses:
                    theses.pop(key, None)
                    auto_trade_state.save(theses=theses)
            except Exception as e_cl:
                log(f"[24H EXECUTOR ERR] Close cleanup error {sym}: {e_cl}", "WARN")

        self.daily_r += realized_r; self.trade_count += 1
        icon = "WIN" if is_win else "LOSS"
        log(f"  {icon}  {pos['situation']} {pos['direction']} {sym} | "
            f"R={realized_r:+.3f} ({reason}) [DailyR={self.daily_r:+.3f} Trades={self.trade_count}]")
        del self.positions[sym]

    def update(self, sym, cur_p):
        if sym not in self.positions: return
        pos = self.positions[sym]
        d   = pos["direction"]; ep = pos["entry_p"]
        fav = ((cur_p-ep)/ep) if d=="LONG" else ((ep-cur_p)/ep)
        adv = ((ep-cur_p)/ep) if d=="LONG" else ((cur_p-ep)/ep)
        pos["best_fav"] = max(pos["best_fav"], fav)
        pos["best_adv"] = max(pos["best_adv"], adv)
        elapsed_sec = max(1.0, time.time() - pos["start_ts"])
        pos["bars_held"] = max(1, int(elapsed_sec // 900))  # 1 bar = 15m (900s)
        r_pct = pos["risk_pct"]; fr = pos["friction_r"]

        # Check if Bybit broker SL/TP filled in exchange engine
        if self.client and pos.get("bybit_order_id"):
            try:
                p_res = self.client.get_positions(sym)
                live_bybit = [p for p in p_res.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
                if not live_bybit and elapsed_sec >= 15:
                    # Position is gone on Bybit! Fetch closed PnL
                    cpnl_res = self.client.get_closed_pnl(limit=5)
                    hit = next((x for x in cpnl_res.get("result", {}).get("list", []) if x.get("symbol") == sym), None)
                    if hit:
                        pnl = float(hit.get("closedPnl", 0))
                        is_w = pnl > 0
                        reason = "BROKER_TP" if is_w else "BROKER_SL"
                        r_mult = 2.0 if is_w else -1.0
                        self._close(sym, pos, cur_p, reason, r_mult, is_w)
                        return
            except Exception:
                pass

        # Timeout: 48 bars (12 actual hours = 43,200 seconds)
        if elapsed_sec > (12 * 3600):
            self._close(sym, pos, cur_p, "TIMEOUT_12H", (fav/r_pct)-fr, fav>0); return

        sp = pos["stop_p"]; tp = pos["target_p"]
        sit = pos["situation"]

        # ── Universal Dynamic Profit Protection & Breakeven Lock ─────────────
        # If trade reaches +0.70R (or +0.80% in price), lock stop to Breakeven (+ fee buffer)
        r_mult = fav / max(r_pct, 1e-6)
        if r_mult >= 0.70 or fav >= 0.0080:
            if not pos.get("be_locked"):
                be_p = ep * 1.0010 if d == "LONG" else ep * 0.9990
                should_update = (d == "LONG" and be_p > pos["stop_p"]) or (d == "SHORT" and be_p < pos["stop_p"])
                if should_update:
                    pos["stop_p"] = be_p
                    pos["be_locked"] = True
                    log(f"  [PROFIT LOCK] {sym} reached +{r_mult:.2f}R (+{fav*100:.2f}%). Stop ratcheted to BREAKEVEN {be_p:.5f}")
                    if self.client and pos.get("bybit_order_id"):
                        try:
                            be_rnd = round_price(sym, be_p)
                            self.client.set_trading_stop(category="linear", symbol=sym, stop_loss=be_rnd)
                            log(f"  [BYBIT] Broker-side stop ratcheted to BREAKEVEN {be_rnd}")
                        except Exception as e_be:
                            log(f"  [BYBIT ERR] Breakeven stop sync failed {sym}: {e_be}", "WARN")

        # If trade reaches +1.20R (or +1.50% in price), trail stop to lock +0.50R profit
        if r_mult >= 1.20 or fav >= 0.0150:
            if not pos.get("trail_locked"):
                trail_p = ep + (r_pct * ep * 0.50) if d == "LONG" else ep - (r_pct * ep * 0.50)
                should_trail = (d == "LONG" and trail_p > pos["stop_p"]) or (d == "SHORT" and trail_p < pos["stop_p"])
                if should_trail:
                    pos["stop_p"] = trail_p
                    pos["trail_locked"] = True
                    log(f"  [PROFIT LOCK +0.5R] {sym} reached +{r_mult:.2f}R. Trailing stop locked at {trail_p:.5f}")
                    if self.client and pos.get("bybit_order_id"):
                        try:
                            tr_rnd = round_price(sym, trail_p)
                            self.client.set_trading_stop(category="linear", symbol=sym, stop_loss=tr_rnd)
                            log(f"  [BYBIT] Broker-side stop updated to lock +0.5R {tr_rnd}")
                        except Exception as e_tr:
                            log(f"  [BYBIT ERR] Trail stop sync failed {sym}: {e_tr}", "WARN")

        # ── Exit Conditions ──────────────────────────────────────────────────
        # 1. Target Hit
        tp_hit = (d == "LONG" and cur_p >= tp) or (d == "SHORT" and cur_p <= tp)
        if tp_hit:
            tgt_r = max(1.0, min(3.5, abs(tp - ep) / (r_pct * ep)))
            reason = "TARGET_VA" if sit == "S2_POC_RECLAIM" else ("AMD_TP_2R" if sit.startswith("AMD_FVG") else "TARGET_TP")
            self._close(sym, pos, cur_p, reason, tgt_r - fr, True)
            return

        # 2. Stop Hit (Hard Stop, Breakeven Lock, or Trailing Stop)
        sl_hit = (d == "LONG" and cur_p <= pos["stop_p"]) or (d == "SHORT" and cur_p >= pos["stop_p"])
        if sl_hit:
            if pos.get("trail_locked"):
                self._close(sym, pos, cur_p, "PROT_TRAIL_STOP", 0.50 - fr, True)
                return
            elif pos.get("be_locked"):
                self._close(sym, pos, cur_p, "PROT_BE_STOP", 0.05 - fr, True)
                return
            else:
                self._close(sym, pos, cur_p, "HARD_STOP", -1.0 - fr, False)
                return

    def snapshot(self):
        return {s:{"direction":p["direction"],"situation":p["situation"],
                   "entry_p":p["entry_p"],"stop_p":p["stop_p"],"target_p":p["target_p"],
                   "poc":p.get("poc"),"val":p.get("val"),"vah":p.get("vah"),
                   "mfe_pct":round(p["best_fav"]*100,3),"mae_pct":round(p["best_adv"]*100,3),
                   "bars_held":p["bars_held"],"harvest":p["harvest_hit"],
                   "bybit_order_id":p.get("bybit_order_id")}
                for s,p in self.positions.items()}

# ─── Main Engine ───────────────────────────────────────────────────────────────
class CMEX5Engine:
    def __init__(self):
        db_init()
        self.cycle = 0
        self.scanned_signals = {}
        self.strategy_mode = "standard"
        self.scan_metrics = {}
        self.championship_engine = ChampionshipDualRegimeEngine()

        # Resolve the persisted dashboard selection BEFORE constructing the
        # executor so startup logs and runtime behavior agree immediately.
        self._refresh_strategy_mode(force=True)

        self.pm    = PositionManager()

        # Model B Agent Layer
        self.sr_agent  = SRAgent()
        self.poc_agent = POCPathfinderAgent()
        self.fvg_agent = FVGImpactAgent()
        self.scorer    = ConfluenceScorer()

        log("=" * 70)
        log("  MASIS — UNIFIED AUTONOMOUS EXECUTION ENGINE")
        log("  Active Strategy: " + ("CHAMPIONSHIP DUAL-REGIME" if self.strategy_mode == "championship" else "CME-X5 MODEL B PURE"))
        log("  Strategy switching is runtime-controlled by dashboard state")
        log("  Execution: Direct Bybit Demo V5 Linear | 10x Leverage | Auto SL/TP")
        log("=" * 70)

    def _refresh_strategy_mode(self, force=False):
        """Read the shared dashboard strategy selection and apply it live."""
        global LOG_STRATEGY_LABEL
        try:
            state = auto_trade_state.load() or {}
        except Exception as e:
            log(f"[MODE WARN] Could not read auto-trade state: {e}", "WARN")
            state = {}

        mode = str(state.get("strategyMode") or "").strip().lower()
        if mode == "championship" or bool(state.get("championshipMode")):
            mode = "championship"
        else:
            # The current UI uses 'standard' for CME-X5 Pure. Other legacy
            # modes remain in the backend but are not selected by this switch.
            mode = "standard"

        if force or mode != self.strategy_mode:
            previous = self.strategy_mode
            self.strategy_mode = mode
            LOG_STRATEGY_LABEL = "CHAMPIONSHIP" if mode == "championship" else "CME-X5"
            # Signal candidates belong to the active strategy. Never carry
            # stale setups from the previous mode into the new mode.
            if hasattr(self, "scanned_signals") and previous != mode:
                self.scanned_signals.clear()
            if force:
                log(f"[MODE] Active strategy at startup: {mode.upper()}")
            else:
                log(f"[MODE] Strategy switched: {previous.upper()} -> {mode.upper()}")

        return state

    def _scan_championship(self, sym, ts, btc_bars):
        """Scan one symbol using the dedicated Championship Dual-Regime engine."""
        k15 = btc_bars if sym == "BTCUSDT" else fetch_klines(sym, "15", 250)
        if len(k15) < 200 or len(btc_bars) < 200:
            self.scan_metrics["data_reject"] += 1
            return

        self.scan_metrics["data_ok"] += 1
        candidate = self.championship_engine.scan_candidate(sym, k15, btc_bars)

        if not candidate:
            self.scan_metrics["no_signal"] += 1
            return

        self.scan_metrics["signals"] += 1
        entry = float(candidate["entry_price"])
        stop = float(candidate["stop_loss"])
        tp1 = float(candidate["tp1"])
        tp2 = float(candidate["tp2"])
        risk_pct = abs(entry - stop) / entry

        sig = {
            "situation": candidate["archetype"],
            "direction": candidate["direction"],
            "entry_p": entry,
            "stop_p": stop,
            "target_p": tp2,
            "tp1_p": tp1,
            "tp2_p": tp2,
            "risk_pct": risk_pct,
            "poc": None,
            "val": None,
            "vah": None,
            "me14": None,
            "ema21_15m": None,
            "ema50_15m": None,
            "strategy_mode": "championship",
            "championship_regime": candidate.get("regime"),
            "championship_archetype": candidate.get("archetype"),
            "championship_candidate": candidate,
        }

        log(f"[SIGNAL] 🏆 {sym} {candidate.get('regime')} {candidate['direction']} | "
            f"{candidate['archetype']} | Entry={entry:.6f} "
            f"SL={stop:.6f} TP1={tp1:.6f} TP2={tp2:.6f} "
            f"Risk={risk_pct*100:.2f}% RVOL={candidate.get('rvol','-')} RSI={candidate.get('rsi','-')}")

        self.scanned_signals[sym] = {
            "symbol": sym,
            "direction": candidate["direction"],
            "situation": candidate["archetype"],
            "entry_p": entry,
            "stop_p": stop,
            "target_p": tp2,
            "tp1_p": tp1,
            "tp2_p": tp2,
            "agent_score": None,
            "agent_decision": "CHAMPIONSHIP_PASS",
            "strategy_mode": "championship",
            "regime": candidate.get("regime"),
            "detected_at": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        }

        if not self.pm.is_occupied(sym):
            self.pm.open(sym, sig, ts)

    def _scan(self, sym, ts):
        k15  = fetch_klines(sym, "15", 100)
        book = fetch_book(sym)
        if len(k15) < VP_LOOKBACK+10 or not book:
            self.scan_metrics["data_reject"] += 1
            return
        self.scan_metrics["data_ok"] += 1

        if book["spread_bps"] > MAX_SPREAD_BPS:
            self.scan_metrics["spread_reject"] += 1
            return

        # FINAL MODEL B: only the audited S2 + gated S7 setups may create orders.
        # AMD/FVG remains research-only and cannot silently become a live order source.
        s2_sig = detect_s2(sym, k15, book)
        s7_sig = detect_s7(sym, k15, book)
        sig = s2_sig or s7_sig
        if not sig:
            self.scan_metrics["no_signal"] += 1
            return

        self.scan_metrics["signals"] += 1
        detector_name = "S2_POC_RECLAIM" if s2_sig else "S7_GATED_CONTINUATION"
        log(f"[SIGNAL] {sym} {detector_name} {sig['direction']} | "
            f"Entry={sig['entry_p']:.6f} SL={sig['stop_p']:.6f} "
            f"TP={sig['target_p']:.6f} Risk={sig['risk_pct']*100:.2f}%")

        # ─── Model B: Agent Layer Pre-Validation ─────────────────────────
        try:
            k1h = fetch_klines(sym, "60", 60)
            k4h = fetch_klines(sym, "240", 50)
        except Exception:
            k1h, k4h = [], []

        vp = volume_profile(k15[-VP_LOOKBACK:]) if len(k15) >= VP_LOOKBACK else None

        try:
            sr_result  = self.sr_agent.analyze(sym, sig, k1h, k4h, k15)
        except Exception as e:
            log(f"[AGENT ERR] S/R agent failed for {sym}: {e}", "WARN")
            sr_result  = {"score": 0.7, "adjustment": None, "reason": f"S/R agent error: {e}"}

        try:
            poc_result = self.poc_agent.analyze(sym, sig, vp)
        except Exception as e:
            log(f"[AGENT ERR] POC agent failed for {sym}: {e}", "WARN")
            poc_result = {"score": 0.7, "adjustment": None, "reason": f"POC agent error: {e}"}

        try:
            fvg_result = self.fvg_agent.analyze(sym, sig, k15)
        except Exception as e:
            log(f"[AGENT ERR] FVG agent failed for {sym}: {e}", "WARN")
            fvg_result = {"score": 0.7, "adjustment": None, "reason": f"FVG agent error: {e}"}

        verdict = self.scorer.evaluate({
            "sr":  sr_result,
            "poc": poc_result,
            "fvg": fvg_result
        })

        log(f"[AGENT] {sym} {sig['situation']} {sig['direction']} -> "
            f"Score={verdict['final_score']:.3f} Decision={verdict['decision']}")
        if self.cycle <= 3 or verdict["decision"] != "PASS":
            log(f"[AGENT REPORT]\n{verdict['report']}")

        if verdict["decision"] == "REJECT":
            self.scan_metrics["agent_reject"] += 1
            log(f"[AGENT] REJECTED {sym} -- agents blocked trade (score={verdict['final_score']:.3f})")
            return

        # Apply TP/SL adjustments from agents
        if verdict["adjustments"]:
            if "target_p" in verdict["adjustments"]:
                old_tp = sig["target_p"]
                sig["target_p"] = verdict["adjustments"]["target_p"]
                log(f"[AGENT] TP adjusted: {old_tp:.5f} -> {sig['target_p']:.5f}")
            if "stop_p" in verdict["adjustments"]:
                old_sl = sig["stop_p"]
                sig["stop_p"] = verdict["adjustments"]["stop_p"]
        sig["agent_score"] = verdict["final_score"]
        sig["agent_decision"] = verdict["decision"]
        sig["agent_report"] = verdict["report"]
        # ─── End Agent Layer ──────────────────────────────────────────────

        self.scanned_signals[sym] = {
            "symbol": sym,
            "direction": sig["direction"],
            "situation": sig["situation"],
            "entry_p": sig["entry_p"],
            "stop_p": sig["stop_p"],
            "target_p": sig["target_p"],
            "poc": sig.get("poc"),
            "val": sig.get("val"),
            "vah": sig.get("vah"),
            "mfe_pct": 0.0,
            "agent_score": verdict["final_score"],
            "agent_decision": verdict["decision"],
            "detected_at": datetime.now(timezone.utc).strftime("%H:%M:%S")
        }
        if not self.pm.is_occupied(sym):
            self.pm.open(sym, sig, ts)

    def run_cycle(self):
        self.cycle += 1
        ts  = datetime.now(timezone.utc).strftime("%H%M%S")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Runtime mode is shared across browser/server/daemon processes.
        self._refresh_strategy_mode()

        self.scan_metrics = {
            "symbols": len(SYMBOLS),
            "data_ok": 0,
            "data_reject": 0,
            "spread_reject": 0,
            "no_signal": 0,
            "signals": 0,
            "agent_reject": 0,
        }
        champ_regime = "SCANNING"
        champ_btc_price = 0.0
        champ_btc_200 = 0.0

        if self.cycle % 6 == 1:
            log(f"Cycle #{self.cycle} | Mode:{self.strategy_mode.upper()} "
                f"| Open:{len(self.pm.positions)} | Trades:{self.pm.trade_count} "
                f"| DailyR:{self.pm.daily_r:+.3f}")

        # Update open positions
        for sym in list(self.pm.positions):
            try:
                b = fetch_book(sym)
                if b: self.pm.update(sym, b["mid"])
            except Exception as e:
                log(f"[UPD ERR] {sym}: {e}", "WARN")

        if self.strategy_mode == "championship":
            # Championship uses BTC 15m as the global macro regime anchor.
            btc_bars = fetch_klines("BTCUSDT", "15", 250)
            if len(btc_bars) < 200:
                self.scan_metrics["data_reject"] += len(SYMBOLS)
                log("[CHAMPIONSHIP] BTC 15m regime anchor unavailable; skipping cycle.", "WARN")
            else:
                champ_regime, champ_btc_price, champ_btc_200 = self.championship_engine.evaluate_regime(btc_bars)
                log(f"[REGIME] BTC 15m: {champ_regime} | Price={champ_btc_price:.2f} | EMA200={champ_btc_200:.2f}")
                for sym in SYMBOLS:
                    try:
                        self._scan_championship(sym, ts, btc_bars)
                    except Exception as e:
                        log(f"[CHAMPIONSHIP SCAN ERR] {sym}: {e}", "WARN")
        else:
            # CME-X5 Pure: scan S2 + S7 through the Model B agent layer.
            for sym in SYMBOLS:
                try:
                    self._scan(sym, ts)
                except Exception as e:
                    log(f"[SCAN ERR] {sym}: {e}", "WARN")

        if self.cycle % 6 == 1:
            log(f"[SCAN SUMMARY] Mode={self.strategy_mode.upper()} "
                f"Symbols={self.scan_metrics['symbols']} "
                f"DataOK={self.scan_metrics['data_ok']} "
                f"SpreadReject={self.scan_metrics['spread_reject']} "
                f"NoSignal={self.scan_metrics['no_signal']} "
                f"Signals={self.scan_metrics['signals']} "
                f"AgentReject={self.scan_metrics['agent_reject']}")

        # Merge active positions + scanned setups for Radar display
        radar_display = dict(self.pm.snapshot())
        for sym, sig in self.scanned_signals.items():
            if sym not in radar_display:
                radar_display[sym] = sig

        # Snapshot for dashboard & monitor
        try:
            with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "engine":"Championship Dual-Regime" if self.strategy_mode == "championship" else "CME-X5 Model B",
                    "strategy_mode": self.strategy_mode,
                    "status":"ACTIVE_24H_EXECUTOR",
                    "version":"B.2.0",
                    "updated_at":now,
                    "cycle":self.cycle,
                    "trade_count":self.pm.trade_count,
                    "daily_r":round(self.pm.daily_r,4),
                    "open_count":len(self.pm.positions),
                    "positions":radar_display
                }, f, indent=2)
        except Exception:
            pass

        # Keep the legacy Championship endpoint synchronized with the real
        # unified engine instead of serving a permanently inactive/stale file.
        try:
            champ_file = os.path.join(ROOT_DIR, "scratch", "championship_live_state.json")
            champ_signals = [
                v for v in self.scanned_signals.values()
                if v.get("strategy_mode") == "championship"
            ]
            with open(champ_file, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": int(time.time()),
                    "mode_active": self.strategy_mode == "championship",
                    "strategy_mode": self.strategy_mode,
                    "btc_price": champ_btc_price,
                    "btc_200_ema": champ_btc_200,
                    "btc_dist_200_pct": ((champ_btc_price - champ_btc_200) / champ_btc_200 * 100.0) if champ_btc_200 else 0.0,
                    "macro_regime": champ_regime,
                    "active_engine": "CHAMPIONSHIP DUAL-REGIME" if self.strategy_mode == "championship" else "CME-X5 MODEL B",
                    "signals_detected": champ_signals,
                    "scan_metrics": self.scan_metrics,
                    "last_updated": now,
                }, f, indent=2)
        except Exception:
            pass

    def start(self):
        log("24/7 autonomous execution loop running.")
        while True:
            try: self.run_cycle()
            except KeyboardInterrupt: log("Shutdown."); break
            except Exception as e: log(f"[CYCLE ERR] {e}", "ERROR")
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    CMEX5Engine().start()

#!/usr/bin/env python3
"""CME-X4 live mathematical market-state research collector.

Research-only: never places or modifies orders.
Collects public Bybit Demo-market data and records mathematical state plus
future barrier outcomes so the state hypothesis can be tested forward.
"""
import json, math, os, time, urllib.request

BASE = "https://api-demo.bybit.com"
SYMBOLS = os.getenv("CME_X4_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,LINKUSDT,AVAXUSDT,DOGEUSDT,ARBUSDT").split(",")
INTERVAL_SEC = int(os.getenv("CME_X4_INTERVAL_SEC", "60"))
HORIZONS = {"5m": 5*60, "15m": 15*60, "30m": 30*60, "60m": 60*60}
BARRIERS = [(0.003, 0.002), (0.005, 0.003), (0.01, 0.005)]
PENDING = []


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "MASIS-CME-X4/1.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode())


def klines(sym, interval, limit=100):
    d = get(f"/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}")
    out=[]
    for x in reversed(d.get("result",{}).get("list",[])):
        out.append({"ts":int(x[0]),"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4]),"v":float(x[5])})
    return out


def book(sym):
    d=get(f"/v5/market/orderbook?category=linear&symbol={sym}&limit=25")
    r=d.get("result",{})
    bids=[(float(x[0]),float(x[1])) for x in r.get("b",[])]
    asks=[(float(x[0]),float(x[1])) for x in r.get("a",[])]
    if not bids or not asks: return {}
    bid,ask=bids[0][0],asks[0][0]; mid=(bid+ask)/2
    def imb(n):
        b=sum(x[1] for x in bids[:n]); a=sum(x[1] for x in asks[:n])
        return (b-a)/(b+a) if b+a else 0.0
    return {"spread_bps":(ask-bid)/mid*10000,"imb5":imb(5),"imb25":imb(25),"mid":mid}


def trades(sym):
    d=get(f"/v5/market/recent-trade?category=linear&symbol={sym}&limit=100")
    rows=d.get("result",{}).get("list",[])
    buy=sell=0.0
    for x in rows:
        q=float(x.get("size",0))
        if str(x.get("side","")).lower()=="buy": buy+=q
        else: sell+=q
    total=buy+sell
    return {"flow_imb":(buy-sell)/total if total else 0.0,"trade_count":len(rows)}


def slope_r2(vals):
    n=len(vals)
    if n<5: return 0.0,0.0
    ys=[math.log(max(v,1e-12)) for v in vals]
    mx=(n-1)/2; my=sum(ys)/n
    den=sum((i-mx)**2 for i in range(n))
    b=sum((i-mx)*(y-my) for i,y in enumerate(ys))/den if den else 0.0
    pred=[my+b*(i-mx) for i in range(n)]
    ssr=sum((ys[i]-pred[i])**2 for i in range(n)); sst=sum((y-my)**2 for y in ys)
    r2=1-ssr/sst if sst>0 else 0.0
    return b*10000,r2


def efficiency(bars, n=20):
    w=bars[-n:]
    if len(w)<3: return 0.0
    net=abs(w[-1]["c"]-w[0]["c"])
    path=sum(abs(w[i]["c"]-w[i-1]["c"]) for i in range(1,len(w)))
    return net/path if path>0 else 0.0


def vol_pct(bars,n=20):
    rs=[abs(math.log(max(b["c"],1e-12)/max(b["o"],1e-12))) for b in bars[-min(len(bars),n+1):]]
    if not rs:return 0.0
    cur=rs[-1]; return sum(x<=cur for x in rs)/len(rs)


def state(sym):
    k={tf:klines(sym,tf,100) for tf in ("1","5","15","60")}
    b=book(sym); tr=trades(sym); s={}; slopes=[]
    for tf in ("1","5","15","60"):
        bars=k[tf]; sl,r2=slope_r2([x["c"] for x in bars[-30:]])
        slopes.append(sl)
        s[f"slope_{tf}"]=round(sl,5); s[f"r2_{tf}"]=round(r2,4); s[f"eff_{tf}"]=round(efficiency(bars),4); s[f"volpct_{tf}"]=round(vol_pct(bars),4)
    last=k["5"][-1]; prev=k["5"][-2]; rng=max(last["h"]-last["l"],1e-12)
    s.update({"price":last["c"],"wick_up":round((last["h"]-max(last["o"],last["c"]))/rng,4),"wick_down":round((min(last["o"],last["c"])-last["l"])/rng,4),"body_eff":round(abs(last["c"]-last["o"])/rng,4),"vol_ratio":round(last["v"]/(sum(x["v"] for x in k["5"][-21:-1])/20),4)})
    prev_rng=max(prev["h"]-prev["l"],1e-12); s["prev_body_eff"]=round(abs(prev["c"]-prev["o"])/prev_rng,4)
    s["slope_coherence"]=round(sum(1 if x>0 else -1 if x<0 else 0 for x in slopes)/4,4)
    s["curvature_5_15"]=round(slopes[1]-slopes[2],5)
    s["book_imb5"]=round(b.get("imb5",0),4); s["book_imb25"]=round(b.get("imb25",0),4); s["spread_bps"]=round(b.get("spread_bps",0),4)
    s["taker_flow_imb"]=round(tr.get("flow_imb",0),4); s["trade_count"]=tr.get("trade_count",0)
    s["market_efficiency"]=s["eff_5"]
    s["pressure_efficiency"]=round(abs(math.log(max(last["c"],1e-12)/max(last["o"],1e-12)))/(last["v"]+1e-12),10)
    return s


def log(obj):
    print(json.dumps(obj,separators=(",",":"),sort_keys=True),flush=True)


def settle(now):
    global PENDING
    keep=[]
    for p in PENDING:
        if now-p["ts"] < p["horizon_sec"]:
            keep.append(p); continue
        try:
            bars=klines(p["symbol"],"1",min(100,int(p["horizon_sec"]/60)+5))
            bars=[b for b in bars if b["ts"] >= p["ts"]*1000]
            if not bars:
                keep.append(p); continue
            close=bars[-1]["c"]
            for tp,sl in BARRIERS:
                outcome="NEITHER"; hit_ts=None
                for b in bars:
                    hi=(b["h"]-p["entry"])/p["entry"]; lo=(b["l"]-p["entry"])/p["entry"]
                    if p["direction"]=="SHORT": hi,lo=-lo,-hi
                    tp_hit=hi>=tp; sl_hit=lo<=-sl
                    if tp_hit or sl_hit:
                        outcome="AMBIGUOUS_SAME_BAR" if tp_hit and sl_hit else ("TP_FIRST" if tp_hit else "SL_FIRST")
                        hit_ts=b["ts"]; break
                signed=((close-p["entry"])/p["entry"]) * (1 if p["direction"]=="LONG" else -1)
                log({"type":"CME_X4_OUTCOME","ts":now,"symbol":p["symbol"],"direction":p["direction"],"horizon":p["horizon"],"tp":tp,"sl":sl,"entry":p["entry"],"close":close,"signed_return":signed,"outcome":outcome,"hit_ts":hit_ts,"state_id":p["state_id"]})
        except Exception as e:
            log({"type":"CME_X4_OUTCOME_ERROR","ts":now,"symbol":p["symbol"],"error":str(e),"state_id":p["state_id"]})
    PENDING=keep


def main():
    log({"type":"CME_X4_START","base":BASE,"symbols":SYMBOLS,"interval_sec":INTERVAL_SEC,"research_only":True})
    while True:
        started=time.time(); now=int(time.time())
        for sym in SYMBOLS:
            try:
                s=state(sym); state_id=f"{sym}:{now}"
                direction="LONG" if (s["slope_5"]+s["slope_15"]>0 and s["slope_coherence"]>0) else "SHORT" if (s["slope_5"]+s["slope_15"]<0 and s["slope_coherence"]<0) else "NEUTRAL"
                log({"type":"CME_X4_STATE","ts":now,"symbol":sym,"state_id":state_id,"direction":direction,"state":s})
                if direction!="NEUTRAL":
                    for horizon,hsec in HORIZONS.items():
                        PENDING.append({"ts":now,"symbol":sym,"entry":s["price"],"direction":direction,"horizon":horizon,"horizon_sec":hsec,"state_id":state_id})
            except Exception as e:
                log({"type":"CME_X4_ERROR","ts":now,"symbol":sym,"error":str(e)})
        settle(now)
        time.sleep(max(2,INTERVAL_SEC-(time.time()-started)))

if __name__=='__main__': main()

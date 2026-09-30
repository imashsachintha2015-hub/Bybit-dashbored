import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
SYMS="BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR APT SUI ARB OP ATOM FIL TRX BCH UNI AAVE INJ TIA SEI WLD ONDO HBAR ETC ICP RUNE STX TON POL CRV MKR".split()
CFG={"1H":(3600,900*24)}
def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=15) as r: return json.loads(r.read())
        except Exception: time.sleep(1+i)
    return {}
def fetch(sym,bar):
    need=CFG[bar][1]; out={}; after=""
    while len(out)<need:
        d=get(f"https://www.okx.com/api/v5/market/history-candles?instId={sym}-USDT-SWAP&bar={bar}&limit=100"+(f"&after={after}" if after else "")).get("data",[])
        if not d: break
        for b in d:
            if b[8]=="1": out[int(b[0])]=[int(b[0]),float(b[1]),float(b[2]),float(b[3]),float(b[4]),float(b[6])]
        after=d[-1][0]; time.sleep(0.1)
    return sorted(out.values())
def job(a):
    s,b=a; d=fetch(s,b); json.dump(d,open(f"d4/{s}_{b}.json","w")); return s,b,len(d)
with ThreadPoolExecutor(6) as ex:
    for r in ex.map(job,[(s,b) for s in SYMS for b in ("1H",)]): print(r,flush=True)

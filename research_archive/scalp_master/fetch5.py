import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=15) as r: return json.loads(r.read())
        except Exception: time.sleep(1+i)
    return {}
def fetch(sym,bar,days):
    per={"5m":288,"1m":1440}[bar]; need=days*per; out={}; after=""
    while len(out)<need:
        d=get(f"https://www.okx.com/api/v5/market/history-candles?instId={sym}-USDT-SWAP&bar={bar}&limit=100"+(f"&after={after}" if after else "")).get("data",[])
        if not d: break
        for b in d:
            if b[8]=="1": out[int(b[0])]=[int(b[0]),float(b[1]),float(b[2]),float(b[3]),float(b[4]),float(b[6])]
        after=d[-1][0]; time.sleep(0.08)
    return sorted(out.values())
def job(a):
    s,bar,days,folder=a; d=fetch(s,bar,days); json.dump(d,open(f"{folder}/{s}_{bar}.json","w")); return s,bar,len(d)
jobs=[(s,"5m",120,"s5") for s in "BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR APT SUI ARB OP".split()]+[(s,"1m",30,"s1") for s in "BTC ETH SOL XRP DOGE LINK ADA AVAX".split()]
with ThreadPoolExecutor(6) as ex:
    for r in ex.map(job,jobs): print(r,flush=True)

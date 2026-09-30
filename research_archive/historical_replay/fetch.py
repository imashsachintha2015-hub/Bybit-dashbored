import json, sys, time, urllib.request, os
from concurrent.futures import ThreadPoolExecutor
SYMS="BTC ETH SOL XRP LINK SEI AVAX DOGE BNB ADA DOT POL LTC NEAR APT TAO TIA WLD ONDO".split()
DAYS=int(sys.argv[1]) if len(sys.argv)>1 else 120
BARS={"15m":900,"1H":3600,"4H":14400}
def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"x"}),timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            time.sleep(1+i)
    return {}
def fetch(sym, bar):
    inst=f"{sym}-USDT-SWAP"; need=DAYS*86400//BARS[bar]; out={}; after=""
    while len(out)<need:
        u=f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100"+(f"&after={after}" if after else "")
        d=get(u).get("data",[])
        if not d: break
        for b in d:
            if b[8]=="1": out[int(b[0])]=[int(b[0]),float(b[1]),float(b[2]),float(b[3]),float(b[4]),float(b[6])]
        after=d[-1][0]
        time.sleep(0.12)
    return sorted(out.values())
def job(a):
    s,b=a; d=fetch(s,b)
    json.dump(d,open(f"data/{s}_{b}.json","w")); return s,b,len(d)
jobs=[(s,b) for s in SYMS for b in ("15m","1H","4H")]
with ThreadPoolExecutor(4) as ex:
    for r in ex.map(job,jobs): print(r,flush=True)

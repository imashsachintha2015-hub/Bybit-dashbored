import json, sys, time, urllib.request, os
from concurrent.futures import ThreadPoolExecutor
SYMS="BTC ETH SOL XRP LINK SEI AVAX DOGE BNB ADA DOT POL LTC NEAR APT TAO TIA WLD ONDO".split()
DAYS=int(sys.argv[1]) if len(sys.argv)>1 else 120
SOURCE=sys.argv[2] if len(sys.argv)>2 else "okx"   # "okx" or "bybit" (public kline endpoint, no API key)
BYBIT_IV={"15m":"15","1H":"60","4H":"240"}
BARS={"15m":900,"1H":3600,"4H":14400}
def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"x"}),timeout=15) as r:
                return json.loads(r.read())
        except Exception as e:
            time.sleep(1+i)
    return {}
def fetch_bybit(sym, bar):
    need=DAYS*86400//BARS[bar]; out={}; end=int(time.time()*1000)
    while len(out)<need:
        u=f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}USDT&interval={BYBIT_IV[bar]}&limit=1000&end={end}"
        d=get(u).get("result",{}).get("list",[])
        if not d: break
        for b in d: out[int(b[0])]=[int(b[0]),float(b[1]),float(b[2]),float(b[3]),float(b[4]),float(b[5])]
        end=int(d[-1][0])-1
        time.sleep(0.12)
    now=int(time.time()*1000)
    return sorted(v for v in out.values() if v[0]+BARS[bar]*1000<=now)  # drop the forming candle
def fetch(sym, bar):
    if SOURCE=="bybit": return fetch_bybit(sym,bar)
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

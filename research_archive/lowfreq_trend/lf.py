import json, os, math, random, statistics as st
D="d3"; SIDE=7e-4; FUND=0.5e-4/8*24  # 7bps/side (14bps RT); 0.5bp per 8h drag on any open position
def load(sym,bar="1D"):
    p=f"{D}/{sym}_{bar}.json"
    return [{"t":r[0],"o":r[1],"h":r[2],"l":r[3],"c":r[4],"v":r[5]} for r in json.load(open(p))] if os.path.exists(p) else []
def universe(bar="1D"):
    return {s:load(s,bar) for s in sorted({f.split("_")[0] for f in os.listdir(D) if f.endswith(f"_{bar}.json")}) if len(load(s,bar))>250}
def ema(x,p):
    k=2/(p+1); r=[x[0]]
    for v in x[1:]: r.append(v*k+r[-1]*(1-k))
    return r
def atrp(B,p=20):
    tr=[(B[0]["h"]-B[0]["l"])/B[0]["c"]]
    for i in range(1,len(B)): tr.append(max(B[i]["h"]-B[i]["l"],abs(B[i]["h"]-B[i-1]["c"]),abs(B[i]["l"]-B[i-1]["c"]))/B[i]["c"])
    return ema(tr,p)
def stats(rets,ann=365):
    """rets: list of period returns. returns dict"""
    if len(rets)<20: return None
    m=sum(rets)/len(rets); sd=st.pstdev(rets) or 1e-12
    eq=0;pk=0;mdd=0
    for r in rets:
        eq+=r; pk=max(pk,eq); mdd=min(mdd,eq-pk)
    # block bootstrap t of the mean (block=10)
    rnd=random.Random(3); n=len(rets); bs=[]
    for _ in range(400):
        s=[]
        while len(s)<n:
            j=rnd.randrange(n); s+=rets[j:j+10]
        bs.append(sum(s[:n])/n)
    bs.sort()
    return {"sharpe":m/sd*math.sqrt(ann),"ann":m*ann,"mdd":mdd,"lo":bs[10]*ann,"hi":bs[389]*ann,"n":n}
def fmt(s):
    return "n/a" if not s else f"Sh={s['sharpe']:+5.2f} ann={s['ann']*100:+6.1f}% mdd={s['mdd']*100:5.1f}% CI[{s['lo']*100:+.0f},{s['hi']*100:+.0f}]%"
def calendar(U): 
    return sorted({b["t"] for B in U.values() for b in B})
def split(cal):
    n=len(cal); return cal[int(n*.5)],cal[int(n*.75)]

import sys; sys.argv=["x"]
import lf
from lf import *
lf.FUND=0.5e-4/8*4
U=universe("4H"); cal=calendar(U); T1,T2=split(cal); N=len(U)
print("4H coins",N,"bars",len(cal))
def run(Np,long_only=True):
    per={}
    for s,B in U.items():
        c=[b["c"] for b in B]; e=ema(c,Np); a=atrp(B,120); prev=0
        for i in range(1,len(B)):
            p=1 if (i>Np and c[i-1]>e[i-1]) else (0 if long_only else -1)
            r=c[i]/c[i-1]-1; w=min(2.0,0.012/max(a[i-1],1e-4))
            g=p*r*w-abs(p-prev)*SIDE*w-(lf.FUND*w if p else 0); prev=p
            per.setdefault(B[i]["t"],[]).append(g)
    return {t:sum(v)/N for t,v in per.items()}
BH={}
for s,B in U.items():
    for i in range(1,len(B)): BH.setdefault(B[i]["t"],[]).append(B[i]["c"]/B[i-1]["c"]-1)
BH={t:sum(v)/N for t,v in BH.items()}
seg=lambda d,lo,hi:[d[t] for t in sorted(d) if lo<=t<hi]
A=2190
for nm,d in [(f"EMA{n}",run(n)) for n in (40,100,200,400)]+[("ENS(40,100,200,400)",None)]:
    if d is None:
        ds=[run(n) for n in (40,100,200,400)]; d={t:sum(x.get(t,0) for x in ds)/4 for t in ds[0]}
    print(f"{nm:20s}", " | ".join(f"{k}: {fmt(stats(seg(d,lo,hi),ann=A))}" for k,(lo,hi) in {"dev":(0,T1),"val":(T1,T2),"FINAL":(T2,1e18)}.items()))
print("BUY&HOLD EW        ", " | ".join(f"{k}: {fmt(stats(seg(BH,lo,hi),ann=A))}" for k,(lo,hi) in {"dev":(0,T1),"val":(T1,T2),"FINAL":(T2,1e18)}.items()))

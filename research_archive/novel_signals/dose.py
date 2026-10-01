import sys
folder,tf=sys.argv[1],sys.argv[2]; sys.argv=["x",folder,tf,"d"]
import events as E
from collections import defaultdict
import math
D=E.D; ema=E.ema
allt=E.allt; th=[allt[int(len(allt)*q)] for q in (1/3,2/3)]
rows=[]  # (t, z, vrel, cl, fwd dict sign=+1 raw long)
for s,B in D.items():
    n=len(B); c=[b["c"] for b in B]
    tr=[(B[0]["h"]-B[0]["l"])]+[max(B[i]["h"]-B[i]["l"],abs(B[i]["h"]-c[i-1]),abs(B[i]["l"]-c[i-1])) for i in range(1,n)]
    atr=ema(tr,20); vol=ema([b["v"] for b in B],30)
    for i in range(250,n-17):
        b=B[i]; a=atr[i-1]; z=(b["c"]-b["o"])/a; vr=b["v"]/max(vol[i-1],1e-9)
        cl=(b["c"]-b["l"])/max(b["h"]-b["l"],1e-12)
        rows.append((b["t"],z,vr,cl,{h:(B[i+h]["c"]/b["c"]-1) for h in (1,4,8,16)}))
def rep(lbl,sel,h=4):
    out=[]
    for k,(lo,hi) in enumerate(((0,th[0]),(th[0],th[1]),(th[1],9e18))):
        v=[(-1 if r[1]>0 else 1)*r[4][h] for r in rows if lo<=r[0]<hi and sel(r)]
        out.append(f"{sum(v)/len(v)*1e4:+6.1f}(n={len(v)})" if len(v)>=20 else "   n/a       ")
    print(f"  {lbl:34s} h={h:2d} fade gross bps by third [early | mid | late]: "+" | ".join(out))
print(f"== {tf}: fade-the-bar gross return (bps), vol>=1.5x avg, by |z| bucket ==")
for lo,hi in ((1,2),(2,3),(3,4),(4,6),(6,99)):
    for h in (4,8): rep(f"|z| {lo}-{hi}",lambda r,lo=lo,hi=hi:lo<=abs(r[1])<hi and r[2]>=1.5,h)
print("-- same, but shock bar closes near its extreme (continuation-looking, no rejection wick) vs. rejected --")
for h in (4,):
    rep("|z|>=3 close near extreme",lambda r:abs(r[1])>=3 and r[2]>=1.5 and ((r[3]>=0.8) if r[1]>0 else (r[3]<=0.2)),h)
    rep("|z|>=3 with rejection wick",lambda r:abs(r[1])>=3 and r[2]>=1.5 and ((r[3]<0.5) if r[1]>0 else (r[3]>0.5)),h)

import sys; sys.argv=["x"]
from e1 import *
START=10.0
def entries(Np,lo,hi):
    n=0
    for s,B in U.items():
        pos=positions(B,"ema",Np,True); prev=0
        for i,p in enumerate(pos):
            if lo<=B[i]["t"]<hi and p==1 and prev==0: n+=1
            prev=p
    return n
Ns=(20,50,100,200)
ds=[run("ema",N,True) for N in Ns]; E={t:sum(d.get(t,0) for d in ds)/len(ds) for t in ds[0]}
print("Daily EMA(20/50/100/200) long-only trend ensemble, 33 coins, vol-scaled, $10 start (compounded daily), 14bps + funding drag")
for nm,(lo,hi) in {"dev  (2022-05..2024-07)":(0,T1),"val  (2024-07..2025-08)":(T1,T2),"FINAL(2025-08..2026-09)":(T2,1e18),"ALL":(0,1e18)}.items():
    eq=START;pk=START;mdd=0
    for t in sorted(E):
        if lo<=t<hi: eq*=1+E[t]; pk=max(pk,eq); mdd=min(mdd,(eq-pk)/pk)
    bh=START
    for t in sorted(BH):
        if lo<=t<hi: bh*=1+BH[t]
    tr=sum(entries(N,lo,hi) for N in Ns)
    print(f"  {nm}: ${eq:6.2f}  trades {tr:5d}  maxDD {mdd*100:5.1f}%   | buy&hold equal-weight: ${bh:6.2f}")

import sys
sys.argv=["x"]
from e1 import *
def ens(kind,Ns):
    ds=[run(kind,N,True) for N in Ns]
    return {t:sum(d.get(t,0) for d in ds)/len(ds) for t in ds[0]}
E=ens("ema",(20,50,100,200)); DN=ens("don",(10,20,40,60,100))
def beta(d,lo,hi):
    ts=[t for t in sorted(d) if lo<=t<hi and t in BH]; x=[BH[t] for t in ts]; y=[d[t] for t in ts]
    mx=sum(x)/len(x); my=sum(y)/len(y); b=sum((a-mx)*(c-my) for a,c in zip(x,y))/sum((a-mx)**2 for a in x)
    return b, (my-b*mx)*365
print("PRE-DECLARED ensembles, long-only, vol-scaled, 14bps RT + funding drag")
for nm,d in (("EMA(20/50/100/200) ens",E),("Donchian(10..100) ens",DN)):
    for seg_,(lo,hi) in {"dev":(0,T1),"val":(T1,T2),"FINAL":(T2,1e18)}.items():
        b,a=beta(d,lo,hi); print(f"  {nm:24s} {seg_:5s} {fmt(stats(seg(d,lo,hi)))} beta={b:.2f} alpha={a*100:+.1f}%/yr")
for seg_,(lo,hi) in {"dev":(0,T1),"val":(T1,T2),"FINAL":(T2,1e18)}.items():
    print(f"  {'Buy&hold EW':24s} {seg_:5s} {fmt(stats(seg(BH,lo,hi)))}")

import pickle, json, math, sys, collections, statistics as st, io, contextlib
import mtf
from disc import tstat
with contextlib.redirect_stdout(io.StringIO()):
    from port import portfolio
FEE=sys.argv[1] if len(sys.argv)>1 else "14"
R=pickle.load(open(f"mtf_{FEE}.pkl","rb"))
A=set("BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR".split())
b=json.load(open("s15/BTC_15m.json")); t0,t1=b[0][0],b[-1][0]; T1=t0+(t1-t0)*.5; T2=t0+(t1-t0)*.75
def seg(tr,coins,lo,hi): return [x for x in tr if (x[1] in A)==(coins=="A") and lo<=x[0]<hi]
rows=[]
for (si,ti),tr in R.items():
    dv=seg(tr,"A",0,T1); va=seg(tr,"A",T1,T2)
    rows.append((si,ti,tstat([(x[0],x[2]) for x in dv]),tstat([(x[0],x[2]) for x in va]),tr))
print(f"fee {FEE} bps | configs {len(rows)}")
# family medians (dev, coin set A)
for nm,idx in (("bias",0),("poi",1),("confirm",2),("sweepL",3),("session",4)):
    g=collections.defaultdict(list)
    for si,ti,d,v,tr in rows:
        if d[0]>=30: g[mtf.SETS[si][idx]].append(d[1])
    print(f"  median dev avgR by {nm:8s}: "+"  ".join(f"{k}={st.median(x):+.3f}" for k,x in g.items()))
g=collections.defaultdict(list)
for si,ti,d,v,tr in rows:
    if d[0]>=30: g[str(mtf.EXITS[ti])].append(d[1])
print("  median dev avgR by exit (target,BE): "+"  ".join(f"{k}={st.median(x):+.3f}" for k,x in g.items()))
print("  share of configs with dev avgR>0:",f"{sum(1 for r in rows if r[2][0]>=30 and r[2][1]>0)}/{sum(1 for r in rows if r[2][0]>=30)}")
c1=[r for r in rows if r[2][0]>=40 and r[2][2]>=2.5 and r[2][1]>0]
print(f"\nDEV pass (n>=40, t>=2.5): {len(c1)}   (chance ~{len(rows)*0.006:.0f})")
c2=[r for r in c1 if r[3][0]>=20 and r[3][1]>0 and r[3][2]>=1.0]
print(f"   ...and positive on VAL (t>=1.0): {len(c2)}")
def eq(tr): 
    e,n,sk,w,dd,tot=portfolio([x for x in sorted(tr)],risk=0.02); return e,n,w,dd
for si,ti,d,v,tr in sorted(c2,key=lambda r:-(r[2][2]+r[3][2]))[:10]:
    fa=seg(tr,"A",T2,9e18); bb=seg(tr,"B",0,9e18); fs=tstat([(x[0],x[2]) for x in fa]); bs=tstat([(x[0],x[2]) for x in bb])
    e1=eq(fa); e2=eq(bb)
    print(f" {mtf.SETS[si]} exit={mtf.EXITS[ti]}")
    print(f"    dev n={d[0]} R={d[1]:+.2f} t={d[2]:+.1f} | val n={v[0]} R={v[1]:+.2f} t={v[2]:+.1f} | FINAL(A) n={fs[0]} R={fs[1]:+.2f} t={fs[2]:+.1f} ${e1[0]:.2f} | UNSEEN coins 365d n={bs[0]} R={bs[1]:+.2f} t={bs[2]:+.1f} $10->${e2[0]:.2f} ({e2[1]} taken, win {e2[2]/max(e2[1],1)*100:.0f}%, DD {e2[3]:.0f}%)")
pickle.dump([(r[0],r[1]) for r in c2],open(f"mtf_surv_{FEE}.pkl","wb"))

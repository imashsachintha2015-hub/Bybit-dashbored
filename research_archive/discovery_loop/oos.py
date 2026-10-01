import json, sys, math
import disc
from disc import *
init("d4","1H")
ORIG=set(f.split("_")[0] for f in os.listdir("data365") if f.endswith("_1H.json"))
t0=json.load(open("data365/BTC_1H.json"))[0][0]
BASE={'fam':'AMD','gate': 2, 'mr': 0.008, 'tp': 2.0, 'tmax': 96, 'N': 48, 'D': 0.8, 'v': 1.2, 'w': 12, 'W': 6, 'comp': 10, 's': 0.6, 'b': 0.1}
def eq(v,frac=0.02):
    e=10.0; pk=10; dd=0
    for t,R in sorted(v): e*=1+frac*R; pk=max(pk,e); dd=min(dd,e/pk-1)
    return e,dd*100
def rep(lbl,v):
    n,m,t=tstat(v)
    if n==0: print(f" {lbl:52s} n=0"); return
    e,dd=eq(v); wr=sum(1 for _,x in v if x>0)/n*100
    print(f" {lbl:52s} trades={n:4d} avgR={m:+.2f} t={t:+.1f} win={wr:3.0f}%  $10 -> ${e:7.2f} (maxDD {dd:5.1f}%)")
def cut(res):
    return {"orig12 / last 365d (used in search)":[(t,x) for t,s,x,sd in res if s in ORIG and t>=t0],
            "orig12 / OLDER 535d (never seen)":[(t,x) for t,s,x,sd in res if s in ORIG and t<t0],
            "NEW coins / last 365d (never seen)":[(t,x) for t,s,x,sd in res if s not in ORIG and t>=t0],
            "NEW coins / OLDER 535d (never seen)":[(t,x) for t,s,x,sd in res if s not in ORIG and t<t0]}
_,res=run_cfg(BASE)
print("BASE candidate, frozen parameters, 8bps limit cost, fill pen 0.05 ATR")
for k,v in cut(res).items(): rep(k,v)
unseen=[(t,x) for t,s,x,sd in res if (s not in ORIG) or t<t0]
print(); rep("ALL UNSEEN (new coins any time + orig coins older)",unseen)
print("\nUnseen data, half-year buckets (stability):")
import datetime
b=defaultdict(list)
for t,x in unseen: 
    d=datetime.datetime.utcfromtimestamp(t/1000); b[f"{d.year}-H{1 if d.month<=6 else 2}"].append((t,x))
for k in sorted(b): rep(k,b[k])
print("\nAll data, direction of drift gate (frozen): gate=1 (WITH drift) should lose if the effect is real")
P=dict(BASE); P["gate"]=1; _,r1=run_cfg(P); rep("gate=1 all unseen",[(t,x) for t,s,x,sd in r1 if (s not in ORIG) or t<t0])
P=dict(BASE); P["gate"]=0; _,r0=run_cfg(P); rep("gate=0 (no gate) all unseen",[(t,x) for t,s,x,sd in r0 if (s not in ORIG) or t<t0])
print("\nHarsher fills on unseen: pen 0.2 ATR / cost 16bps")
P=dict(BASE); P["pf"]=0.2; disc.A_FEE[0]=16e-4; _,rh=run_cfg(P); rep("pf=0.2, 16bps unseen",[(t,x) for t,s,x,sd in rh if (s not in ORIG) or t<t0])

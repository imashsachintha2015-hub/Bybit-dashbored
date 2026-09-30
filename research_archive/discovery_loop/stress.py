import json, sys, itertools, math
import disc
from disc import *
folder,tf="data365","1H"
init(folder,tf)
r=json.load(open(f"{folder}/BTC_{tf}.json")); a,b=r[0][0],r[-1][0]; T1,T2=a+(b-a)*.5,a+(b-a)*.75
BASE={'fam':'AMD','gate': 2, 'mr': 0.008, 'tp': 2.0, 'tmax': 96, 'N': 48, 'D': 0.8, 'v': 1.2, 'w': 12, 'W': 6, 'comp': 10, 's': 0.6, 'b': 0.1}
def ev(P,fee=8):
    disc.A_FEE[0]=fee*1e-4; _,res=run_cfg(P); return res
def seg(res):
    out={}
    for k,(lo,hi) in {"train":(0,T1),"val":(T1,T2),"FINAL":(T2,9e18)}.items():
        v=[(t,x) for t,s,x,sd in res if lo<=t<hi]; out[k]=tstat(v)
    return out
def fm(o): return " | ".join(f"{k} n={v[0]:3d} R={v[1]:+.2f} t={v[2]:+.1f}" for k,v in o.items())
print("== fee sensitivity (limit-order round-trip cost) ==")
for fee in (8,12,16,20,30): print(f" {fee:2d} bps: {fm(seg(ev(BASE,fee)))}")
res=ev(BASE)
print("\n== concentration (all trades) ==")
Rs=sorted([x for _,_,x,_ in res],reverse=True); n=len(Rs); print(f" n={n} mean={sum(Rs)/n:+.3f}; mean without top 3 trades: {sum(Rs[3:])/(n-3):+.3f}; top-5 share of total R: {sum(Rs[:5])/sum(Rs)*100:.0f}%")
by=defaultdict(list)
for t,s,x,sd in res: by[s].append(x)
print(" by coin:"," ".join(f"{s}:{sum(v)/len(v):+.2f}({len(v)})" for s,v in sorted(by.items())))
bs=defaultdict(list)
for t,s,x,sd in res: bs[sd].append(x)
print(" by side: ", " ".join(f"{k}:{sum(v)/len(v):+.2f}({len(v)})" for k,v in bs.items()), " (L = long after bearish drift; S = short after bullish drift)")
print("\n== neighbourhood: change one parameter at a time ==")
opts={"D":[0.6,0.8,1.0,1.2,1.8],"v":[0,1.2,2.0],"w":[6,12,24],"W":[3,6,12],"comp":[6,10,14,99],"s":[0.3,0.6,1.0],"b":[0.1,0.3,0.6],"tp":[1.5,2.0,3.0],"mr":[0.004,0.008,0.012],"N":[24,48,96],"tmax":[48,96],"gate":[0,1,2]}
for k,vals in opts.items():
    for v in vals:
        if v==BASE[k]: continue
        P=dict(BASE); P[k]=v; print(f" {k}={v!s:5s}: {fm(seg(ev(P)))}")

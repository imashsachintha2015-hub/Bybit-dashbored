import pickle, json, math, statistics as st, collections
from disc import tstat
r=json.load(open("s5a/BTC_5m.json")); a,b=r[0][0],r[-1][0]; T1,T2=a+(b-a)*.5,a+(b-a)*.75
pool=[]
for tag in ("sc4",):
    for P,res in pickle.load(open(f"disc_{tag}.pkl","rb")):
        seg=lambda lo,hi:[(t,x) for t,s,x,sd in res if lo<=t<hi]
        pool.append((tag,P,tstat(seg(0,T1)),tstat(seg(T1,T2)),tstat(seg(T2,9e18)),res))
print("configs:",len(pool))
sel=[x for x in pool if x[2][0]>=60 and x[3][0]>=30 and x[2][2]>=1.5 and x[3][2]>=1.5 and x[2][1]>0 and x[3][1]>0]
print(f"consistent (train R>0 t>=1.5 AND val R>0 t>=1.5): {len(sel)}  (chance expectation ~{len(pool)*0.067*0.067:.0f})")
fin=[x for x in sel if x[4][0]>=20]
pos=sum(1 for x in fin if x[4][1]>0)
print(f"of those with >=20 final trades: {len(fin)}; positive on FINAL: {pos} ({pos/max(len(fin),1)*100:.0f}%; null 50%); mean final avgR {st.mean([x[4][1] for x in fin]):+.3f}; median {st.median([x[4][1] for x in fin]):+.3f}")
fam=collections.Counter(x[1]["fam"] for x in sel); print(" by family:",dict(fam))
print("\nTop consistent by (train t + val t) with FINAL shown:")
for tag,P,a_,b_,f_,res in sorted(sel,key=lambda x:-(x[2][2]+x[3][2]))[:10]:
    d={k:P[k] for k in P if k!="fam"}
    print(f" {P['fam']:7s} train n={a_[0]:4d} R={a_[1]:+.2f} t={a_[2]:+.1f} | val n={b_[0]:4d} R={b_[1]:+.2f} t={b_[2]:+.1f} | FINAL n={f_[0]:4d} R={f_[1]:+.2f} t={f_[2]:+.1f} | {d}")
pickle.dump([(x[0],x[1]) for x in sel],open("sel_scalp4.pkl","wb"))

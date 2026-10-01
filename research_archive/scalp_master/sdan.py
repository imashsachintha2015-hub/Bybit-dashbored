import pickle, sys, json, math
from disc import tstat
tag,folder,tf=sys.argv[1],sys.argv[2],sys.argv[3]
r=json.load(open(f"{folder}/BTC_{tf}.json")); a,b=r[0][0],r[-1][0]; T1,T2=a+(b-a)*.5,a+(b-a)*.75
out=pickle.load(open(f"disc_{tag}.pkl","rb")); allc=[]
for P,res in out:
    tr=[(t,x) for t,s,x,sd in res if t<T1]; va=[(t,x) for t,s,x,sd in res if T1<=t<T2]; fi=[(t,x) for t,s,x,sd in res if t>=T2]
    allc.append((P,tstat(tr),tstat(va),tstat(fi),len(res)))
import collections
print("configs",len(allc)); 
fam=collections.defaultdict(list)
for P,a_,b_,f_,n in allc: fam[P["fam"]].append((a_[1],b_[1],n))
print("per family (all configs): median train avgR / val avgR / trades")
for k,v in fam.items():
    v2=[x for x in v if x[2]>50]; 
    if v2: 
        import statistics as st; print(f"   {k:7s} n_cfg={len(v2):4d} median train {st.median([x[0] for x in v2]):+.3f}  val {st.median([x[1] for x in v2]):+.3f}  best train {max(x[0] for x in v2):+.2f}")
c1=[x for x in allc if x[1][0]>=60 and x[1][2]>=3 and x[1][1]>=0.10]
print(f"\ntrain n>=60,t>=3,R>=0.10: {len(c1)} (chance ~{len(allc)*0.00135:.1f} before filters)")
c1.sort(key=lambda x:-x[1][2])
for P,a_,b_,f_,n in c1[:12]:
    d={k:P[k] for k in P if k!="fam"}
    print(f" {P['fam']:7s} train n={a_[0]:5d} R={a_[1]:+.2f} t={a_[2]:+.1f} | VAL n={b_[0]:5d} R={b_[1]:+.2f} t={b_[2]:+.1f} | {d}")
c2=[x for x in c1 if x[2][0]>=30 and x[2][1]>0 and x[2][2]>=1.3]
print(f"\npositive on VAL (n>=30,t>=1.3): {len(c2)}")
for P,a_,b_,f_,n in sorted(c2,key=lambda x:-x[2][2])[:12]:
    d={k:P[k] for k in P if k!="fam"}
    print(f" {P['fam']:7s} train n={a_[0]:5d} R={a_[1]:+.2f} t={a_[2]:+.1f} | VAL n={b_[0]:5d} R={b_[1]:+.2f} t={b_[2]:+.1f} | {d}")
pickle.dump(c2,open(f"surv_{tag}.pkl","wb"))
print("\nbest raw: train R>=0.3 (n>=60):",sum(1 for x in allc if x[1][0]>=60 and x[1][1]>=0.3)," val R>=0.3 (n>=30):",sum(1 for x in allc if x[2][0]>=30 and x[2][1]>=0.3))

import pickle, sys, math, json
from disc import tstat
tags={"1H":("r1_1h","data365"),"15m":("r1_15m","data"),"4H":("r1_4h","d3")}
def span(folder,tf):
    r=json.load(open(f"{folder}/BTC_{tf}.json")); a,b=r[0][0],r[-1][0]; return a+(b-a)*.5,a+(b-a)*.75
tot=0; allc=[]
for tf,(tag,folder) in tags.items():
    T1,T2=span(folder,tf); out=pickle.load(open(f"disc_{tag}.pkl","rb")); tot+=len(out)
    for P,res in out:
        tr=[(t,r) for t,s,r,sd in res if t<T1]; va=[(t,r) for t,s,r,sd in res if T1<=t<T2]; fi=[(t,r) for t,s,r,sd in res if t>=T2]
        a=tstat(tr); b=tstat(va)
        allc.append((tf,P,a,b,tstat(fi),len(res)))
print("configs tested:",tot)
def show(rows,k=12):
    for tf,P,a,b,f,n in rows[:k]:
        d={x:P[x] for x in P if x not in("fam",)}
        print(f" {tf:3s} {P['fam']:7s} train n={a[0]:5d} R={a[1]:+.2f} t={a[2]:+.1f} | VAL n={b[0]:5d} R={b[1]:+.2f} t={b[2]:+.1f} | {d}")
c1=[x for x in allc if x[2][0]>=40 and x[2][2]>=3.0 and x[2][1]>=0.15]
print("\nTrain n>=40, t>=3, R>=0.15:",len(c1),"(pure chance expects ~",round(tot*0.00135,1),"of one-sided t>=3 across configs, before the n/R filters)")
c1.sort(key=lambda x:-x[2][2]); show(c1,15)
c2=[x for x in c1 if x[3][0]>=25 and x[3][1]>0 and x[3][2]>=1.3]
print("\nOf those, positive on VAL (n>=25,t>=1.3):",len(c2)); show(sorted(c2,key=lambda x:-x[3][2]),15)
print("\nBest single train R>=0.5 with n>=40:",sum(1 for x in allc if x[2][0]>=40 and x[2][1]>=0.5)," and VAL R>=0.5 (n>=25):",sum(1 for x in allc if x[3][0]>=25 and x[3][1]>=0.5))
pickle.dump(c2,open("survivors_r1.pkl","wb"))

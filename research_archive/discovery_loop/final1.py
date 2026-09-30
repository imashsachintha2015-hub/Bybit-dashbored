import pickle, json
from disc import tstat
sv=pickle.load(open("survivors_r2.pkl","rb"))
out=pickle.load(open("disc_r2_1h.pkl","rb")); r=json.load(open("data365/BTC_1H.json")); a,b=r[0][0],r[-1][0]; T1,T2=a+(b-a)*.5,a+(b-a)*.75
def eqcurve(tr,frac=0.02):
    eq=10.0; pk=10; mdd=0
    for t,R in sorted(tr): eq*=1+frac*R; pk=max(pk,eq); mdd=min(mdd,eq/pk-1)
    return eq,mdd*100
for tf,P,a_,b_,f_,n in sv:
    res=[x for P2,x in out if P2==P][0]
    seg={"train":[(t,r_) for t,s,r_,sd in res if t<T1],"val":[(t,r_) for t,s,r_,sd in res if T1<=t<T2],"FINAL":[(t,r_) for t,s,r_,sd in res if t>=T2]}
    print({k:P[k] for k in ("gate","mr","tp","tmax","N","D","v","w","W","comp","s","b")})
    for k,v in seg.items():
        nn,m,t=tstat(v); e,dd=eqcurve(v); wr=sum(1 for _,x in v if x>0)/max(len(v),1)*100
        print(f"   {k:5s} trades={nn:4d} avgR={m:+.2f} t={t:+.1f} win={wr:3.0f}%  $10 -> ${e:6.2f} (2% risk, maxDD {dd:5.1f}%)")

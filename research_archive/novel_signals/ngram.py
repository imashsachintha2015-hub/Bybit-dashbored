import sys
folder,tf=sys.argv[1],sys.argv[2]; sys.argv=["x",folder,tf,"n"]
import events as E, math, random
from collections import defaultdict
D=E.D; T1,T2=E.T1,E.T2; ema=E.ema
K=int(sys.argv[1]) if False else 3
rec=[]  # (t, pattern, fwd4, fwd8)
for s,B in D.items():
    n=len(B); c=[b["c"] for b in B]
    tr=[(B[0]["h"]-B[0]["l"])]+[max(B[i]["h"]-B[i]["l"],abs(B[i]["h"]-c[i-1]),abs(B[i]["l"]-c[i-1])) for i in range(1,n)]
    atr=ema(tr,20); vol=ema([b["v"] for b in B],30)
    def sym(i):
        b=B[i]; z=(b["c"]-b["o"])/atr[i-1]; cl=(b["c"]-b["l"])/max(b["h"]-b["l"],1e-12); vr=b["v"]/max(vol[i-1],1e-9)
        return (0 if z<-0.6 else 1 if z<0 else 2 if z<0.6 else 3, 0 if cl<0.33 else 1 if cl<0.67 else 2, 0 if vr<0.9 else 1 if vr<1.4 else 2)
    S=[None]*n
    for i in range(250,n-9): S[i]=sym(i)
    for i in range(250+K,n-9): rec.append((B[i]["t"],tuple(S[i-K+1:i+1]),{h:B[i+h]["c"]/B[i]["c"]-1 for h in (4,8)}))
print(tf,"records",len(rec),"distinct patterns",len({r[1] for r in rec}))
def tst(v):
    d=defaultdict(list)
    for t,x in v: d[t//86400000].append(x)
    n=sum(len(a) for a in d.values()); m=sum(x for a in d.values() for x in a)/n
    se=math.sqrt(sum((sum(a)-m*len(a))**2 for a in d.values()))/n; return n,m,(m/se if se>0 else 0)
for h in (4,8):
    by=defaultdict(list)
    for t,p,f in rec:
        if t<T1: by[p].append((t,f[h]))
    cand=[]
    for p,v in by.items():
        if len(v)<60: continue
        n,m,t=tst(v); sg=1 if m>0 else -1
        cand.append((abs(t),p,sg,n,m))
    cand.sort(reverse=True); top=cand[:20]
    tv=defaultdict(list)
    for t,p,f in rec:
        if T1<=t<T2: tv[p].append((t,f[h]))
    print(f"h={h}: patterns with dev n>=60: {len(cand)}; best |t| dev {top[0][0]:.1f} (expected max by chance ~{math.sqrt(2*math.log(len(cand))):.1f})")
    ok=0; tot=[]
    for at,p,sg,n,m in top:
        v=tv.get(p,[]); 
        if len(v)<20: continue
        nn,mm,ttt=tst(v); net=sg*mm-E.COST; tot.append((sg*mm)); ok+= sg*mm>0
    print(f"   top-20 dev patterns: val gross sign-consistent {ok}/{len(tot)}; mean val gross bps {sum(tot)/len(tot)*1e4:+.1f} (cost 14)")

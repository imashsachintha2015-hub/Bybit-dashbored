import json, os, sys, math, random
from collections import defaultdict
folder,tf,tag=sys.argv[1],sys.argv[2],sys.argv[3]
COST=float(sys.argv[4]) if len(sys.argv)>4 else 14e-4
HS=(1,2,4,8,16)
def load(s):
    return [{"t":r[0],"o":r[1],"h":r[2],"l":r[3],"c":r[4],"v":r[5]} for r in json.load(open(f"{folder}/{s}_{tf}.json"))]
syms=sorted({f.split("_")[0] for f in os.listdir(folder) if f.endswith(f"_{tf}.json")})
D={s:load(s) for s in syms}; D={s:B for s,B in D.items() if len(B)>800}
btc={b["t"]:i for i,b in enumerate(D["BTC"])}
def ema(x,p):
    k=2/(p+1); r=[x[0]]
    for v in x[1:]: r.append(v*k+r[-1]*(1-k))
    return r
ev=defaultdict(list)   # name -> list of (t, sym, sign, {h:fwd})
allt=sorted({b["t"] for B in D.values() for b in B}); T1=allt[int(len(allt)*.5)]; T2=allt[int(len(allt)*.75)]
BR={b["t"]:(b["c"]/D["BTC"][i-1]["c"]-1) for i,b in enumerate(D["BTC"]) if i>0}
def emit(name,B,i,sign):
    f={}
    for h in HS:
        if i+h<len(B): f[h]=sign*(B[i+h]["c"]/B[i]["c"]-1)
    ev[name].append((B[i]["t"],B[0]["sym"],sign,f))
for s,B in D.items():
    for b in B: b["sym"]=s
    n=len(B); c=[b["c"] for b in B]
    tr=[(B[0]["h"]-B[0]["l"])]+[max(B[i]["h"]-B[i]["l"],abs(B[i]["h"]-c[i-1]),abs(B[i]["l"]-c[i-1])) for i in range(1,n)]
    atr=ema(tr,20); vol=ema([b["v"] for b in B],30)
    r1=[0]+[c[i]/c[i-1]-1 for i in range(1,n)]
    brr=[BR.get(b["t"],0) for b in B]
    # rolling beta to BTC (past 240 bars)
    for i in range(250,n-1):
        b=B[i]; a=atr[i-1] or 1e-9; z=(b["c"]-b["o"])/a; vrel=b["v"]/max(vol[i-1],1e-9); rr=(b["h"]-b["l"])/a
        cl=(b["c"]-b["l"])/max(b["h"]-b["l"],1e-12)
        if abs(z)>=3 and vrel>=2: emit("SHOCK fade |z|>=3 v>=2",B,i,-1 if z>0 else 1); emit("SHOCK continue |z|>=3 v>=2",B,i,1 if z>0 else -1)
        if abs(z)>=2 and vrel>=1.5 and abs(z)<3: emit("MID-shock fade 2<=|z|<3",B,i,-1 if z>0 else 1)
        if rr>=2 and vrel<=0.7: emit("THIN move fade (rng>=2ATR,v<=.7)",B,i,-1 if z>0 else 1)
        if vrel>=2.5 and rr<=0.6:
            d=1 if c[i]>c[i-4] else -1; emit("ABSORB then trend-dir",B,i,d); emit("ABSORB then counter-dir",B,i,-d)
        if s!="BTC":
            bz=brr[i]/((atr[i-1]/c[i-1]) or 1e-9)  # BTC bar in own-ATR units approx via alt atr (crude) -> use BTC series instead below
        # residual reversion over 4 bars
        if i>=260:
            xs=[r1[j] for j in range(i-240,i-4)]; ys=[brr[j] for j in range(i-240,i-4)]
            mx=sum(ys)/len(ys); my=sum(xs)/len(xs); vx=sum((y-mx)**2 for y in ys) or 1e-12
            beta=sum((y-mx)*(x-my) for x,y in zip(xs,ys))/vx
            res4=sum(r1[j]-beta*brr[j] for j in range(i-3,i+1)); sd=(atr[i-1]/c[i-1])*2 or 1e-9
            if s!="BTC" and abs(res4)/sd>=1.5: emit("RESID-4bar reversion |res|>=1.5*2ATR",B,i,-1 if res4>0 else 1); emit("RESID-4bar continuation",B,i,1 if res4>0 else -1)
        # run exhaustion: 5 same-direction closes with falling volume
        if all(c[i-k]>c[i-k-1] for k in range(5)) and B[i]["v"]<B[i-4]["v"]: emit("RUN-5up falling vol -> fade",B,i,-1)
        if all(c[i-k]<c[i-k-1] for k in range(5)) and B[i]["v"]<B[i-4]["v"]: emit("RUN-5dn falling vol -> fade",B,i,1)
# lead-lag: BTC big bar -> alts next bars
bt=D["BTC"]; ba=ema([max(bt[i]["h"]-bt[i]["l"],1e-9) for i in range(len(bt))],20)
for i in range(250,len(bt)-1):
    z=(bt[i]["c"]-bt[i]["o"])/ba[i-1]
    if abs(z)>=2:
        t=bt[i]["t"]
        for s,B in D.items():
            if s=="BTC": continue
            idx=next((j for j in range(len(B)) if B[j]["t"]==t),None) if False else None
ix={s:{b["t"]:j for j,b in enumerate(B)} for s,B in D.items()}
for i in range(250,len(bt)-1):
    z=(bt[i]["c"]-bt[i]["o"])/ba[i-1]
    if abs(z)>=2:
        for s,B in D.items():
            if s=="BTC": continue
            j=ix[s].get(bt[i]["t"])
            if j: emit("LEADLAG BTC |z|>=2 -> alt follows",B,j,1 if z>0 else -1); emit("LEADLAG BTC |z|>=2 -> alt fades",B,j,-1 if z>0 else 1)
# hour-of-day drift
hb=defaultdict(list)
for s,B in D.items():
    for i in range(250,len(B)-1):
        h=(B[i]["t"]//3600000)%24
        if tf=="1H": emit(f"HOUR {h:02d}UTC long",B,i,1); emit(f"HOUR {h:02d}UTC short",B,i,-1)
def tstat(rows,h,lo,hi):
    d=defaultdict(list)
    for t,s,sg,f in rows:
        if lo<=t<hi and h in f: d[t//86400000].append(f[h]-COST)
    n=sum(len(v) for v in d.values())
    if n<30: return None
    m=sum(x for v in d.values() for x in v)/n
    s=sum((sum(v)-m*len(v))**2 for v in d.values()); se=math.sqrt(s)/n
    return n,m*1e4,(m/se if se>0 else 0)
def gross(rows,h,lo,hi):
    v=[f[h] for t,s,sg,f in rows if lo<=t<hi and h in f]; return sum(v)/len(v)*1e4 if v else 0
if __name__=="__main__":
    print(f"tf={tf} cost={COST*1e4:.0f}bps coins={len(D)} | net mean bps (t) at best horizon on DEV -> same rule on VAL")
    res=[]
    for name,rows in ev.items():
        for h in HS:
            a=tstat(rows,h,0,T1); b=tstat(rows,h,T1,T2)
            if a and b: res.append((a[2],name,h,a,b,gross(rows,h,0,T1)))
    res.sort(reverse=True)
    print(f"tests run: {len(res)}")
    print(f"{'event':44s} h | DEV n  net_bps   t  gross | VAL n  net_bps   t")
    for t,name,h,a,b,g in res[:22]:
        print(f"{name:44s}{h:2d} | {a[0]:6d} {a[1]:+7.1f} {a[2]:+5.1f} {g:+6.1f} | {b[0]:6d} {b[1]:+7.1f} {b[2]:+5.1f}")
    pos=[r for r in res if r[3][2]>=2.5]
    print("dev t>=2.5:",len(pos)," of which val net>0:",sum(1 for r in pos if r[4][1]>0), " val t>=1.5:",sum(1 for r in pos if r[4][2]>=1.5))
    print("\nBest GROSS (before cost) dev bps per event family, horizon 4:")
    for name,rows in ev.items():
        if len(rows)>200 and not name.startswith("HOUR"): print(f"  {name:44s} gross dev {gross(rows,4,0,T1):+6.1f}  val {gross(rows,4,T1,T2):+6.1f}  n_dev={sum(1 for r in rows if r[0]<T1)}")

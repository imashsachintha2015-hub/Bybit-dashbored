import json, os, sys, math, random, pickle, time
from collections import defaultdict
from multiprocessing import Pool
FEE_MKT=14e-4; FEE_LMT=8e-4; A_FEE=[FEE_LMT]; EXIT={}; FEAT={}; BTCX={}
NS=(12,24,48,96)
def ema(x,p):
    k=2/(p+1); r=[x[0]]
    for v in x[1:]: r.append(v*k+r[-1]*(1-k))
    return r
def load(path,inv=False):
    d=json.load(open(path))
    if inv: return [[r[0],-r[1],-r[3],-r[2],-r[4],r[5]] for r in d]
    return d
def prep(rows,sym):
    n=len(rows); t=[r[0] for r in rows]; o=[r[1] for r in rows]; h=[r[2] for r in rows]; l=[r[3] for r in rows]; c=[r[4] for r in rows]; v=[r[5] for r in rows]
    tr=[h[0]-l[0]]+[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,n)]
    a=ema(tr,20); vm=ema(v,30)
    atr=[a[0]]+a[:-1]; vrel=[v[i]/max(vm[i-1],1e-12) if i>0 else 1 for i in range(n)]
    rmax={N:[None]*n for N in NS}; rmin={N:[None]*n for N in NS}
    for N in NS:
        for i in range(N,n): rmax[N][i]=max(h[i-N:i]); rmin[N][i]=min(l[i-N:i])
    return dict(sym=sym,n=n,t=t,o=o,h=h,l=l,c=c,v=v,atr=atr,vrel=vrel,rmax=rmax,rmin=rmin)
def sim(A,e,ep,stop,tp,tmax,fee):
    """long-orientation sim (arrays may be price-inverted). net R after fee, or None"""
    risk=ep-stop
    if risk<=0: return None
    rp=risk/abs(ep); h,l,c=A["h"],A["l"],A["c"]; n=A["n"]; key=(A["sym"],A["t"][e])
    for j in range(e,min(n,e+tmax)):
        if l[j]<=stop: EXIT[key]=(A["t"][j],rp); return -1-fee/rp
        if tp is not None and h[j]>=tp: EXIT[key]=(A["t"][j],rp); return (tp-ep)/risk-fee/rp
    j=min(n-1,e+tmax-1); EXIT[key]=(A["t"][j],rp); return (c[j]-ep)/risk-fee/rp
def sim_fill(A,e,ep,stop,tp,tmax,fee):
    """limit fill at bar e: the fill bar can only stop us out (its high may have printed before the fill)"""
    risk=ep-stop
    if risk<=0: return None
    rp=risk/abs(ep); h,l,c=A["h"],A["l"],A["c"]; n=A["n"]
    key=(A["sym"],A["t"][e])
    if l[e]<=stop: EXIT[key]=(A["t"][e],rp); return -1-fee/rp
    for j in range(e+1,min(n,e+tmax)):
        if l[j]<=stop: EXIT[key]=(A["t"][j],rp); return -1-fee/rp
        if tp is not None and h[j]>=tp: EXIT[key]=(A["t"][j],rp); return (tp-ep)/risk-fee/rp
    j=min(n-1,e+tmax-1); EXIT[key]=(A["t"][j],rp); return (c[j]-ep)/risk-fee/rp
def drift_ok(A,i,gate):
    if gate==0: return True
    if i<96: return False
    up=A["c"][i]>A["c"][i-96]
    return up if gate==1 else (not up)
def finish(A,i,P,ep,stop,rng_tp,out):
    """market entry at next bar open"""
    e=i+1
    if e>=A["n"]-1: return
    epx=A["o"][e]; risk=epx-stop
    if risk<=0 or risk/abs(epx)<P["mr"]: return
    if P["tp"]=="range":
        if rng_tp is None or rng_tp-epx<risk: return
        tp=rng_tp
    else: tp=epx+P["tp"]*risk
    r=sim(A,e,epx,stop,tp,P["tmax"],FEE_MKT)
    if r is not None: out.append((A["t"][e],A["sym"],r))
def scan(A,P):
    fam=P["fam"]; out=[]; n=A["n"]; N=P.get("N",24); last=-99
    h,l,c,o,atr,vr=A["h"],A["l"],A["c"],A["o"],A["atr"],A["vrel"]
    for i in range(110,n-2):  # scalp: 110-bar warmup
        if i-last<3: continue
        at=atr[i]
        if at<=0: continue
        if not drift_ok(A,i,P["gate"]): continue
        n0=len(out)
        if fam=="SPRING":
            hit=None
            for k in range(P["m"]):
                j=i-k; RL=A["rmin"][N][j]; RH=A["rmax"][N][j]
                if RL is None: continue
                if (RH-RL)/atr[j]>P["comp"]: continue
                if l[j]<RL-P["s"]*atr[j] and vr[j]>=P["v"] and c[i]>RL and c[i]>o[i]: hit=(j,RL,RH); break
            if not hit: continue
            j,RL,RH=hit; sl=min(l[j:i+1])-P["b"]*at
            finish(A,i,P,c[i],sl,RH,out)
        elif fam=="CLIMAX":
            hit=None
            for k in range(1,P["m"]+1):
                j=i-k
                if vr[j]>=P["v"] and (c[j]-o[j])<=-P["D"]*atr[j] and c[i]>(o[j]+c[j])/2 and c[i]>o[i]: hit=j; break
            if hit is None: continue
            j=hit; sl=min(l[j:i+1])-P["b"]*at
            finish(A,i,P,c[i],sl,max(h[j],o[j]),out)
        elif fam=="ABSORB":
            hit=None
            for k in range(1,P["m"]+1):
                j=i-k
                if vr[j]>=P["v"] and (h[j]-l[j])<=P["r"]*atr[j] and c[i]>h[j] and c[i]>o[i]: hit=j; break
            if hit is None: continue
            j=hit; sl=l[j]-P["b"]*at
            finish(A,i,P,c[i],sl,None,out)
        elif fam in ("IMBAL","AMD"):
            if not (l[i]>h[i-2] and (c[i-1]-o[i-1])>=P["D"]*atr[i-1] and vr[i-1]>=P["v"]): continue
            RH=None
            if fam=="IMBAL":
                RB=A["rmax"][N][i-1]
                if RB is None or c[i-1]<=RB: continue
                sl=l[i-2]-P["b"]*at
            else:
                hit=None
                for j in range(i-2-P["W"],i-1):
                    RL=A["rmin"][N][j]; RH0=A["rmax"][N][j]
                    if RL is None or (RH0-RL)/atr[j]>P["comp"]: continue
                    if l[j]<RL-P["s"]*atr[j] and c[i-1]>RL: hit=(j,RL,RH0); break
                if not hit: continue
                j,RL,RH=hit; sl=min(l[j:i])-P["b"]*at
            zb,zt=h[i-2],l[i]; ce=(zb+zt)/2
            filled=None
            for w in range(1,P["w"]+1):
                q=i+w
                if q>=n-1: break
                if c[q]<zb: break
                if l[q]<=ce-P.get("pf",0.05)*atr[i]: filled=q; break
            if filled is None: continue
            risk=ce-sl
            if risk<=0 or risk/abs(ce)<P["mr"]: continue
            if P["tp"]=="range":
                if RH is None or RH-ce<risk: continue
                tp=RH
            else: tp=ce+P["tp"]*risk
            r=sim_fill(A,filled,ce,sl,tp,P["tmax"],A_FEE[0])
            if r is not None:
                out.append((A["t"][filled],A["sym"],r))
                if fam=="AMD":
                    tf_=A["t"][filled]; bx=BTCX.get(tf_-tf_%3600000,(0,0)); sg=1 if A["side"]=="L" else -1
                    vv_=sum(A["v"][k]*abs(c[k]) for k in range(max(0,i-480),i))/max(1,min(480,i))
                    FEAT[(A["sym"],tf_)]=dict(side=A["side"],rp=risk/abs(ce)*100,disc=(c[i-1]-o[i-1])/atr[i-1],dvol=vr[i-1],sweep=(RL-l[j])/atr[j],
                        comp=(RH-RL)/atr[j],fvg=(zt-zb)/at,wait=filled-i,room=(RH-ce)/risk,drift=abs(c[i]/c[i-96]-1)*100,
                        btc=sg*bx[0],btcatr=bx[1],atrp=at/abs(c[i])*100,hour=(tf_//3600000)%24,gap=i-j,prevol=vr[filled-1],liq=vv_,dow=(tf_//86400000+3)%7)
        elif fam=="RETEST":
            RB=A["rmax"][N][i]
            if RB is None or c[i]<=RB or (c[i]-o[i])<P["D"]*at or vr[i]<P["v"]: continue
            ce=RB; sl=RB-P["sb"]*at; filled=None
            for w in range(1,P["w"]+1):
                q=i+w
                if q>=n-1: break
                if c[q]<sl: break
                if l[q]<=ce-P.get("pf",0.05)*atr[i]: filled=q; break
            if filled is None: continue
            risk=ce-sl
            if risk<=0 or risk/abs(ce)<P["mr"]: continue
            tp=ce+P["tp"]*risk if P["tp"]!="range" else ce+2*risk
            r=sim_fill(A,filled,ce,sl,tp,P["tmax"],A_FEE[0])
            if r is not None: out.append((A["t"][filled],A["sym"],r))
        elif fam=="VACUUM":
            RB=A["rmax"][N][i]
            if RB is None or c[i]<=RB or (c[i]-o[i])<P["D"]*at or vr[i]<P["v"]: continue
            lo_,hi_=c[i],c[i]+1.5*at; vv=0.0; tot=0.0
            for q in range(i-96,i):
                tp_=(h[q]+l[q]+c[q])/3; tot+=A["v"][q]
                if lo_<tp_<hi_: vv+=A["v"][q]
            if tot<=0 or vv/tot>P["lvn"]: continue
            sl=RB-P["b"]*at
            finish(A,i,P,c[i],sl,None,out)
        if len(out)>n0: last=i
    return out
def sample(rnd,fam):
    P=dict(fam=fam,gate=rnd.choice([0,0,1,2]),mr=rnd.choice([0.0015,0.0025,0.004]),tp=rnd.choice([1.0,1.5,2.0,3.0,"range"]),tmax=rnd.choice([12,24,48]),b=rnd.choice([0.1,0.3,0.6]),N=rnd.choice([12,24,48,96]))
    if fam=="RETEST": P.update(D=rnd.choice([0.8,1.2,1.8]),v=rnd.choice([1.0,1.5,2.5]),w=rnd.choice([3,6,12]),sb=rnd.choice([0.5,1.0,1.5]))
    if MODE[0]=="wide": P.update(mr=rnd.choice([0.004,0.006,0.008]),tmax=rnd.choice([24,48]),tp=rnd.choice([1.5,2.0,3.0,"range"]))
    if fam=="SPRING": P.update(m=rnd.choice([1,2,3]),comp=rnd.choice([5,8,12,99]),s=rnd.choice([0.1,0.3,0.6]),v=rnd.choice([0,1.2,2.0]))
    if fam=="CLIMAX": P.update(m=rnd.choice([1,2,3]),v=rnd.choice([1.5,2.5,3.5]),D=rnd.choice([1.0,1.5,2.5]))
    if fam=="ABSORB": P.update(m=rnd.choice([1,2,4]),v=rnd.choice([1.5,2.5,3.5]),r=rnd.choice([0.5,0.8,1.1]))
    if fam in ("IMBAL","AMD"): P.update(D=rnd.choice([0.8,1.2,1.8,2.5]),v=rnd.choice([0,1.2,2.0]),w=rnd.choice([3,6,12]),W=rnd.choice([3,6,12]),comp=rnd.choice([6,10,99]),s=rnd.choice([0.1,0.3,0.6]))
    if fam=="VACUUM": P.update(D=rnd.choice([0.8,1.2,1.8]),v=rnd.choice([1.0,1.5,2.5]),lvn=rnd.choice([0.02,0.05,0.10]))
    return P
G={}
MODE=["base"]
def init(folder,tf):
    for s in sorted({f.split("_")[0] for f in os.listdir(folder) if f.endswith(f"_{tf}.json")}):
        p=f"{folder}/{s}_{tf}.json"
        rows=load(p)
        if len(rows)<600: continue
        G[(s,"L")]=prep(rows,s); G[(s,"S")]=prep(load(p,True),s); G[(s,"L")]["side"]="L"; G[(s,"S")]["side"]="S"
    if ("BTC","L") in G:
        B=G[("BTC","L")]; n=B["n"]
        for i in range(96,n): BTCX[B["t"][i]]=((B["c"][i]/B["c"][i-96]-1)*100,B["atr"][i]/B["c"][i]*100)
def run_cfg(P):
    res=[]
    for (s,side),A in G.items(): res+=[(t,sy,r,side) for t,sy,r in scan(A,P)]
    return P,res
def tstat(v):
    d=defaultdict(list)
    for t,r in v: d[t//86400000].append(r)
    n=sum(len(a) for a in d.values())
    if n<2: return n,0,0
    m=sum(x for a in d.values() for x in a)/n
    se=math.sqrt(sum((sum(a)-m*len(a))**2 for a in d.values()))/n
    return n,m,(m/se if se>0 else 0)
if __name__=="__main__":
    folder,tf,K,seed,tag=sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),sys.argv[5]
    fams=sys.argv[6].split(",") if len(sys.argv)>6 else ["SPRING","CLIMAX","ABSORB","IMBAL","AMD","VACUUM"]
    if len(sys.argv)>7: A_FEE[0]=float(sys.argv[7])*1e-4
    if len(sys.argv)>8: MODE[0]=sys.argv[8]
    rnd=random.Random(seed); cfgs=[sample(rnd,f) for f in fams for _ in range(K)]
    t0=time.time()
    with Pool(4,initializer=init,initargs=(folder,tf)) as p: out=p.map(run_cfg,cfgs,chunksize=2)
    pickle.dump(out,open(f"disc_{tag}.pkl","wb")); print(tag,"configs",len(cfgs),"secs",int(time.time()-t0))

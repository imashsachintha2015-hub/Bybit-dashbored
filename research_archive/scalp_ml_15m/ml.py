import json, os, sys, math, random, time
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as SW
from sklearn.ensemble import HistGradientBoostingRegressor
FOLDER="s15"; BAR=900000
SET_A=os.environ.get("SETA","BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR").split()
SET_B=os.environ.get("SETB","APT SUI ARB OP ATOM FIL TRX BCH UNI AAVE INJ HBAR").split()
HORIZ=int(os.environ.get("HORIZ",16)); STOP_ATR=float(os.environ.get("STOPATR",1.2)); MIN_STOP=float(os.environ.get("MINSTOP",0.0035)); TPR=float(os.environ.get("TPR",1.5))
def load(c):
    p=f"{FOLDER}/{c}_15m.json"
    if not os.path.exists(p): return None
    d=np.array(json.load(open(p)),dtype=float)
    return d  # t,o,h,l,c,v
def ema(x,a):
    out=np.empty_like(x); out[0]=x[0]
    for i in range(1,len(x)): out[i]=out[i-1]+a*(x[i]-out[i-1])
    return out
def roll(x,k,fn):
    pad=np.full(k-1,np.nan); return np.concatenate([pad,fn(SW(x,k),axis=1)])
def feats(d,btc_feat,rs_ret16):
    t,o,h,l,c,v=[d[:,i] for i in range(6)]; n=len(t)
    prev=np.concatenate([[c[0]],c[:-1]])
    tr=np.maximum(h-l,np.maximum(np.abs(h-prev),np.abs(l-prev))); atr=ema(tr,1/14); atrp=atr/c
    rng=np.maximum(h-l,1e-12); cl=(c-l)/rng
    F={}
    for k in (1,2,4,8,16,48,96):
        r=np.concatenate([np.full(k,np.nan),(c[k:]-c[:-k])]); F[f"ret{k}"]=r/atr
    F["body"]=(c-o)/atr; F["range"]=(h-l)/atr; F["cl"]=cl
    F["upw"]=(h-np.maximum(o,c))/rng; F["dnw"]=(np.minimum(o,c)-l)/rng
    vm=ema(v,1/30); vprev=np.concatenate([[vm[0]],vm[:-1]]); F["vrel"]=v/np.maximum(vprev,1e-12)
    F["vrel4"]=roll(v,4,np.mean)/np.maximum(vprev,1e-12)
    flow=v*(2*cl-1)
    for k in (4,16): F[f"cvd{k}"]=roll(flow,k,np.sum)/np.maximum(roll(v,k,np.sum),1e-12)
    for k in (16,48):
        net=np.concatenate([np.full(k,np.nan),np.abs(c[k:]-c[:-k])]); path=roll(np.abs(c-prev),k,np.sum); F[f"er{k}"]=net/np.maximum(path,1e-12)
    F["atrp"]=atrp*100; F["vov"]=roll(atrp,48,np.std)/np.maximum(roll(atrp,48,np.mean),1e-12)
    tp=(h+l+c)/3; vw=roll(tp*v,96,np.sum)/np.maximum(roll(v,96,np.sum),1e-12); F["vwap96"]=(c-vw)/atr
    F["hi96"]=(roll(h,96,np.max)-c)/atr; F["lo96"]=(c-roll(l,96,np.min))/atr
    m24=np.concatenate([[np.nan],roll(l,24,np.min)[:-1]]); M24=np.concatenate([[np.nan],roll(h,24,np.max)[:-1]])
    F["sweepdn"]=np.maximum(m24-l,0)/atr*(c>m24); F["sweepup"]=np.maximum(h-M24,0)/atr*(c<M24)
    rr=np.concatenate([[0],(c[1:]/c[:-1]-1)]); F["z1"]=rr/np.maximum(roll(np.abs(rr),96,np.mean),1e-9)
    hr=((t//3600000)%24); F["hsin"]=np.sin(2*np.pi*hr/24); F["hcos"]=np.cos(2*np.pi*hr/24); F["dow"]=((t//86400000)+3)%7
    if os.environ.get("MATH","0")=="1":
        import mathfeat; F.update(mathfeat.compute(d,atr,cl,F["vrel"]))
    for k,arr in btc_feat.items(): F[k]=arr
    F["rs16"]=F["ret16"]*atrp-rs_ret16  # coin 16-bar return minus universe median (in price fraction terms)
    names=list(F); X=np.column_stack([F[k] for k in names]); X[~np.isfinite(X)]=np.nan; return names,X,atr
def labels(d,atr,side,fee):
    t,o,h,l,c,v=[d[:,i] for i in range(6)]; n=len(t); N=n-HORIZ-2
    idx=np.arange(N); entry=o[idx+1]; risk=np.maximum(STOP_ATR*atr[idx],MIN_STOP*entry)
    sg=1 if side=="L" else -1
    stop=entry-sg*risk; tp=entry+sg*TPR*risk
    alive=np.ones(N,bool); R=np.zeros(N); xk=np.full(N,HORIZ)
    for k in range(1,HORIZ+1):
        lo=l[idx+k]; hi=h[idx+k]
        sh=alive&((lo<=stop) if sg==1 else (hi>=stop)); th=alive&~sh&((hi>=tp) if sg==1 else (lo<=tp))
        R[sh]=-1; R[th]=TPR; xk[sh|th]=k; alive&=~(sh|th)
    ce=c[idx+HORIZ]; R[alive]=(sg*(ce-entry)/risk)[alive]
    rp=risk/entry; net=R-fee/rp
    full=np.full(n,np.nan); full[:N]=net; xt=np.full(n,np.nan); xt[:N]=t[idx+xk]; rpa=np.full(n,np.nan); rpa[:N]=rp
    return full,xt,rpa
def build(coins,fee):
    D={c:load(c) for c in coins}; D={c:d for c,d in D.items() if d is not None and len(d)>5000}
    btc=load("BTC"); bt={int(x):i for i,x in enumerate(btc[:,0])}
    # BTC features aligned by timestamp
    bc=btc[:,4]; batr=ema(np.maximum(btc[:,2]-btc[:,3],1e-9),1/14)
    bf={}
    for k in (1,4,16):
        r=np.concatenate([np.full(k,np.nan),bc[k:]-bc[:-k]]); bf[f"btc{k}"]=r/batr
    bf["btcatr"]=batr/bc*100
    # universe median 16-bar return (price fraction) by timestamp
    allrs={}
    for c,d in D.items():
        cc=d[:,4]; r=np.concatenate([np.full(16,np.nan),cc[16:]/cc[:-16]-1])
        for tt,x in zip(d[:,0].astype(np.int64),r): allrs.setdefault(tt,[]).append(x)
    med={tt:np.nanmedian(v) for tt,v in allrs.items()}
    out={}
    for c,d in D.items():
        ts=d[:,0].astype(np.int64); ix=np.array([bt.get(int(x),-1) for x in ts])
        btc_feat={k:np.where(ix>=0,v[np.maximum(ix,0)],np.nan) for k,v in bf.items()}
        rs=np.array([med.get(int(x),np.nan) for x in ts])
        names,X,atr=feats(d,btc_feat,rs)
        lL,xL,rL=labels(d,atr,"L",fee); lS,xS,rS=labels(d,atr,"S",fee)
        cc=d[:,4]; fr=np.full(len(cc),np.nan); fr[:-16]=(cc[16:]-cc[:-16])/atr[:-16]
        out[c]=dict(names=names,X=X,t=d[:,0],yL=lL,xL=xL,rL=rL,yS=lS,xS=xS,rS=rS,fr=fr)
    return out

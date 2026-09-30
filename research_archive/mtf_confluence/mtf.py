"""Multi-timeframe 'wait for confluence' system:
1H bias (BOS state, premium/discount) -> 15m liquidity sweep into a 1H POI (FVG / POC)
-> 15m confirmation candle (reclaim / engulfing / CHoCH) -> high-R target, optional session filter.
Long logic only; shorts use price inversion. All information strictly causal."""
import json, os, sys, pickle, itertools
import numpy as np
from multiprocessing import Pool
FOLDER="s15"; BAR=900000; HR=3600000
FEE=float(os.environ.get("FEE",14))*1e-4
BIAS=("none","bos","bos_disc"); POI=("none","fvg","poc","any"); CONF=("reclaim","engulf","choch")
SWL=(16,48); SESS=("all","kz"); TGT=(2,3,4,5,"liq"); BE=(0,1)
SETS=list(itertools.product(BIAS,POI,CONF,SWL,SESS)); EXITS=list(itertools.product(TGT,BE))
M_CONF=6; HOLD=96; MINSTOP=0.004; BUF=0.1; KZ={7,8,9,12,13,14,15}
def load(sym,inv):
    d=np.array(json.load(open(f"{FOLDER}/{sym}_15m.json")),dtype=float)
    t,o,h,l,c,v=[d[:,k].copy() for k in range(6)]
    if inv: o,h,l,c=-o,-l,-h,-c
    return t,o,h,l,c,v
def ema(x,a):
    out=np.empty_like(x); out[0]=x[0]
    for i in range(1,len(x)): out[i]=out[i-1]+a*(x[i]-out[i-1])
    return out
def series(sym,inv):
    t,o,h,l,c,v=load(sym,inv); n=len(t)
    prev=np.concatenate([[c[0]],c[:-1]]); tr=np.maximum(h-l,np.maximum(np.abs(h-prev),np.abs(l-prev)))
    atr=np.concatenate([[tr[0]],ema(tr,1/14)[:-1]])
    # ---- 1H bars from 15m (complete hours only) ----
    hid=(t//HR).astype(np.int64); uh,start,cnt=np.unique(hid,return_index=True,return_counts=True)
    keep=cnt==4; uh=uh[keep]; st=start[keep]
    H1=np.array([h[s:s+4].max() for s in st]); L1=np.array([l[s:s+4].min() for s in st]); C1=c[st+3]; V1=np.array([v[s:s+4].sum() for s in st]); T1=(h[st]*0+0)+uh
    TP1=(H1+L1+C1)/3; m1=len(uh)
    hour_end=(uh+1)*HR; idx1=np.searchsorted(hour_end,t+BAR,side="right")-1   # last complete hour usable at close of 15m bar i
    # 1H pivots (k=2), confirmed 2 hours later; bias state machine; dealing range
    ph_conf=[]; pl_conf=[]  # (confirm_hour, price)
    bias=np.zeros(m1,bool); eq=np.full(m1,np.nan); lastph=None; lastpl=None; state=0; rlow=np.nan; rhigh=np.nan
    fvg_list=[]; active_fvg=[[] for _ in range(m1)]; act=[]
    ph_price_by_hour=np.full(m1,np.nan)
    poc1=np.full(m1,np.nan)
    for m in range(m1):
        if m>=4:
            k=m-2
            if H1[k]>max(H1[k-2],H1[k-1],H1[k+1],H1[k+2]): lastph=H1[k]; ph_conf.append((m,H1[k]))
            if L1[k]<min(L1[k-2],L1[k-1],L1[k+1],L1[k+2]): lastpl=L1[k]
        if lastph is not None and C1[m]>lastph and state!=1:
            state=1; rlow=lastpl if lastpl is not None else L1[max(0,m-24):m+1].min(); rhigh=H1[m]
        elif lastpl is not None and C1[m]<lastpl and state!=-1: state=-1
        if state==1: rhigh=max(rhigh,H1[m])
        bias[m]=state==1; eq[m]=(rlow+rhigh)/2 if state==1 else np.nan
        # 1H FVGs (bullish): L[m]>H[m-2]; mitigated by a 1H close below bottom
        if m>=2 and L1[m]>H1[m-2]: act.append((m,H1[m-2],L1[m]))
        act=[f for f in act if C1[m]>=f[1] and m-f[0]<=72]
        active_fvg[m]=list(act)
        if m>=48:
            lo=L1[m-47:m+1].min(); hi=H1[m-47:m+1].max()
            if hi>lo:
                hist,_=np.histogram(TP1[m-47:m+1],bins=24,range=(lo,hi),weights=V1[m-47:m+1]); poc1[m]=lo+(np.argmax(hist)+0.5)*(hi-lo)/24
    ph_hours=np.array([x[0] for x in ph_conf]) if ph_conf else np.array([],int); ph_prices=np.array([x[1] for x in ph_conf]) if ph_conf else np.array([])
    # 15m pivot highs for CHoCH (k=2, confirmed 2 bars later)
    piv=np.zeros(n,bool); piv[2:-2]=(h[2:-2]>h[1:-3])&(h[2:-2]>h[:-4])&(h[2:-2]>h[3:-1])&(h[2:-2]>h[4:])
    last_piv=np.full(n,np.nan); lp=np.nan
    for i in range(n):
        if i>=2 and piv[i-2]: lp=h[i-2]
        last_piv[i]=lp
    hour=((t//HR)%24).astype(int)
    return dict(sym=sym,inv=inv,t=t,o=o,h=h,l=l,c=c,atr=atr,idx1=idx1,bias=bias,eq=eq,active_fvg=active_fvg,poc1=poc1,
                ph_hours=ph_hours,ph_prices=ph_prices,last_piv=last_piv,hour=hour,n=n)
def sim(S,e,ep,stop,tp,be):
    h,l,c,t=S["h"],S["l"],S["c"],S["t"]; n=S["n"]; risk=ep-stop; rp=risk/abs(ep); fr=FEE/rp; cur=stop; moved=False
    for j in range(e,min(n,e+HOLD)):
        if l[j]<=cur: return ((cur-ep)/risk-fr, t[j], rp)
        if h[j]>=tp: return ((tp-ep)/risk-fr, t[j], rp)
        if be and not moved and h[j]>=ep+risk: cur=ep+FEE*abs(ep); moved=True
    j=min(n-1,e+HOLD-1); return ((c[j]-ep)/risk-fr, t[j], rp)
def run_series(args):
    sym,inv=args; S=series(sym,inv); n=S["n"]; h,l,c,o,atr=S["h"],S["l"],S["c"],S["o"],S["atr"]; idx1=S["idx1"]
    out={}
    for L in SWL:
        rmin=np.full(n,np.nan)
        for i in range(L,n): rmin[i]=l[i-L:i].min()
        sweeps=[j for j in range(max(L,200),n-2) if l[j]<rmin[j]]
        for si,(bias,poi,conf,L_,sess) in enumerate(SETS):
            if L_!=L: continue
            last=-99
            for j in sweeps:
                if j<=last: continue
                m=idx1[j]
                if m<50: continue
                if bias!="none" and not S["bias"][m]: continue
                if poi!="none":
                    okf=any(l[j]<=f[2] and h[j]>=f[1] for f in S["active_fvg"][m]) if poi in ("fvg","any") else False
                    pc=S["poc1"][m]; okp=(not np.isnan(pc)) and (l[j]<=pc+0.25*atr[j]) and (h[j]>=pc-0.25*atr[j]) if poi in ("poc","any") else False
                    if not (okf or okp): continue
                swept=rmin[j]
                for i in range(j,min(n-2,j+M_CONF+1)):
                    if l[i]<l[j] and i>j: break          # new low: setup invalid, wait for next sweep
                    if conf=="reclaim": ok=c[i]>swept and c[i]>o[i] and (c[i]-l[i])>=0.6*(h[i]-l[i]+1e-12)
                    elif conf=="engulf": ok=i>0 and c[i-1]<o[i-1] and c[i]>o[i-1] and o[i]<=c[i-1]+1e-12 and c[i]>swept
                    else:
                        pvt=S["last_piv"][j]; ok=(not np.isnan(pvt)) and c[i]>pvt
                    if not ok: continue
                    mi=idx1[i]
                    if bias=="bos_disc" and not (S["bias"][mi] and c[i]<S["eq"][mi]): break
                    if sess=="kz" and S["hour"][i] not in KZ: break
                    e=i+1; ep=o[e]; stop=min(l[j:i+1])-BUF*atr[i]
                    if ep-stop<MINSTOP*abs(ep): stop=ep-MINSTOP*abs(ep)
                    risk=ep-stop
                    if risk<=0: break
                    # liquidity target: nearest confirmed 1H pivot high above entry
                    ph=S["ph_prices"][S["ph_hours"]<=mi]; above=ph[ph>ep]; liq=above[-1] if len(above) else None
                    for ti,(tg,be) in enumerate(EXITS):
                        if tg=="liq":
                            if liq is None or (liq-ep)<2*risk: continue
                            tp=liq
                        else: tp=ep+tg*risk
                        R,xt,rp=sim(S,e,ep,stop,tp,be)
                        out.setdefault((si,ti),[]).append((S["t"][e],sym,R,"S" if inv else "L",xt,rp))
                    last=i+4; break
    return out
if __name__=="__main__":
    coins=sorted(f.split("_")[0] for f in os.listdir(FOLDER) if f.endswith("_15m.json"))
    jobs=[(s,inv) for s in coins for inv in (False,True)]
    with Pool(4) as p: parts=p.map(run_series,jobs)
    allr={}
    for part in parts:
        for k,v in part.items(): allr.setdefault(k,[]).extend(v)
    pickle.dump(allr,open(f"mtf_{int(FEE*1e4)}.pkl","wb")); print("configs with trades:",len(allr),"of",len(SETS)//2*len(EXITS) if False else len(SETS)*len(EXITS))

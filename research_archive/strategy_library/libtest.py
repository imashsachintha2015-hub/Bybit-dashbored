import json, os, sys, pickle, time, warnings
import numpy as np
from multiprocessing import Pool
import lib
warnings.filterwarnings("ignore")
FOLDER="s15"; FEE=float(os.environ.get("FEE",14))*1e-4
EXITS={"E1 1.5ATR/2R":(1.5,2.0),"E2 1ATR/3R":(1.0,3.0)}; HOLD=48; MINSTOP=0.003
def load(sym,tf):
    d=np.array(json.load(open(f"{FOLDER}/{sym}_15m.json")),dtype=float)
    if tf=="15m": return [d[:,k] for k in range(6)]
    hid=(d[:,0]//3600000).astype(np.int64); uh,st,cnt=np.unique(hid,return_index=True,return_counts=True); st=st[cnt==4]
    t=d[st,0]; o=d[st,1]; h=np.array([d[s:s+4,2].max() for s in st]); l=np.array([d[s:s+4,3].min() for s in st]); c=d[st+3,4]; v=np.array([d[s:s+4,5].sum() for s in st])
    return [t,o,h,l,c,v]
def sim(I,sig,side,k,R_):
    t,o,h,l,c,atr=I["t"],I["o"],I["h"],I["l"],I["c"],I["atr"]; n=len(c); out=[]; busy=-1
    for i in np.where(sig)[0]:
        if i<220 or i+2>=n or i<=busy: continue
        e=i+1; ep=o[e]; risk=max(k*atr[i],MINSTOP*ep)
        if not np.isfinite(risk) or risk<=0: continue
        sg=1 if side=="L" else -1; stop=ep-sg*risk; tp=ep+sg*R_*risk; rp=risk/ep; fr=FEE/rp; res=None
        for j in range(e,min(n,e+HOLD)):
            if (l[j]<=stop) if sg==1 else (h[j]>=stop): res=(-1-fr,j); break
            if (h[j]>=tp) if sg==1 else (l[j]<=tp): res=(R_-fr,j); break
        if res is None: j=min(n-1,e+HOLD-1); res=(sg*(c[j]-ep)/risk-fr,j)
        out.append((t[e],I["sym"],res[0],side,t[res[1]],rp)); busy=res[1]
    return out
def job(a):
    sym,tf=a; t,o,h,l,c,v=load(sym,tf); I=lib.indicators(t,o,h,l,c,v); I["sym"]=sym; S=lib.strategies(I); out={}
    for nm,(L,Sg) in S.items():
        L=np.nan_to_num(L.astype(float))>0; Sg=np.nan_to_num(Sg.astype(float))>0
        for en,(k,R_) in EXITS.items():
            out[(tf,nm,en)]=sim(I,L,"L",k,R_)+sim(I,Sg,"S",k,R_)
    return out
if __name__=="__main__":
    coins=sorted(f.split("_")[0] for f in os.listdir(FOLDER) if f.endswith("_15m.json"))
    t0=time.time()
    with Pool(4) as p: parts=p.map(job,[(s,tf) for tf in ("15m","1H") for s in coins])
    R={}
    for pt in parts:
        for k,v in pt.items(): R.setdefault(k,[]).extend(v)
    pickle.dump(R,open(f"lib_{int(FEE*1e4)}.pkl","wb")); print("done",int(time.time()-t0),"s; tests",len(R))

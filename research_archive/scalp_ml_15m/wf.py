import sys, time, random, math, pickle
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
import ml
FEE=float(sys.argv[1])*1e-4 if len(sys.argv)>1 else 12e-4
DAILY_CAP=int(sys.argv[2]) if len(sys.argv)>2 else 10
SIG_PER_DAY=float(sys.argv[3]) if len(sys.argv)>3 else 25.0
TAG=sys.argv[4] if len(sys.argv)>4 else "run"
DAY=86400000
def stack(D,coins):
    Xs,ts,yL,yS,xL,xS,rL,rS,cid=[],[],[],[],[],[],[],[],[]
    for k,c in enumerate(coins):
        if c not in D: continue
        d=D[c]; n=len(d["t"]); Xs.append(d["X"]); ts.append(d["t"]); yL.append(d["yL"]); yS.append(d["yS"]); xL.append(d["xL"]); xS.append(d["xS"]); rL.append(d["rL"]); rS.append(d["rS"]); cid.append(np.full(n,k))
    X=np.vstack(Xs); t=np.concatenate(ts)
    cat=lambda a:np.concatenate(a)
    S=dict(X=X,t=t,yL=cat(yL),yS=cat(yS),xL=cat(xL),xS=cat(xS),rL=cat(rL),rS=cat(rS),cid=cat(cid),coins=[c for c in coins if c in D])
    S["ok"]=~np.isnan(X).any(axis=1)
    return S
def portfolio(trades,risk=0.02,maxpos=3,lev=10,start=10.0,minnot=5.0):
    trades=sorted(trades); openp=[]; eq=start; pk=start; dd=0; done=0; skipped=0; wins=0; tot=0.0
    def close_upto(t):
        nonlocal eq,pk,dd,wins
        for p in sorted([p for p in openp if p[0]<=t]):
            openp.remove(p); eq+=p[1]; wins+=p[1]>0; pk=max(pk,eq); dd=min(dd,eq/pk-1)
    for et,sym,R,sd,xt,rp in trades:
        close_upto(et)
        if eq<1.0: break
        if len(openp)>=maxpos or any(p[2]==sym for p in openp): skipped+=1; continue
        used=sum(p[3] for p in openp); notional=min(eq*risk/rp,eq*lev-used)
        if notional<minnot: skipped+=1; continue
        openp.append((xt,notional*rp*R,sym,notional)); done+=1; tot+=R
    close_upto(1e18)
    return eq,done,skipped,wins,dd*100,tot
def model():
    return HistGradientBoostingRegressor(max_depth=3,learning_rate=0.05,max_iter=150,min_samples_leaf=300,l2_regularization=10.0,random_state=0)
def main():
    t0=time.time()
    D=ml.build(ml.SET_A+ml.SET_B,FEE); print("features built",int(time.time()-t0),"s; coins",len(D),"features",len(next(iter(D.values()))["names"]))
    A=stack(D,ml.SET_A); B=stack(D,ml.SET_B)
    tmin=A["t"].min(); tmax=A["t"].max(); days=(tmax-tmin)/DAY; print(f"span {days:.0f} days")
    INIT=150*DAY; BLK=30*DAY; PURGE=ml.HORIZ*ml.BAR
    starts=[tmin+INIT+k*BLK for k in range(int((tmax-tmin-INIT)//BLK)+1)]
    preds={"A":{},"B":{}}  # side-> arrays of preds filled per block
    PA={s:np.full(len(A["t"]),np.nan) for s in "LS"}; PB={s:np.full(len(B["t"]),np.nan) for s in "LS"}
    blocks=[]
    for bs in starts:
        be=bs+BLK; tr=A["ok"]&(A["t"]<bs-PURGE)
        for s in "LS":
            y=A["y"+s]; m=tr&~np.isnan(y)
            if m.sum()<20000: break
            mdl=model().fit(A["X"][m],y[m])
            ta=A["ok"]&(A["t"]>=bs)&(A["t"]<be); PA[s][ta]=mdl.predict(A["X"][ta])
            tb=B["ok"]&(B["t"]>=bs)&(B["t"]<be); PB[s][tb]=mdl.predict(B["X"][tb])
        else: blocks.append((bs,be))
    print("walk-forward blocks",len(blocks),"secs",int(time.time()-t0))
    # calibrate threshold on first block ONLY by frequency (no performance peeking)
    bs0,be0=blocks[0]; m0=(A["t"]>=bs0)&(A["t"]<be0)&A["ok"]
    allp=np.concatenate([PA["L"][m0],PA["S"][m0]]); allp=allp[~np.isnan(allp)]
    ndays=(be0-bs0)/DAY; k=int(SIG_PER_DAY*ndays); tau=np.sort(allp)[-k]
    print(f"calibration block: tau={tau:+.3f}R chosen for ~{SIG_PER_DAY:.0f} raw signals/day on set A (frequency only)")
    ev_start=blocks[1][0]; ev_end=blocks[-1][1]
    def trades_for(S,P,select,seed=0):
        rnd=random.Random(seed); cand=[]
        for s in "LS":
            m=S["ok"]&(S["t"]>=ev_start)&(S["t"]<ev_end)&~np.isnan(P[s])&~np.isnan(S["y"+s])
            idx=np.where(m)[0]
            for i in idx:
                cand.append((S["t"][i]+ml.BAR,S["coins"][int(S["cid"][i])],float(S["y"+s][i]),s,float(S["x"+s][i]),float(S["r"+s][i]),float(P[s][i])))
        cand.sort(key=lambda x:(x[0],-x[6]))
        return cand
    def choose(cand,mode,seed=0):
        rnd=random.Random(seed); out=[]; cnt={}
        pool=[c for c in cand if c[6]>tau] if mode=="model" else cand
        if mode=="random":
            n=len([c for c in cand if c[6]>tau]); pool=cand
            # random control: same number of candidates overall, spread uniformly
            pool=rnd.sample(cand,min(n,len(cand))); pool.sort(key=lambda x:(x[0],-x[6]))
        for c in pool:
            d=int(c[0]//DAY)
            if cnt.get(d,0)>=DAILY_CAP: continue
            cnt[d]=cnt.get(d,0)+1; out.append((c[0],c[1],c[2],c[3],c[4],c[5]))
        return out
    def rep(lbl,S,P):
        cand=trades_for(S,P,None); evd=(ev_end-ev_start)/DAY
        base=np.mean([c[2] for c in cand]); print(f"\n[{lbl}] evaluation span {evd:.0f} days; {len(cand)} candidate entries; unconditional avg net R {base:+.3f}")
        for mode,seeds in (("model",[0]),("random",[1,2,3,4,5])):
            res=[]
            for sd in seeds:
                tr=choose(cand,mode,sd); e,n,sk,w,dd,tot=portfolio(tr); res.append((e,n,sk,w,dd,tot,len(tr),np.mean([x[2] for x in tr]) if tr else 0))
            e,n,sk,w,dd,tot,ns,avg=[np.mean([r[i] for r in res]) for i in range(8)]
            print(f"   {mode:6s}: signals/day {ns/evd:4.1f} | taken {n:5.0f} ({n/evd:4.1f}/day) win {w/max(n,1)*100:3.0f}% | avgR(signals) {avg:+.3f} | totalR(taken) {tot:+7.1f} | $10 -> ${e:9.2f} maxDD {dd:5.1f}%"+("  (mean of 5 seeds)" if mode=="random" else ""))
        return cand
    def deciles(lbl,S,P):
        for sd in "LS":
            m=S["ok"]&(S["t"]>=ev_start)&(S["t"]<ev_end)&~np.isnan(P[sd])&~np.isnan(S["y"+sd])
            p=P[sd][m]; y=S["y"+sd][m]; q=np.quantile(p,np.linspace(0,1,11)); ic=np.corrcoef(np.argsort(np.argsort(p)),np.argsort(np.argsort(y)))[0,1]
            dm=[y[(p>=q[k])&(p<=q[k+1])].mean() for k in range(10)]
            print(f"   {lbl} {sd}: rank-IC {ic:+.4f} | realized net R by predicted decile (low->high): "+" ".join(f"{x:+.2f}" for x in dm))
    print("\nPrediction quality (out-of-sample):"); deciles("A",A,PA); deciles("B",B,PB)
    cA=rep("SET A: search coins, walk-forward out-of-sample",A,PA)
    cB=rep("SET B: UNSEEN coins (model trained on A only)",B,PB)
    pickle.dump(dict(tau=tau,ev=(ev_start,ev_end)),open(f"wf_{TAG}.pkl","wb"))
    # per-block stability (model, set A)
    print("\nPer 30-day block, SET A model signals (avg net R, count):")
    for bs,be in blocks[1:]:
        tr=[c for c in choose(cA,"model") if bs<=c[0]<be]
        print(f"   days {int((bs-tmin)/DAY):3d}-{int((be-tmin)/DAY):3d}: n={len(tr):4d} avgR {np.mean([x[2] for x in tr]) if tr else 0:+.3f}")
if __name__=="__main__": main()

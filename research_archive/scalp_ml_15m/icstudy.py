import os, sys, time
os.environ["MATH"]="1"
import numpy as np, ml
from wf import stack
t0=time.time(); D=ml.build(ml.SET_A+ml.SET_B,12e-4); names=next(iter(D.values()))["names"]; print("built",int(time.time()-t0),"s; features",len(names))
def stackfr(coins):
    S=stack(D,coins); S["fr"]=np.concatenate([D[c]["fr"] for c in S["coins"]]); return S
A=stackfr(ml.SET_A); B=stackfr(ml.SET_B)
DAY=86400000; tmin=A["t"].min()
def rankic(x,y):
    m=~np.isnan(x)&~np.isnan(y)
    if m.sum()<500: return np.nan
    rx=np.argsort(np.argsort(x[m])); ry=np.argsort(np.argsort(y[m])); return np.corrcoef(rx,ry)[0,1]
def study(S,tag):
    out=[]
    blocks=[(tmin+k*30*DAY,tmin+(k+1)*30*DAY) for k in range(12)]
    for j,nm in enumerate(names):
        row=[]
        for tg in ("dir","abs"):
            ics=[]
            for bs,be in blocks:
                m=(S["t"]>=bs)&(S["t"]<be)
                y=S["fr"][m] if tg=="dir" else np.abs(S["fr"][m])
                ics.append(rankic(S["X"][m,j],y))
            ics=np.array([x for x in ics if not np.isnan(x)])
            row.append((ics.mean(),ics.mean()/(ics.std(ddof=1)/np.sqrt(len(ics))+1e-12),(np.sign(ics)==np.sign(ics.mean())).mean()))
        out.append((nm,row))
    return out
RA=study(A,"A"); RB=study(B,"B")
print("\nRank-IC of each feature vs forward 16-bar move (12 monthly blocks; t across blocks; A=search coins, B=unseen coins)")
print("(with ~70 features x 2 targets x 2 sets, |t|>3.5 is a fair bar)")
def show(target_idx,title):
    print(f"\n== {title} ==")
    rows=[]
    for (nm,ra),(_,rb) in zip(RA,RB): rows.append((abs(ra[target_idx][1])+abs(rb[target_idx][1]) if np.sign(ra[target_idx][0])==np.sign(rb[target_idx][0]) else 0,nm,ra[target_idx],rb[target_idx]))
    rows.sort(reverse=True)
    for sc,nm,a,b in rows[:14]: print(f"  {nm:14s} A: IC {a[0]:+.3f} t={a[1]:+.1f} same-sign blocks {a[2]*100:3.0f}% | B: IC {b[0]:+.3f} t={b[1]:+.1f} same-sign {b[2]*100:3.0f}%")
show(0,"DIRECTION: signed 16-bar forward return"); show(1,"MAGNITUDE: |16-bar forward move| (volatility forecast)")
import pickle; pickle.dump((RA,RB,names),open("ic_results.pkl","wb"))

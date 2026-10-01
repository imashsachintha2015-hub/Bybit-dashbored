import sys, os, pickle
from multiprocessing import Pool
from setups import *
def rsi(c,p=14):
    g=[0];l=[0]
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag,al=ema(g,p),ema(l,p)
    return [100-100/(1+a/b) if b>0 else 100 for a,b in zip(ag,al)]
def btc_up(folder,tf):
    B=load(f"{folder}/BTC_{tf}.json"); c=[b["c"] for b in B]; e50,e200=ema(c,50),ema(c,200)
    return {b["t"]:(e50[i]>e200[i]) for i,b in enumerate(B)}
def analyse2(args):
    path,L,folder,tf=args
    B=load(path)
    if not B or len(B)<600: return []
    bu=btc_up(folder,tf); sym=os.path.basename(path).split("_")[0]; rows=[]
    for side,BB in (("L",B),("S",invert(B))):
        F=feats(BB,L); c=[b["c"] for b in BB]; R=rsi(c); e200=ema(c,200); e50=ema(c,50)
        for i,f in F.items():
            if not f["trend"]: continue
            b=BB[i]; rng=max(b["h"]-b["l"],1e-12); av=sum(x["v"] for x in BB[i-20:i])/20
            up=bu.get(B[i]["t"])
            r={"sym":sym,"side":side,"t":B[i]["t"],"flags":[k for k in ("poc","fvg","ob","ema","wick") if f[k]]}
            r["x"]={"rvol":b["v"]/av if av>0 else 1,"rsi":R[i],"dist200":(b["c"]-e200[i])/abs(b["c"])*100 if False else (abs(b["c"])-abs(e200[i]))/abs(b["c"])*100*(1 if side=="L" else -1),
                    "atrp":f["atr"]/abs(b["c"])*100,"lwick":(min(b["o"],b["c"])-b["l"])/rng,"body":abs(b["c"]-b["o"])/rng,
                    "ret24":(b["c"]-BB[i-24]["c"])/abs(b["c"])*100,"hour":(B[i]["t"]//3600000)%24,
                    "btc_with":(None if up is None else (up if side=="L" else not up)),"riskp":(b["c"]-f["stop"])/abs(b["c"])*100}
            r["R"]={rr:outcome(BB,i,f["stop"],rr=rr,tmax=48) for rr in (1.5,2.0,3.0)}
            rows.append(r)
    return rows
if __name__=="__main__":
    folder,tf,L,tag=sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4]
    syms=sorted({p.split("_")[0] for p in os.listdir(folder) if p.endswith(f"_{tf}.json")})
    with Pool(4) as p: res=p.map(analyse2,[(f"{folder}/{s}_{tf}.json",L,folder,tf) for s in syms])
    pickle.dump([r for x in res for r in x],open(f"mrows_{tag}.pkl","wb")); print(tag,sum(len(x) for x in res))

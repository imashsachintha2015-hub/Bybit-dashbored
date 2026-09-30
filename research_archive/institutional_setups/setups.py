import json, os, sys, random, math
from multiprocessing import Pool
FR=0.0014
def load(path):
    if not os.path.exists(path): return None
    return [{"t":r[0],"o":r[1],"h":r[2],"l":r[3],"c":r[4],"v":r[5]} for r in json.load(open(path))]
def invert(B): return [{"t":b["t"],"o":-b["o"],"h":-b["l"],"l":-b["h"],"c":-b["c"],"v":b["v"]} for b in B]
def ema(x,p):
    k=2/(p+1); r=[x[0]]
    for v in x[1:]: r.append(v*k+r[-1]*(1-k))
    return r
def atr(B,p=14):
    tr=[B[0]["h"]-B[0]["l"]]
    for i in range(1,len(B)):
        tr.append(max(B[i]["h"]-B[i]["l"],abs(B[i]["h"]-B[i-1]["c"]),abs(B[i]["l"]-B[i-1]["c"])))
    return ema(tr,p)
def poc(B,i,L,bins=24):
    w=B[i-L:i]; lo=min(b["l"] for b in w); hi=max(b["h"] for b in w)
    if hi<=lo: return None
    s=(hi-lo)/bins; bv=[0.0]*bins
    for b in w:
        tp=(b["h"]+b["l"]+b["c"])/3; bv[min(bins-1,int((tp-lo)/s))]+=b["v"]
    m=max(range(bins),key=lambda j:bv[j]); return lo+(m+0.5)*s
def feats(B,L):
    """long-side setup flags on bar list B (short handled by inverting prices). Returns dict i->flags"""
    n=len(B); c=[b["c"] for b in B]
    e20,e50,e200=ema(c,20),ema(c,50),ema(c,200); A=atr(B)
    out={}
    # pre-scan FVG and OB zones
    fvgs=[]  # (k_end, bot, top)
    obs=[]   # (k_conf, low, high)
    for k in range(2,n):
        if B[k]["l"]>B[k-2]["h"]: fvgs.append((k,B[k-2]["h"],B[k]["l"]))
    for j in range(10,n-3):
        if B[j]["c"]<B[j]["o"]:
            hh=max(B[x]["h"] for x in range(j-10,j))
            for m in range(j+1,j+4):
                if B[m]["c"]>hh and (B[m]["h"]-B[m]["l"])>=1.5*A[m]:
                    obs.append((m,B[j]["l"],B[j]["h"])); break
    fi=0; oi=0; fa=[]; oa=[]
    for i in range(max(L,205),n-1):
        while fi<len(fvgs) and fvgs[fi][0]<=i-1: fa.append(fvgs[fi]); fi+=1
        while oi<len(obs) and obs[oi][0]<=i-1: oa.append(obs[oi]); oi+=1
        b=B[i]; trend=e50[i]>e200[i] and c[i]>e50[i] and e50[i]>e50[i-10]
        bull=b["c"]>b["o"]; rng=max(b["h"]-b["l"],1e-12)
        f={"trend":trend,"bull":bull}
        # POC
        p=poc(B,i,L); f["poc"]=bool(p and b["l"]<=p*1.001 and b["c"]>p and bull); zl=[b["l"]]
        if f["poc"]: zl.append(p)
        # FVG CE hold (unmitigated, formed within 40 bars)
        fv=False
        for (k,bot,top) in fa[-40:]:
            if i-k>40 or i-k<1: continue
            ce=(bot+top)/2
            if min(B[x]["c"] for x in range(k+1,i))<bot if i-k>1 else False: continue
            if b["l"]<=ce and b["c"]>ce and bull: fv=True; zl.append(bot); break
        f["fvg"]=fv
        ob=False
        for (m,lo,hi) in oa[-30:]:
            if i-m>60: continue
            if min(B[x]["c"] for x in range(m+1,i))<lo if i-m>1 else False: continue
            if b["l"]<=hi and b["c"]>hi and bull: ob=True; zl.append(lo); break
        f["ob"]=ob
        f["ema"]=bool(b["l"]<=e20[i]*1.001 and b["c"]>e20[i] and bull)
        f["wick"]=bool((min(b["o"],b["c"])-b["l"])/rng>=0.35 and (b["c"]-b["l"])/rng>=0.6)
        f["stop"]=min(zl)-0.25*A[i]
        f["atr"]=A[i]
        out[i]=f
    return out
def outcome(B,i,stop,rr=2.0,tmax=32):
    ep=B[i]["c"]; risk=ep-stop
    if risk<=0 or risk/abs(ep)<0.0015: return None
    rp=risk/abs(ep); fr=FR/rp; tp=ep+rr*risk
    for j in range(i+1,min(len(B),i+1+tmax)):
        if B[j]["l"]<=stop: return -1-fr
        if B[j]["h"]>=tp: return rr-fr
    j=min(len(B)-1,i+tmax); return (B[j]["c"]-ep)/risk-fr
def fwd(B,i,h):
    if i+h>=len(B): return None
    return (B[i+h]["c"]-B[i]["c"])/abs(B[i]["c"])
def analyse(args):
    path,L=args
    B=load(path)
    if not B or len(B)<600: return []
    sym=os.path.basename(path).split("_")[0]; rows=[]
    for side,BB in (("L",B),("S",invert(B))):
        F=feats(BB,L)
        for i,f in F.items():
            if not f["trend"]: continue
            r={"sym":sym,"side":side,"t":B[i]["t"],"i":i,"flags":[k for k in ("poc","fvg","ob","ema","wick") if f[k]]}
            r["f"]={h:(fwd(BB,i,h)) for h in (4,8,16,32)}
            r["R"]=outcome(BB,i,f["stop"]); rows.append(r)
    return rows
if __name__=="__main__":
    tag,folder,tf,L=sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4])
    syms=sorted({p.split("_")[0] for p in os.listdir(folder) if p.endswith(f"_{tf}.json")})
    with Pool(4) as p: res=p.map(analyse,[(f"{folder}/{s}_{tf}.json",L) for s in syms])
    import pickle; pickle.dump([r for x in res for r in x],open(f"rows_{tag}.pkl","wb"))
    print(tag,sum(len(x) for x in res))

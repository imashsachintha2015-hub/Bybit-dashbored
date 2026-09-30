from lf import *
import sys
U=universe("1D"); cal=calendar(U); T1,T2=split(cal)
print("coins",len(U),"days",len(cal),"dev<",T1,"val<",T2)
def positions(B,kind,N,long_only,M=None):
    c=[b["c"] for b in B]; h=[b["h"] for b in B]; l=[b["l"] for b in B]; n=len(B); pos=[0]*n; cur=0
    e=ema(c,N) if kind=="ema" else None
    for i in range(N,n):
        if kind=="don":
            hi=max(h[i-N:i]); lo=min(l[i-N:i]); mh=max(h[i-(M or N//2):i]); ml=min(l[i-(M or N//2):i])
            if cur<=0 and c[i]>hi: cur=1
            elif cur>=0 and (not long_only) and c[i]<lo: cur=-1
            if cur==1 and c[i]<ml: cur=0 if long_only else (-1 if c[i]<lo else 0)
            if cur==-1 and c[i]>mh: cur=0
        else:
            cur=1 if c[i]>e[i] else (0 if long_only else -1)
        pos[i]=cur
    return pos
def run(kind,N,long_only,volscale=True):
    per={}  # t -> list of (ret contributions)
    for s,B in U.items():
        pos=positions(B,kind,N,long_only); a=atrp(B); prev=0
        for i in range(1,len(B)):
            p=pos[i-1]; r=B[i]["c"]/B[i-1]["c"]-1
            w=min(2.0,0.03/max(a[i-1],1e-4)) if volscale else 1.0
            g=p*r*w - abs(p-prev)*SIDE*w - (FUND*w if p!=0 else 0); prev=p
            per.setdefault(B[i]["t"],[]).append(g)
    d={t:sum(v)/len(U) for t,v in per.items()}  # capital split equally across universe
    return d
def bench():
    per={}
    for s,B in U.items():
        for i in range(1,len(B)): per.setdefault(B[i]["t"],[]).append(B[i]["c"]/B[i-1]["c"]-1)
    return {t:sum(v)/len(U) for t,v in per.items()}
BH=bench()
def seg(d,lo,hi): return [d[t] for t in sorted(d) if lo<=t<hi]
print("\nBenchmark equal-weight buy&hold:")
for nm,(lo,hi) in {"dev":(0,T1),"val":(T1,T2),"final":(T2,1e18)}.items(): print(f"  {nm:5s} {fmt(stats(seg(BH,lo,hi)))}")
if __name__=="__main__":
    print("\n%-22s | %-58s | %-58s"%("strategy","DEV","VAL"))
    rows=[]
    for kind,Ns in (("don",(10,20,40,60,100)),("ema",(20,50,100,200))):
        for lo_ in (True,False):
            for N in Ns:
                d=run(kind,N,lo_); a=stats(seg(d,0,T1)); b=stats(seg(d,T1,T2))
                print("%-22s | %-58s | %-58s"%(f"{kind}{N} {'LONG' if lo_ else 'L/S'}",fmt(a),fmt(b)))

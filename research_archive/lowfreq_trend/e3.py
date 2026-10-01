import sys; sys.argv=["x"]
from e1 import *
C={s:{b["t"]:b["c"] for b in B} for s,B in U.items()}
days=cal
def xs(L,hold,q=0.2,sign=1,skip=1):
    """sign=+1 momentum (long winners), -1 reversal. rebalance every `hold` days."""
    w={}; out={}; pos={}
    for k,t in enumerate(days):
        if k<L+skip+1: continue
        # daily PnL from yesterday's positions
        tp=days[k-1]; r=0.0
        for s,p in pos.items():
            if t in C[s] and tp in C[s]: r+=p*(C[s][t]/C[s][tp]-1)
        gross=sum(abs(p) for p in pos.values()); r-=gross*FUND
        if (k-L-skip-1)%hold==0:
            sc={}
            for s in U:
                a=C[s].get(days[k-skip]); b=C[s].get(days[k-skip-L])
                if a and b and t in C[s]: sc[s]=a/b-1
            if len(sc)>=10:
                rk=sorted(sc,key=sc.get); m=max(1,int(len(rk)*q)); new={}
                for s in rk[-m:]: new[s]=sign/m*0.5
                for s in rk[:m]: new[s]=-sign/m*0.5
                turn=sum(abs(new.get(s,0)-pos.get(s,0)) for s in set(new)|set(pos)); r-=turn*SIDE; pos=new
        out[t]=r
    return out
if __name__=="__main__":
    print("\nCross-sectional, dollar-neutral (0.5 long / 0.5 short), 20% tails, 14bps RT + funding drag")
    print("%-24s | %-58s | %-58s"%("config","DEV","VAL"))
    for sign,nm in ((1,"MOM"),(-1,"REV")):
        for L,hold in ((3,1),(7,7),(14,7),(28,7),(56,14)):
            d=xs(L,hold,sign=sign)
            print("%-24s | %-58s | %-58s"%(f"{nm} L={L} hold={hold}",fmt(stats(seg(d,0,T1))),fmt(stats(seg(d,T1,T2)))))

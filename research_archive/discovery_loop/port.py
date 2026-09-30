import json, sys, datetime
import disc
from disc import *
init("d4","1H")
ORIG=set(f.split("_")[0] for f in os.listdir("data365") if f.endswith("_1H.json"))
t0=json.load(open("data365/BTC_1H.json"))[0][0]
BASE={'fam':'AMD','gate': 2, 'mr': 0.008, 'tp': 2.0, 'tmax': 96, 'N': 48, 'D': 0.8, 'v': 1.2, 'w': 12, 'W': 6, 'comp': 10, 's': 0.6, 'b': 0.1}
def portfolio(trades,risk=0.02,maxpos=3,lev=10,start=10.0,minnot=5.0):
    """trades: (entry_t,sym,R,side,exit_t,rp). chronological, realized-equity sizing, cap concurrent notional at lev x equity."""
    trades=sorted(trades); openp=[]; eq=start; pk=start; dd=0; done=0; skipped=0; wins=0; curve=[]
    def close_upto(t):
        nonlocal eq,pk,dd,wins
        for p in sorted([p for p in openp if p[0]<=t]):
            openp.remove(p); eq+=p[1]; wins+=p[1]>0; pk=max(pk,eq); dd=min(dd,eq/pk-1)
    for et,sym,R,sd,xt,rp in trades:
        close_upto(et)
        if eq<1.0: break
        if len(openp)>=maxpos or any(p[2]==sym for p in openp): skipped+=1; continue
        used=sum(p[3] for p in openp); notional=min(eq*risk/rp, eq*lev-used)
        if notional<minnot: skipped+=1; continue
        openp.append((xt,notional*rp*R,sym,notional)); done+=1
    close_upto(1e18)
    return eq,done,skipped,wins,dd*100
def build(res):
    out=[]
    for t,s,r,sd in res:
        ex=disc.EXIT.get((s,t))
        if ex: out.append((t,s,r,sd,ex[0],ex[1]))
    return out
_,res=run_cfg(BASE); T=build(res)
def rep(lbl,tr,**kw):
    e,n,sk,w,dd=portfolio(tr,**kw); print(f" {lbl:58s} $10 -> ${e:8.2f} | trades taken {n:4d} (skipped {sk:4d}) win {w/max(n,1)*100:3.0f}% maxDD {dd:5.1f}%")
unseen=[x for x in T if (x[1] not in ORIG) or x[0]<t0]
print("Portfolio simulation of the frozen AMD-FVG candidate (max 3 open, one per symbol, 10x cap, min notional $5, 8bps limit costs)")
for risk in (0.01,0.02,0.03):
    print(f"-- risk {risk*100:.0f}% of equity per trade --")
    rep("ALL 33 coins x 900d",T,risk=risk); rep("UNSEEN only (new coins any time + old coins older)",unseen,risk=risk)
print("-- by year, unseen only, 2% risk, fresh $10 each --")
yr=defaultdict(list)
for x in unseen: yr[datetime.datetime.utcfromtimestamp(x[0]/1000).year].append(x)
for y in sorted(yr): rep(f"year {y}",yr[y],risk=0.02)
print("-- harsher: pen 0.2 ATR, 16bps, unseen, 2% --")
P=dict(BASE); P["pf"]=0.2; disc.A_FEE[0]=16e-4; disc.EXIT.clear(); _,rh=run_cfg(P); Th=build(rh)
rep("unseen harsh",[x for x in Th if (x[1] not in ORIG) or x[0]<t0],risk=0.02)

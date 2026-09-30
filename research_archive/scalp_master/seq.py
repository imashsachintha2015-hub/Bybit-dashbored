import sys, io, contextlib, json, os
import scalp
from scalp import *
with contextlib.redirect_stdout(io.StringIO()):
    import port
from port import portfolio
CFG={'fam':'IMBAL','gate': 0, 'mr': 0.006, 'tp': 1.5, 'tmax': 24, 'b': 0.1, 'N': 48, 'D': 0.8, 'v': 0, 'w': 12, 'W': 3, 'comp': 99, 's': 0.3}
def trades(folder,fee):
    scalp.G.clear(); scalp.EXIT.clear(); scalp.init(folder,"5m"); scalp.A_FEE[0]=fee*1e-4
    _,res=scalp.run_cfg(CFG); out=[]
    for t,s,r,sd in res:
        ex=scalp.EXIT.get((s,t))
        if ex: out.append((t,s,r,sd,ex[0],ex[1]))
    return out
r=json.load(open("s5a/BTC_5m.json")); a,b=r[0][0],r[-1][0]; T2=a+(b-a)*.75
def line(lbl,tr):
    if not tr: print(lbl,"no trades"); return
    R=[x[2] for x in tr]; e,n,sk,w,dd=portfolio(tr,risk=0.02)
    print(f" {lbl:44s} signals {len(tr):4d} avgR {sum(R)/len(R):+.3f} totalR {sum(R):+6.1f} | $10 -> ${e:7.2f} (trades taken {n:3d}, skipped {sk:3d}, win {w/max(n,1)*100:3.0f}%, maxDD {dd:5.1f}%)")
for fee in (4,8):
    print(f"--- limit-order cost {fee} bps, top scalp candidate (5m IMBAL), max 3 open, 2% risk ---")
    A_=trades("s5a",fee); line("search coins, full 120d (in-sample!)",A_); line("search coins, FINAL third (untouched)",[x for x in A_ if x[0]>=T2])
    U_=trades("s5b",fee); line("UNSEEN coins, all 120d",U_)

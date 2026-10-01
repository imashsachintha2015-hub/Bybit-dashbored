import json, sys, io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    import port
from port import *
import disc
def tot(tr): return sum(x[2] for x in tr), len(tr)
r,n=tot(T); print(f"ALL 33 coins x 900d (uncapped): total {r:+.1f}R over {n} trades (avg {r/n:+.2f}R)")
r,n=tot(unseen); print(f"UNSEEN only (uncapped): total {r:+.1f}R over {n} trades (avg {r/n:+.2f}R)")
# capped: max 3 open, one per symbol -> total R
def capped(tr,maxpos=3):
    tr=sorted(tr); openp=[]; s=0; k=0
    for et,sym,R,sd,xt,rp in tr:
        openp=[p for p in openp if p[0]>et]
        if len(openp)>=maxpos or any(p[1]==sym for p in openp): continue
        openp.append((xt,sym)); s+=R; k+=1
    return s,k
r,n=capped(unseen); print(f"UNSEEN, max 3 open, one per symbol: total {r:+.1f}R over {n} trades")
r,n=capped(T); print(f"ALL, max 3 open: total {r:+.1f}R over {n} trades")
# final quarter of the original 12-coin year
import json
b=json.load(open("data365/BTC_1H.json")); a0,b0=b[0][0],b[-1][0]; T2=a0+(b0-a0)*.75
fq=[x for x in T if x[1] in ORIG and x[0]>=T2]; r,n=tot(fq); print(f"FINAL quarter (untouched at selection), 12 coins: total {r:+.1f}R over {n} trades; capped: {capped(fq)[0]:+.1f}R / {capped(fq)[1]} trades")
r,n=tot([x for x in unseen if x[0]>=T2]); print(f"Last quarter, unseen coins only: total {r:+.1f}R over {n} trades")

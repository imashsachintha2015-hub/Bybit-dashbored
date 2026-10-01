import pickle, json, statistics as st, io, contextlib, collections
import mtf
from disc import tstat
with contextlib.redirect_stdout(io.StringIO()):
    from port import portfolio
R=pickle.load(open("mtf_14.pkl","rb"))
A=set("BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR".split())
b=json.load(open("s15/BTC_15m.json")); t0,t1=b[0][0],b[-1][0]; T1=t0+(t1-t0)*.5; T2=t0+(t1-t0)*.75
def S(tr,coins,lo,hi): return tstat([(x[0],x[2]) for x in tr if (x[1] in A)==(coins=="A") and lo<=x[0]<hi])
cells=collections.defaultdict(list)
for (si,ti),tr in R.items():
    bias,poi,conf,L,sess=mtf.SETS[si]
    if conf!="choch": continue
    segs=[S(tr,"A",0,T1),S(tr,"A",T1,T2),S(tr,"A",T2,9e18),S(tr,"B",0,T1),S(tr,"B",T1,T2),S(tr,"B",T2,9e18)]
    cells["all"].append(segs)
print("CHoCH family: 240 configs; median avgR per segment (A=search coins, B=unseen)")
names=["A dev","A val","A final","B 1st half","B 3rd q","B 4th q"]
for k,i in enumerate(names):
    v=[s[k][1] for s in cells["all"] if s[k][0]>=20]
    print(f"   {i:10s} median {st.median(v):+.3f}  share>0 {sum(x>0 for x in v)/len(v)*100:3.0f}%  (n cfg {len(v)})")
# pooled: one fixed pre-declared config per structural choice, report by component
print("\nBreakdown within CHoCH, median over configs, B = unseen coins full year:")
for nm,idx in (("bias",0),("poi",1),("sweepL",3),("session",4)):
    g=collections.defaultdict(list)
    for (si,ti),tr in R.items():
        if mtf.SETS[si][2]!="choch": continue
        s=S(tr,"B",0,9e18)
        if s[0]>=30: g[mtf.SETS[si][idx]].append(s[1])
    print(f"   {nm:8s}: "+"  ".join(f"{k}={st.median(x):+.3f}" for k,x in g.items()))
g=collections.defaultdict(list)
for (si,ti),tr in R.items():
    if mtf.SETS[si][2]!="choch": continue
    s=S(tr,"B",0,9e18)
    if s[0]>=30: g[str(mtf.EXITS[ti])].append(s[1])
print("   exit    : "+"  ".join(f"{k}={st.median(x):+.3f}" for k,x in g.items()))

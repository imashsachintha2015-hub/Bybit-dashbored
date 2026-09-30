import pickle, json, io, contextlib, math
from disc import tstat
with contextlib.redirect_stdout(io.StringIO()):
    from port import portfolio
R=pickle.load(open("lib_14.pkl","rb")); G=pickle.load(open("lib_0.pkl","rb"))
b=json.load(open("s15/BTC_15m.json")); t0,t1=b[0][0],b[-1][0]; TM=t0+(t1-t0)/2
rows=[]
for k,tr in R.items():
    n,m,t=tstat([(x[0],x[2]) for x in tr]); g=G[k]; gm=sum(x[2] for x in g)/max(len(g),1)
    h1=tstat([(x[0],x[2]) for x in tr if x[0]<TM]); h2=tstat([(x[0],x[2]) for x in tr if x[0]>=TM])
    e,nt,sk,w,dd=portfolio(sorted(tr),risk=0.02)
    wr=sum(1 for x in tr if x[2]>0)/max(len(tr),1)*100
    rows.append((m,k,n,wr,gm,t,h1[1],h2[1],e,nt,dd))
rows.sort(key=lambda r:-r[0])
print(f"{len(rows)} tests = 42 strategies x 2 exits x 2 timeframes, 24 coins x 365d, 14bps round trip. Bonferroni 5% needs |t|>3.4")
print(f"{'tf':3s} {'strategy':42s} {'exit':12s} {'signals':>7s} {'win%':>5s} {'grossR':>7s} {'netR':>6s} {'t':>5s} {'H1':>6s} {'H2':>6s} | $10 ->   (trades, maxDD)")
for m,k,n,wr,gm,t,a,b_,e,nt,dd in rows:
    print(f"{k[0]:3s} {k[1][:42]:42s} {k[2]:12s} {n:7d} {wr:5.0f} {gm:+7.3f} {m:+6.3f} {t:+5.1f} {a:+6.2f} {b_:+6.2f} | ${e:7.2f} ({nt:4d}, {dd:4.0f}%)")
print("\npositive net:",sum(1 for r in rows if r[0]>0),"of",len(rows),"| positive in BOTH halves:",sum(1 for r in rows if r[6]>0 and r[7]>0),"| t>3.4:",sum(1 for r in rows if r[5]>3.4))
print("positive GROSS (before fees):",sum(1 for r in rows if r[4]>0),"of",len(rows))
print("ended above $10:",sum(1 for r in rows if r[8]>10))

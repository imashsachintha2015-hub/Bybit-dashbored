import pickle, sys, random
tag=sys.argv[1]; bar_ms=int(sys.argv[2]); cool=int(sys.argv[3])
rows=pickle.load(open(f"rows_{tag}.pkl","rb"))
ts=sorted(r["t"] for r in rows); t60=ts[int(len(ts)*0.6)]
groups={"BASE trend":lambda f:True,"POC":lambda f:"poc" in f,"FVG":lambda f:"fvg" in f,"OB":lambda f:"ob" in f,
 "EMA20":lambda f:"ema" in f,"WICK":lambda f:"wick" in f,"conf>=2 (poc/fvg/ob/ema)":lambda f:len([x for x in f if x!="wick"])>=2,
 "conf>=3":lambda f:len([x for x in f if x!="wick"])>=3,"conf>=2 + wick":lambda f:len([x for x in f if x!="wick"])>=2 and "wick" in f}
def pick(g,side=None):
    last={}; out=[]
    for r in sorted(rows,key=lambda r:r["t"]):
        if side and r["side"]!=side: continue
        if not groups[g](r["flags"]): continue
        k=(r["sym"],r["side"])
        if r["t"]-last.get(k,0)<cool*bar_ms: continue
        last[k]=r["t"]; out.append(r)
    return out
def ci(vals_by_day):
    d=list(vals_by_day.values()); rnd=random.Random(2); b=[]
    for _ in range(800):
        s=[x for _ in d for x in rnd.choice(d)]; b.append(sum(s)/len(s))
    b.sort(); return b[20],b[779]
def stat(rs,key):
    v=[(r["t"]//86400000,key(r)) for r in rs if key(r) is not None]
    if not v: return "n=0"
    days={}
    for d,x in v: days.setdefault(d,[]).append(x)
    m=sum(x for _,x in v)/len(v); lo,hi=ci(days); return f"n={len(v):5d} {m:+.3f} [{lo:+.3f},{hi:+.3f}]"
def bps(rs,h): 
    v=[r["f"][h] for r in rs if r["f"][h] is not None]
    return (sum(v)/len(v)*1e4,len(v)) if v else (0,0)
print(f"{'setup':28s} {'side':4s} {'n':>6s} {'gross16 bps':>11s} {'excess vs base':>14s} | avgR net (sim 2R, 14bps) all | train | test")
base={s:bps(pick("BASE trend",s if s!="ALL" else None),16)[0] for s in ("ALL","L","S")}
for g in groups:
    for s in ("ALL","L","S"):
        rs=pick(g,None if s=="ALL" else s)
        if len(rs)<30: continue
        gb,n=bps(rs,16)
        tr=[r for r in rs if r["t"]<t60]; te=[r for r in rs if r["t"]>=t60]
        print(f"{g:28s} {s:4s} {n:6d} {gb:+11.1f} {gb-base[s]:+14.1f} | {stat(rs,lambda r:r['R'])} | {stat(tr,lambda r:r['R'])} | {stat(te,lambda r:r['R'])}")

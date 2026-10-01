import pickle, sys, itertools, math, random
tag=sys.argv[1]; RR=float(sys.argv[2]); BARMS=int(sys.argv[3]); COOL=int(sys.argv[4])
rows=[r for r in pickle.load(open(f"mrows_{tag}.pkl","rb")) if r["R"][RR] is not None and r["x"]["btc_with"] is not None]
rows.sort(key=lambda r:r["t"]); N=len(rows)
items={}
def add(name,fn):
    m=0
    for j,r in enumerate(rows):
        if fn(r): m|=1<<j
    items[name]=m
for k in ("poc","fvg","ob","ema","wick"): add(k.upper(),lambda r,k=k:k in r["flags"])
add("rvol>=1.5",lambda r:r["x"]["rvol"]>=1.5); add("rvol>=2.5",lambda r:r["x"]["rvol"]>=2.5); add("rvol<0.8",lambda r:r["x"]["rvol"]<0.8)
add("rsi<45",lambda r:r["x"]["rsi"]<45); add("rsi45-60",lambda r:45<=r["x"]["rsi"]<60); add("rsi>=60",lambda r:r["x"]["rsi"]>=60)
add("dist200<2%",lambda r:r["x"]["dist200"]<2); add("dist200 2-6%",lambda r:2<=r["x"]["dist200"]<6); add("dist200>=6%",lambda r:r["x"]["dist200"]>=6)
add("lwick>=.4",lambda r:r["x"]["lwick"]>=0.4); add("body>=.6",lambda r:r["x"]["body"]>=0.6)
add("ret24<0",lambda r:r["x"]["ret24"]<0); add("ret24>=3%",lambda r:r["x"]["ret24"]>=3)
add("btc_with",lambda r:r["x"]["btc_with"]); add("btc_against",lambda r:not r["x"]["btc_with"])
add("risk<1%",lambda r:r["x"]["riskp"]<1); add("risk1-2%",lambda r:1<=r["x"]["riskp"]<2); add("risk>=2%",lambda r:r["x"]["riskp"]>=2)
add("Asia",lambda r:r["x"]["hour"]<8); add("EU",lambda r:8<=r["x"]["hour"]<16); add("US",lambda r:r["x"]["hour"]>=16)
add("LONG",lambda r:r["side"]=="L"); add("SHORT",lambda r:r["side"]=="S")
add("atr low",lambda r:r["x"]["atrp"]<0.6); add("atr high",lambda r:r["x"]["atrp"]>=1.0)
names=list(items); print("items",len(names),"rows",N)
tmid=rows[N//2]["t"]; idx_a=[j for j,r in enumerate(rows) if r["t"]<tmid]; idx_b=[j for j,r in enumerate(rows) if r["t"]>=tmid]
maskA=sum(1<<j for j in idx_a); maskB=sum(1<<j for j in idx_b)
def bits(m):
    while m:
        l=m&-m; yield l.bit_length()-1; m^=l
def dedupe(js):
    last={}; out=[]
    for j in js:
        r=rows[j]; k=(r["sym"],r["side"])
        if r["t"]-last.get(k,0)<COOL*BARMS: continue
        last[k]=r["t"]; out.append(j)
    return out
def tstat(js):
    if len(js)<2: return 0,0,0
    d={}
    for j in js: d.setdefault(rows[j]["t"]//86400000,[]).append(rows[j]["R"][RR])
    n=len(js); m=sum(x for v in d.values() for x in v)/n
    # cluster-robust se of mean
    s=sum((sum(v)-m*len(v))**2 for v in d.values()); se=math.sqrt(s)/n
    return m,(m/se if se>0 else 0),n
def scan(train_mask,test_mask,label,minn=40,tmin=float(sys.argv[5]) if len(sys.argv)>5 else 3.0):
    cands=[]
    combos=[(a,) for a in names]+list(itertools.combinations(names,2))+list(itertools.combinations(names,3))
    for cb in combos:
        if ("LONG" in cb and "SHORT" in cb) or ("btc_with" in cb and "btc_against" in cb): continue
        m=~0
        for c in cb: m&=items[c]
        mt=m&train_mask
        if mt.bit_count()<minn: continue
        js=dedupe(list(bits(mt)))
        mean,t,n=tstat(js)
        if n>=minn and mean>0 and t>=tmin: cands.append((t,mean,n,cb,m))
    cands.sort(reverse=True)
    print(f"\n[{label}] combos scanned {len(combos)}; passing train (n>={minn}, t>={tmin}, R>0): {len(cands)}")
    ok=0; res=[]
    for t,mean,n,cb,m in cands[:25]:
        js=dedupe(list(bits(m&test_mask))); mt,tt,nt=tstat(js)
        res.append((cb,n,mean,t,nt,mt,tt)); ok+= (nt>=20 and mt>0)
    for cb,n,mean,t,nt,mt,tt in res[:15]:
        print(f"  {' & '.join(cb):55s} train n={n:4d} R={mean:+.2f} t={t:.1f} | TEST n={nt:4d} R={mt:+.2f} t={tt:+.1f}")
    print(f"  top-25 with test n>=20: positive-in-test {ok}/{len(res)}")
    # pooled strategy of top-10 (union of signals), test only
    u=0
    for cb,*_ in [(x[3],) for x in cands[:10]]:
        mm=~0
        for c in cb: mm&=items[c]
        u|=mm
    js=dedupe(list(bits(u&test_mask))); mt,tt,nt=tstat(js)
    print(f"  POOLED top-10 rules on TEST: n={nt} avgR={mt:+.3f} t={tt:+.1f}  (per-year trades: {nt*2 if label.startswith('A') else nt*2})")
scan(maskA,maskB,"A: fit first half -> test second half")
scan(maskB,maskA,"B: fit second half -> test first half")

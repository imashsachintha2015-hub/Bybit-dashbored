import pickle, math, statistics as st
rows=sorted(pickle.load(open("gradedata.pkl","rb")),key=lambda r:r["t"])
def mean(v): return sum(v)/len(v) if v else 0
def welch(a,b):
    if len(a)<8 or len(b)<8: return 0
    ma,mb=mean(a),mean(b); va=sum((x-ma)**2 for x in a)/(len(a)-1); vb=sum((x-mb)**2 for x in b)/(len(b)-1)
    se=math.sqrt(va/len(a)+vb/len(b)); return (ma-mb)/se if se>0 else 0
print("== Loss anatomy (all 1107 trades) ==")
L=[r for r in rows if r["R"]<0]; W=[r for r in rows if r["R"]>=0]
dur=lambda r:(r["exit"]-r["t"])/3600000
print(f" losers {len(L)} ({len(L)/len(rows)*100:.0f}%), avg {mean([r['R'] for r in L]):+.2f}R | winners {len(W)}, avg {mean([r['R'] for r in W]):+.2f}R")
print(f" median hours in trade: losers {st.median([dur(r) for r in L]):.0f}h, winners {st.median([dur(r) for r in W]):.0f}h")
print(f" stopped out within 3h of fill: {sum(1 for r in L if dur(r)<=3)/len(L)*100:.0f}% of losers; within 12h: {sum(1 for r in L if dur(r)<=12)/len(L)*100:.0f}%")
print(f" 'time exits' (96h, R between -1 and +2): {sum(1 for r in rows if -0.9<r['R']<1.9)} trades, avg {mean([r['R'] for r in rows if -0.9<r['R']<1.9]):+.2f}R")
print("\n== A-priori loss hypotheses (fixed BEFORE looking; all trades, split by chronological half) ==")
H=[("H1 BTC drifting AGAINST the trade (aligned BTC 96h move < 0)",lambda r:r["btc"]<0),
   ("H2 high-volatility regime (BTC ATR% > 0.7)",lambda r:r["btcatr"]>0.7),
   ("H3 obstacle: range high closer than 2R (room < 2)",lambda r:r["room"]<2),
   ("H4 stale setup (fill wait >= 8 bars)",lambda r:r["wait"]>=8),
   ("H5 weak displacement (body < 1.0 ATR)",lambda r:r["disc"]<1.0),
   ("H6 illiquid coin (bottom third of dollar volume)",None),
   ("H7 Asia session fill (00-08 UTC)",lambda r:r["hour"]<8),
   ("H8 weekend fill (Sat/Sun)",lambda r:r["dow"] in (5,6)),
   ("H9 small FVG (< 0.15 ATR)",lambda r:r["fvg"]<0.15)]
liq=sorted(r["liq"] for r in rows); lc=liq[len(liq)//3]
H[5]=(H[5][0],lambda r:r["liq"]<=lc)
h=len(rows)//2
print(f"{'hypothesis':62s} {'n flag':>6s} {'avgR flag':>9s} {'avgR else':>9s} {'t':>5s} | 1st half diff | 2nd half diff")
for name,fn in H:
    a=[r["R"] for r in rows if fn(r)]; b=[r["R"] for r in rows if not fn(r)]
    d=[]
    for part in (rows[:h],rows[h:]):
        pa=[r["R"] for r in part if fn(r)]; pb=[r["R"] for r in part if not fn(r)]; d.append(mean(pa)-mean(pb))
    print(f"{name:62s} {len(a):6d} {mean(a):+9.2f} {mean(b):+9.2f} {welch(a,b):+5.1f} | {d[0]:+13.2f} | {d[1]:+13.2f}")
print("(9 tests: |t|>2.8 needed for Bonferroni 5%)")

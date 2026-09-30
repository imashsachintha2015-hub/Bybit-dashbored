import pickle, random, statistics as st, sys, json
from replay import *
Bsig,Csig=pickle.load(open("signals.pkl","rb"))
K={s:load(s,"15m") for s in C_SYMS}
def mirror(sig,bars,i):
    ep=bars[i]["close"]; m=dict(sig)
    m["direction"]="SHORT" if sig["direction"]=="LONG" else "LONG"
    m["stop_p"]=2*ep-sig["stop_p"]; m["target_p"]=2*ep-sig["target_p"]
    if "tp1" in sig: m["tp1"]=2*ep-sig["tp1"]
    return m
def portfolio(items,maxpos=3):
    items=sorted(items,key=lambda x:x[2]); openp={}; acc=[]
    for it in items:
        sym,i,ts,s,r=it
        for k in [k for k,v in openp.items() if v<=ts]: del openp[k]
        if sym in openp or len(openp)>=maxpos: continue
        openp[sym]=ts+900000*(r["exit_i"]-i+1) if False else K[sym][r["exit_i"]]["start"]+900000
        acc.append(it)
    return acc
def run(sigs,conv="close",tp_first=False,fric=FRIC,use_tp="target_p",mirror_=False,maxpos=3):
    items=[]
    for sym,i,ts,s in sigs:
        bars=K[sym]; ss=mirror(s,bars,i) if mirror_ else s
        r=sim(bars,i,ss,conv,tp_first,fric,use_tp)
        if r: items.append((sym,i,ts,ss,r))
    return portfolio(items,maxpos)
def summ(acc,key="R"):
    if not acc: return "n=0"
    R=[a[4][key] for a in acc]; n=len(R); m=sum(R)/n; w=sum(1 for x in R if x>0)/n
    gp=sum(x for x in R if x>0); gl=-sum(x for x in R if x<0)
    days={}
    for a in acc: days.setdefault(a[2]//86400000,[]).append(a[4][key])
    dk=list(days.values()); rnd=random.Random(1); bs=[]
    for _ in range(1500):
        s=[x for _ in dk for x in rnd.choice(dk)]; bs.append(sum(s)/len(s))
    bs.sort()
    return f"n={n:4d} win={w*100:5.1f}% avgR={m:+.3f} PF={gp/gl if gl else 9:4.2f} CI95=[{bs[37]:+.3f},{bs[1462]:+.3f}]"
def bykind(acc):
    d={}
    for a in acc: d.setdefault(a[3]["kind"],[]).append(a[4]["R"])
    for k,v in sorted(d.items()): print(f"     {k[:40]:40s} n={len(v):3d} avgR={sum(v)/len(v):+.3f} win={sum(x>0 for x in v)/len(v)*100:.0f}%")
def exits(acc):
    d={}
    for a in acc: d[a[4]["kind"]]=d.get(a[4]["kind"],0)+1
    print("     exits:",d)
def block(name,sigs):
    print(f"\n=== {name}  (raw signals {len(sigs)})")
    for conv in ("close","nextopen"):
        for tpf in (False,True):
            if tpf and conv=="nextopen": continue
            acc=run(sigs,conv,tpf)
            print(f" entry={conv:8s} tie={'TP-first' if tpf else 'STOP-first':10s} real: {summ(acc)}")
            if conv=="close" and not tpf:
                print(f"     booked(executor labels): {summ(acc,'booked')}"); exits(acc); bykind(acc)
    acc=run(sigs,"nextopen"); print(" nextopen+unlimited positions:",summ(run(sigs,"nextopen",maxpos=99)))
    for f in (0.0008,0.0020,0.0030):
        print(f" friction {f*1e4:.0f}bps (nextopen):",summ(run(sigs,"nextopen",fric=f)))
    m=run(sigs,"nextopen",mirror_=True); print(" MIRROR control (flip dir, same geometry, nextopen):",summ(m))
    m=run(sigs,"close",mirror_=True); print(" MIRROR control (close entry):",summ(m))
    acc=run(sigs,"nextopen")
    if acc:
        ts=sorted(a[2] for a in acc); mid=ts[len(ts)//2]
        print(" first half :",summ([a for a in acc if a[2]<mid])); print(" second half:",summ([a for a in acc if a[2]>=mid]))
if __name__=="__main__":
    S2=[x for x in Bsig if x[3]["kind"]=="S2"]; S7=[x for x in Bsig if x[3]["kind"]=="S7"]
    print("agent decisions:",{d:sum(1 for x in Bsig if x[3]["agent"]==d) for d in ("PASS","ADJUST","REJECT")})
    block("MODEL B, no agent filter",Bsig)
    block("MODEL B, agents applied (REJECT dropped, TP adj applied)",[(a,b,c,dict(s,target_p=s["tp_adj"] or s["target_p"])) for a,b,c,s in Bsig if s["agent"]!="REJECT"])
    block("  S2 only (no agents)",S2); block("  S7 only (no agents)",S7)
    block("  agent-REJECTED signals only",[x for x in Bsig if x[3]["agent"]=="REJECT"])
    block("CHAMPIONSHIP (TP=2R as live, no partial)",Csig)
    # what documented 50/50 would give: tp1 only
    block("CHAMPIONSHIP if exit at TP1 only",[(a,b,c,dict(s,target_p=s["tp1"])) for a,b,c,s in Csig])
    for reg in ("BULL","BEAR"):
        block(f"CHAMPIONSHIP {reg} only",[x for x in Csig if x[3]["regime"]==reg])

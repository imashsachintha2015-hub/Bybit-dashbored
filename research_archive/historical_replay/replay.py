import sys, os, json, math, random, bisect, statistics as st
sys.path.insert(0, "../..")
os.environ.setdefault("BYBIT_BASE_URL","https://invalid.local")
from multiprocessing import Pool
import daemons.cme_x5_pure_engine as X
from backend_lib.championship_engine import ChampionshipDualRegimeEngine
from daemons.agents import SRAgent, POCPathfinderAgent, FVGImpactAgent, ConfluenceScorer
D="data"
B_SYMS=["BTC","ETH","SOL","XRP","LINK","SEI","AVAX","DOGE","BNB","ADA","DOT","POL","LTC","NEAR","APT"]
C_SYMS=B_SYMS+["TAO","TIA","WLD","ONDO"]
def load(s,b):
    p=f"{D}/{s}_{b}.json"
    if not os.path.exists(p): return []
    return [{"start":r[0],"open":r[1],"high":r[2],"low":r[3],"close":r[4],"volume":r[5]} for r in json.load(open(p))]

def gen_b(sym):
    k=load(sym,"15m"); h1=load(sym,"1H"); h4=load(sym,"4H")
    if len(k)<300: return []
    sr,poc,fvg,sc=SRAgent(),POCPathfinderAgent(),FVGImpactAgent(),ConfluenceScorer()
    t1=[b["start"] for b in h1]; t4=[b["start"] for b in h4]
    out=[]
    for i in range(100,len(k)-1):
        w=k[i-99:i+1]
        s2=X.detect_s2(sym,w,None); s7=X.detect_s7(sym,w,None)
        sig=s2 or s7
        if not sig: continue
        close_t=k[i]["start"]+900000
        a=bisect.bisect_right(t1,close_t-3600000); b_=bisect.bisect_right(t4,close_t-14400000)
        k1h=h1[max(0,a-60):a]; k4h=h4[max(0,b_-50):b_]
        vp=X.volume_profile(w[-X.VP_LOOKBACK:])
        s=dict(sig)
        try: r1=sr.analyze(sym,s,k1h,k4h,w)
        except Exception as e: r1={"score":0.7,"adjustment":None}
        try: r2=poc.analyze(sym,s,vp)
        except Exception as e: r2={"score":0.7,"adjustment":None}
        try: r3=fvg.analyze(sym,s,w)
        except Exception as e: r3={"score":0.7,"adjustment":None}
        v=sc.evaluate({"sr":r1,"poc":r2,"fvg":r3})
        s["kind"]="S2" if s2 else "S7"
        s["agent"]=v["decision"]; s["agent_score"]=v["final_score"]
        s["scores"]=[r1.get("score"),r2.get("score"),r3.get("score")]
        s["tp_adj"]=v["adjustments"].get("target_p") if v["adjustments"] else None
        out.append((sym,i,k[i]["start"],s))
    return out

def gen_c(sym):
    k=load(sym,"15m"); btc=load("BTC","15m")
    if len(k)<400: return []
    bt={b["start"]:j for j,b in enumerate(btc)}
    eng=ChampionshipDualRegimeEngine(); out=[]
    for i in range(250,len(k)-1):
        j=bt.get(k[i]["start"])
        if j is None or j<250: continue
        c=eng.scan_candidate(sym,k[i-249:i+1],btc[j-249:j+1])
        if not c: continue
        e,sp=c["entry_price"],c["stop_loss"]
        s={"kind":c["archetype"],"regime":c["regime"],"direction":c["direction"],"entry_p":e,"stop_p":sp,
           "target_p":c["tp2"],"tp1":c["tp1"],"risk_pct":abs(e-sp)/e}
        out.append((sym,i,k[i]["start"],s))
    return out

FRIC=0.0014
def sim(bars,i,sig,entry_conv="close",tp_first=False,fric=FRIC,use_tp="target_p"):
    d=1 if sig["direction"]=="LONG" else -1
    stop=sig["stop_p"]; tp=sig.get(use_tp) or sig["target_p"]
    if entry_conv=="close": ep=bars[i]["close"]; j0=i+1
    else: ep=bars[i+1]["open"]; j0=i+1
    risk_pct_sig=sig["risk_pct"]
    # real risk from actual entry & stop
    rp=abs(ep-stop)/ep
    if (d==1 and stop>=ep) or (d==-1 and stop<=ep): return None
    if (d==1 and tp<=ep) or (d==-1 and tp>=ep): return None
    fr=fric/rp
    cur=stop; be=tr=False; best=0.0
    for j in range(j0,min(len(bars),j0+48)):
        b=bars[j]; hi,lo,op=b["high"],b["low"],b["open"]
        fav_ext=(hi-ep)/ep if d==1 else (ep-lo)/ep
        stop_hit=(lo<=cur) if d==1 else (hi>=cur)
        tp_hit=(hi>=tp) if d==1 else (lo<=tp)
        def R(x): return d*(x-ep)/(rp*ep)-fr
        def res(kind,x,booked):
            return {"kind":kind,"exit_i":j,"R":R(x),"booked":booked-fr,"bars":j-j0+1,"rp":rp,"be":be,"tr":tr,"mfe":best}
        if stop_hit and tp_hit:
            if tp_first: return res("TP",tp,min(3.5,max(1,abs(tp-ep)/(rp*ep))))
            x=min(op,cur) if d==1 else max(op,cur)
            return stop_exit(res,x,cur,be,tr)
        if stop_hit:
            x=min(op,cur) if d==1 else max(op,cur)
            return stop_exit(res,x,cur,be,tr)
        if tp_hit: return res("TP",tp,min(3.5,max(1,abs(tp-ep)/(rp*ep))))
        best=max(best,fav_ext/rp)
        rm=fav_ext/max(risk_pct_sig,1e-6)
        if (rm>=0.70 or fav_ext>=0.008) and not be:
            bp=ep*1.001 if d==1 else ep*0.999
            if (d==1 and bp>cur) or (d==-1 and bp<cur): cur=bp; be=True
        if (rm>=1.20 or fav_ext>=0.015) and not tr:
            tpp=ep+d*rp*ep*0.5
            if (d==1 and tpp>cur) or (d==-1 and tpp<cur): cur=tpp; tr=True
    j=min(len(bars),j0+48)-1
    fav=d*(bars[j]["close"]-ep)/ep
    r=res("TIMEOUT",bars[j]["close"],fav/risk_pct_sig); return r
def stop_exit(res,x,cur,be,tr):
    if tr: return res("TRAIL",x,0.5)
    if be: return res("BE",x,0.05)
    return res("STOP",x,-1.0)

def gen_all():
    with Pool(4) as p:
        B=p.map(gen_b,B_SYMS); C=p.map(gen_c,C_SYMS)
    return [x for l in B for x in l],[x for l in C for x in l]

if __name__=="__main__":
    Bsig,Csig=gen_all()
    import pickle; pickle.dump((Bsig,Csig),open("signals.pkl","wb"))
    print(len(Bsig),len(Csig))

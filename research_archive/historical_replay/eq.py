import sys
from analyze import *
START=10.0
def exit_ts(a): return K[a[0]][a[4]["exit_i"]]["start"]
def equity(acc,mode):
    """acc: portfolio-accepted trades. mode 'risk2' compounding 2%/trade (notional<=10x eq); 'flat50' $50 notional."""
    eq=START; pk=START; mdd=0; wins=0; lo=START; ex=0
    for a in sorted(acc,key=exit_ts):
        R=a[4]["R"]; rp=a[4]["rp"]
        if eq<=0.5: break
        if mode=="risk2":
            frac=min(0.02,10*rp); pnl=eq*frac*R
        else:
            pnl=50.0*rp*R
        ex+=1; eq+=pnl; wins+=pnl>0; pk=max(pk,eq); mdd=min(mdd,(eq-pk)/pk); lo=min(lo,eq)
    n=ex
    return eq,n,wins,mdd*100,lo
def line(nm,sigs,conv="nextopen"):
    a3=run(sigs,conv,maxpos=3); a2=run(sigs,conv,maxpos=2)
    e1=equity(a3,"risk2"); e2=equity(a2,"flat50")
    days=(max(K["BTC"][-1]["start"],0)-K["BTC"][0]["start"])/86400000
    print(f"{nm:34s} | 2%-risk: ${e1[0]:7.2f} ({e1[1]:4d} trades, win {e1[2]/max(e1[1],1)*100:2.0f}%, maxDD {e1[3]:5.1f}%, low ${e1[4]:.2f}) | $50 flat: ${e2[0]:7.2f} ({e2[1]:4d} trades, maxDD {e2[3]:5.1f}%)")
if __name__=="__main__":
    S2=[x for x in Bsig if x[3]["kind"]=="S2"]; S7=[x for x in Bsig if x[3]["kind"]=="S7"]
    AG=[(a,b,c,dict(s,target_p=s["tp_adj"] or s["target_p"])) for a,b,c,s in Bsig if s["agent"]!="REJECT"]
    print(f"Start $10, 120 days of 15m data (OKX), 14bps costs, entry next-bar open, stop-first")
    line("Model B S2 (no agents)",S2); line("Model B S7 (no agents)",S7); line("Model B all (no agents)",Bsig)
    line("Model B WITH agents (live setup)",AG)
    line("Championship all (TP2)",Csig); line("Championship BULL",[x for x in Csig if x[3]["regime"]=="BULL"])
    line("Championship BEAR",[x for x in Csig if x[3]["regime"]=="BEAR"])
    line("Championship if TP1 only",[(a,b,c,dict(s,target_p=s["tp1"])) for a,b,c,s in Csig])

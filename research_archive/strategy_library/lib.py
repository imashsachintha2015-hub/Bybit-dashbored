"""Strategy library: ~40 widely-taught strategies with textbook parameters fixed in advance (no tuning).
Each returns (long_signal, short_signal) boolean arrays evaluated at bar close; entry = next bar open."""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as SW
def pad(a,k,fill=np.nan): return np.concatenate([np.full(k-1,fill),a])
def ema(x,n):
    a=2/(n+1); o=np.empty_like(x); o[0]=x[0]
    for i in range(1,len(x)): o[i]=o[i-1]+a*(x[i]-o[i-1])
    return o
def rma(x,n):
    o=np.empty_like(x); o[0]=x[0]
    for i in range(1,len(x)): o[i]=o[i-1]+(x[i]-o[i-1])/n
    return o
def sma(x,n): return pad(SW(x,n).mean(1),n)
def rstd(x,n): return pad(SW(x,n).std(1),n)
def rmax(x,n): return pad(SW(x,n).max(1),n)
def rmin(x,n): return pad(SW(x,n).min(1),n)
def shift(x,k=1): return np.concatenate([np.full(k,np.nan),x[:-k]])
def cross_up(a,b): return (a>b)&(shift(a)<=shift(b))
def cross_dn(a,b): return (a<b)&(shift(a)>=shift(b))
def rsi(c,n):
    d=np.diff(c,prepend=c[0]); g=rma(np.maximum(d,0),n); l=rma(np.maximum(-d,0),n); return 100-100/(1+g/np.maximum(l,1e-12))
def indicators(t,o,h,l,c,v):
    I=dict(t=t,o=o,h=h,l=l,c=c,v=v)
    pc=shift(c); pc[0]=c[0]; tr=np.maximum(h-l,np.maximum(abs(h-pc),abs(l-pc))); I["atr"]=rma(tr,14)
    for n in (9,20,21,50,200): I[f"e{n}"]=ema(c,n)
    I["rsi14"]=rsi(c,14); I["rsi2"]=rsi(c,2)
    m=ema(c,12)-ema(c,26); s=ema(m,9); I["macd"]=m; I["macds"]=s; I["hist"]=m-s
    mid=sma(c,20); sd=rstd(c,20); I["bbu"]=mid+2*sd; I["bbl"]=mid-2*sd; I["bbm"]=mid; I["bw"]=(4*sd)/np.maximum(mid,1e-12)
    I["kcu"]=I["e20"]+2*I["atr"]; I["kcl"]=I["e20"]-2*I["atr"]
    # ADX
    up=h-shift(h); dn=shift(l)-l; up[0]=dn[0]=0
    pdm=np.where((up>dn)&(up>0),up,0.0); mdm=np.where((dn>up)&(dn>0),dn,0.0); atr_=rma(tr,14)
    pdi=100*rma(pdm,14)/np.maximum(atr_,1e-12); mdi=100*rma(mdm,14)/np.maximum(atr_,1e-12)
    dx=100*abs(pdi-mdi)/np.maximum(pdi+mdi,1e-12); I["adx"]=rma(dx,14); I["pdi"]=pdi; I["mdi"]=mdi
    # stochastic, williams, cci, mfi
    hh=rmax(h,14); ll=rmin(l,14); k=100*(c-ll)/np.maximum(hh-ll,1e-12); I["stk"]=sma(np.nan_to_num(k,nan=50),3); I["std"]=sma(I["stk"],3); I["wr"]=k-100
    tp=(h+l+c)/3; sm=sma(tp,20); md=pad(np.abs(SW(tp,20)-SW(tp,20).mean(1)[:,None]).mean(1),20); I["cci"]=(tp-sm)/np.maximum(0.015*md,1e-12)
    rmf=tp*v; pos=np.where(tp>shift(tp),rmf,0.0); neg=np.where(tp<shift(tp),rmf,0.0)
    I["mfi"]=100-100/(1+pad(SW(pos,14).sum(1),14)/np.maximum(pad(SW(neg,14).sum(1),14),1e-12))
    obv=np.cumsum(np.sign(np.diff(c,prepend=c[0]))*v); I["obv"]=obv
    # supertrend(10,3)
    a10=rma(tr,10); hl2=(h+l)/2; ub=hl2+3*a10; lb=hl2-3*a10; fu=ub.copy(); fl=lb.copy(); dirn=np.ones(len(c))
    for i in range(1,len(c)):
        fu[i]=ub[i] if (ub[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]
        fl[i]=lb[i] if (lb[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        dirn[i]=1 if c[i]>fu[i-1] else (-1 if c[i]<fl[i-1] else dirn[i-1])
    I["st"]=dirn
    # parabolic SAR (0.02, 0.2)
    sar=np.empty_like(c); bull=True; af=0.02; ep=h[0]; sar[0]=l[0]
    for i in range(1,len(c)):
        s_=sar[i-1]+af*(ep-sar[i-1])
        if bull:
            s_=min(s_,l[i-1],l[i-2] if i>1 else l[i-1])
            if l[i]<s_: bull=False; s_=ep; ep=l[i]; af=0.02
            elif h[i]>ep: ep=h[i]; af=min(af+0.02,0.2)
        else:
            s_=max(s_,h[i-1],h[i-2] if i>1 else h[i-1])
            if h[i]>s_: bull=True; s_=ep; ep=h[i]; af=0.02
            elif l[i]<ep: ep=l[i]; af=min(af+0.02,0.2)
        sar[i]=s_; I.setdefault("sarb",np.zeros(len(c),bool))[i]=bull
    # heikin ashi
    hc=(o+h+l+c)/4; ho=np.empty_like(c); ho[0]=(o[0]+c[0])/2
    for i in range(1,len(c)): ho[i]=(ho[i-1]+hc[i-1])/2
    I["hag"]=hc>ho
    # ichimoku
    tk=(rmax(h,9)+rmin(l,9))/2; kj=(rmax(h,26)+rmin(l,26))/2; sa=shift((tk+kj)/2,26); sb=shift((rmax(h,52)+rmin(l,52))/2,26)
    I["tk"]=tk; I["kj"]=kj; I["ctop"]=np.fmax(sa,sb); I["cbot"]=np.fmin(sa,sb)
    # session VWAP (UTC day) and its std
    day=(t//86400000).astype(np.int64); pv=tp*v; vw=np.empty_like(c); vs=np.empty_like(c); cp=cv=cq=0.0; dprev=-1
    for i in range(len(c)):
        if day[i]!=dprev: cp=cv=cq=0.0; dprev=day[i]
        cp+=pv[i]; cv+=v[i]; cq+=tp[i]**2*v[i]; vw[i]=cp/max(cv,1e-12); vs[i]=np.sqrt(max(cq/max(cv,1e-12)-vw[i]**2,0))
    I["vwap"]=vw; I["vwsd"]=vs; I["hour"]=((t//3600000)%24).astype(int); I["minute"]=((t//60000)%60).astype(int); I["day"]=day
    I["vavg"]=sma(v,20)
    return I
def swing_lows(l,k=3):
    p=np.zeros(len(l),bool); p[k:-k]=np.all([l[k:-k]<l[k-j:len(l)-k-j] for j in range(1,k+1)]+[l[k:-k]<l[k+j:len(l)-k+j] for j in range(1,k+1)],axis=0); return p
def session_range(I,h0,h1):
    """range high/low of bars with hour in [h0,h1) of the current UTC day; known after h1"""
    h,l,hour,day=I["h"],I["l"],I["hour"],I["day"]; n=len(h); RH=np.full(n,np.nan); RL=np.full(n,np.nan); cd=-1; hi=-np.inf; lo=np.inf; done=False
    for i in range(n):
        if day[i]!=cd: cd=day[i]; hi=-np.inf; lo=np.inf; done=False
        if h0<=hour[i]<h1: hi=max(hi,h[i]); lo=min(lo,l[i])
        elif hour[i]>=h1 and hi>-np.inf: RH[i]=hi; RL[i]=lo
    return RH,RL
def strategies(I):
    o,h,l,c,v=I["o"],I["h"],I["l"],I["c"],I["v"]; S={}
    up200=c>I["e200"]; dn200=c<I["e200"]; rng=np.maximum(h-l,1e-12); body=abs(c-o)
    # ---- trend following ----
    S["T01 EMA9/21 cross"]=(cross_up(I["e9"],I["e21"]),cross_dn(I["e9"],I["e21"]))
    S["T02 EMA20/50 cross + EMA200 filter"]=(cross_up(I["e20"],I["e50"])&up200,cross_dn(I["e20"],I["e50"])&dn200)
    S["T03 MACD cross (above/below zero)"]=(cross_up(I["macd"],I["macds"])&(I["macd"]>0),cross_dn(I["macd"],I["macds"])&(I["macd"]<0))
    S["T04 MACD cross + EMA200"]=(cross_up(I["macd"],I["macds"])&up200,cross_dn(I["macd"],I["macds"])&dn200)
    st=I["st"]; S["T05 Supertrend(10,3) flip"]=((st==1)&(shift(st)==-1),(st==-1)&(shift(st)==1))
    S["T06 Supertrend flip + EMA200"]=(S["T05 Supertrend(10,3) flip"][0]&up200,S["T05 Supertrend(10,3) flip"][1]&dn200)
    sb=I["sarb"]; S["T07 Parabolic SAR flip + EMA200"]=(sb&~shift(sb.astype(float)).astype(bool)&up200,~sb&shift(sb.astype(float)).astype(bool)&dn200)
    S["T08 ADX>25 DI cross"]=(cross_up(I["pdi"],I["mdi"])&(I["adx"]>25),cross_dn(I["pdi"],I["mdi"])&(I["adx"]>25))
    S["T09 Ichimoku TK cross vs cloud"]=(cross_up(I["tk"],I["kj"])&(c>I["ctop"]),cross_dn(I["tk"],I["kj"])&(c<I["cbot"]))
    hg=I["hag"]; S["T10 Heikin-Ashi flip + EMA50 trend"]=(hg&~shift(hg.astype(float)).astype(bool)&(c>I["e50"]),~hg&shift(hg.astype(float)).astype(bool)&(c<I["e50"]))
    S["T11 EMA20 pullback in trend"]=((I["e20"]>I["e50"])&(I["e50"]>I["e200"])&(l<=I["e20"])&(c>I["e20"])&(c>o),(I["e20"]<I["e50"])&(I["e50"]<I["e200"])&(h>=I["e20"])&(c<I["e20"])&(c<o))
    # ---- breakout ----
    S["B01 Donchian 20 breakout"]=(c>shift(rmax(h,20)),c<shift(rmin(l,20)))
    S["B02 Donchian 55 breakout (turtle)"]=(c>shift(rmax(h,55)),c<shift(rmin(l,55)))
    sq=I["bw"]<=rmin(I["bw"],120)*1.1; sqp=shift(sq.astype(float),1)==1
    S["B03 Bollinger squeeze breakout"]=(sqp&(c>I["bbu"]),sqp&(c<I["bbl"]))
    S["B04 Keltner breakout"]=(cross_up(c,I["kcu"]),cross_dn(c,I["kcl"]))
    ib=(shift(h)<shift(h,2))&(shift(l)>shift(l,2))
    S["B05 Inside-bar breakout"]=(ib&(c>shift(h)),ib&(c<shift(l)))
    r7=rng<=rmin(rng,7); nr=shift(r7.astype(float))==1
    S["B06 NR7 breakout"]=(nr&(c>shift(h)),nr&(c<shift(l)))
    RH,RL=session_range(I,0,7); lon=(I["hour"]>=7)&(I["hour"]<11)
    S["B07 Asia range breakout at London"]=(lon&cross_up(c,RH),lon&cross_dn(c,RL))
    RH2,RL2=session_range(I,13,14); ny=(I["hour"]>=14)&(I["hour"]<18)
    S["B08 NY opening-range breakout"]=(ny&cross_up(c,RH2),ny&cross_dn(c,RL2))
    # ---- mean reversion ----
    S["M01 RSI2<10 above EMA200 (Connors)"]=((I["rsi2"]<10)&up200,(I["rsi2"]>90)&dn200)
    S["M02 RSI14 30/70 cross back"]=(cross_up(I["rsi14"],np.full(len(c),30.0)),cross_dn(I["rsi14"],np.full(len(c),70.0)))
    S["M03 Bollinger re-entry"]=((shift(c)<shift(I["bbl"]))&(c>I["bbl"]),(shift(c)>shift(I["bbu"]))&(c<I["bbu"]))
    S["M04 Stochastic cross in trend"]=(cross_up(I["stk"],I["std"])&(I["stk"]<20)&up200,cross_dn(I["stk"],I["std"])&(I["stk"]>80)&dn200)
    S["M05 VWAP 2-sigma fade"]=((c<I["vwap"]-2*I["vwsd"])&(c>o),(c>I["vwap"]+2*I["vwsd"])&(c<o))
    z=(c-sma(c,50))/np.maximum(rstd(c,50),1e-12)
    S["M06 Z-score(50) >2 fade"]=(cross_up(z,np.full(len(c),-2.0)),cross_dn(z,np.full(len(c),2.0)))
    S["M07 Williams %R reversal"]=(cross_up(I["wr"],np.full(len(c),-80.0)),cross_dn(I["wr"],np.full(len(c),-20.0)))
    S["M08 CCI +-100 re-entry"]=(cross_up(I["cci"],np.full(len(c),-100.0)),cross_dn(I["cci"],np.full(len(c),100.0)))
    S["M09 MFI 20/80"]=(cross_up(I["mfi"],np.full(len(c),20.0)),cross_dn(I["mfi"],np.full(len(c),80.0)))
    # ---- price action ----
    low20=l<=rmin(l,20); high20=h>=rmax(h,20)
    pinb=((np.minimum(o,c)-l)/rng>=0.6)&(body/rng<=0.3); pins=((h-np.maximum(o,c))/rng>=0.6)&(body/rng<=0.3)
    S["P01 Pin bar at 20-bar extreme"]=(pinb&low20,pins&high20)
    eb=(shift(c)<shift(o))&(c>o)&(c>=shift(o))&(o<=shift(c)); es=(shift(c)>shift(o))&(c<o)&(c<=shift(o))&(o>=shift(c))
    S["P02 Engulfing at 20-bar extreme"]=(eb&(rmin(l,3)<=rmin(l,20)),es&(rmax(h,3)>=rmax(h,20)))
    tws=(c>o)&(shift(c)>shift(o))&(shift(c,2)>shift(o,2))&(c>shift(c))&(shift(c)>shift(c,2))
    tbc=(c<o)&(shift(c)<shift(o))&(shift(c,2)<shift(o,2))&(c<shift(c))&(shift(c)<shift(c,2))
    S["P03 Three soldiers/crows"]=(tws,tbc)
    ms=(shift(c,2)<shift(o,2))&(abs(shift(c)-shift(o))<=0.3*shift(rng))&(c>o)&(c>(shift(o,2)+shift(c,2))/2)
    es_=(shift(c,2)>shift(o,2))&(abs(shift(c)-shift(o))<=0.3*shift(rng))&(c<o)&(c<(shift(o,2)+shift(c,2))/2)
    S["P04 Morning/evening star"]=(ms,es_)
    S["P05 Turtle soup (false break of 20-low)"]=((l<shift(rmin(l,20)))&(c>shift(rmin(l,20))),(h>shift(rmax(h,20)))&(c<shift(rmax(h,20))))
    fr_h=np.zeros(len(c),bool); fr_l=np.zeros(len(c),bool)
    fr_h[2:-2]=(h[2:-2]>h[1:-3])&(h[2:-2]>h[:-4])&(h[2:-2]>h[3:-1])&(h[2:-2]>h[4:]); fr_l[2:-2]=(l[2:-2]<l[1:-3])&(l[2:-2]<l[:-4])&(l[2:-2]<l[3:-1])&(l[2:-2]<l[4:])
    lfh=np.full(len(c),np.nan); lfl=np.full(len(c),np.nan); a=b=np.nan
    for i in range(len(c)):
        if i>=2 and fr_h[i-2]: a=h[i-2]
        if i>=2 and fr_l[i-2]: b=l[i-2]
        lfh[i]=a; lfl[i]=b
    S["P06 Fractal breakout + EMA50"]=(cross_up(c,lfh)&(c>I["e50"]),cross_dn(c,lfl)&(c<I["e50"]))
    # double bottom/top: two swing lows within 0.3 ATR, 10-60 bars apart, then close above the neckline
    sl=swing_lows(l,3); sh=swing_lows(-h,3); dbl=np.zeros(len(c),bool); dtp=np.zeros(len(c),bool)
    idx_l=np.where(sl)[0]; idx_h=np.where(sh)[0]
    for a_,b_ in zip(idx_l[:-1],idx_l[1:]):
        if 10<=b_-a_<=60 and abs(l[a_]-l[b_])<=0.3*I["atr"][b_]:
            neck=h[a_:b_+1].max()
            for i in range(b_+3,min(len(c),b_+30)):
                if c[i]>neck: dbl[i]=True; break
                if l[i]<min(l[a_],l[b_]): break
    for a_,b_ in zip(idx_h[:-1],idx_h[1:]):
        if 10<=b_-a_<=60 and abs(h[a_]-h[b_])<=0.3*I["atr"][b_]:
            neck=l[a_:b_+1].min()
            for i in range(b_+3,min(len(c),b_+30)):
                if c[i]<neck: dtp[i]=True; break
                if h[i]>max(h[a_],h[b_]): break
    S["P07 Double bottom/top neckline break"]=(dbl,dtp)
    # ---- volume / flow ----
    vs=v>=2*shift(I["vavg"]); S["V01 Volume spike + strong close"]=(vs&(c>o)&((c-l)/rng>=0.75),vs&(c<o)&((h-c)/rng>=0.75))
    S["V02 OBV 20-high with price below high"]=((I["obv"]>=rmax(I["obv"],20))&(c<shift(rmax(h,20)))&(c>I["e50"]),(I["obv"]<=rmin(I["obv"],20))&(c>shift(rmin(l,20)))&(c<I["e50"]))
    S["V03 VWAP reclaim in trend"]=(cross_up(c,I["vwap"])&up200,cross_dn(c,I["vwap"])&dn200)
    # ---- divergence ----
    ll20=l<=rmin(l,20); rl=I["rsi14"]; prev_ll=shift(rmin(l,20),10); prev_rsi=shift(rmin(rl,20),10)
    S["D01 RSI divergence at 20-bar extreme"]=(ll20&(rl>prev_rsi+5)&(l<prev_ll)&(c>o),(h>=rmax(h,20))&(rl<shift(rmax(rl,20),10)-5)&(h>shift(rmax(h,20),10))&(c<o))
    hi_=I["hist"]; S["D02 MACD-hist divergence"]=(ll20&(hi_>shift(rmin(hi_,20),10))&(hi_<0)&(hi_>shift(hi_)),(h>=rmax(h,20))&(hi_<shift(rmax(hi_,20),10))&(hi_>0)&(hi_<shift(hi_)))
    # ---- session / time ----
    asia_dir=np.sign(c-np.where(np.isnan(RL),c,(RH+RL)/2))
    S["S01 London continuation of Asia drift"]=((I["hour"]==8)&(I["minute"]==0)&(asia_dir>0)&(c>RH),(I["hour"]==8)&(I["minute"]==0)&(asia_dir<0)&(c<RL))
    d_open=np.where(I["hour"]==0,o,np.nan)
    last_open=np.empty_like(c); cur=c[0]
    for i in range(len(c)):
        if I["hour"][i]==0 and I["minute"][i]==0: cur=o[i]
        last_open[i]=cur
    S["S02 NY reversal of London move"]=((I["hour"]==14)&(I["minute"]==0)&(c<last_open*0.99),(I["hour"]==14)&(I["minute"]==0)&(c>last_open*1.01))
    return S

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as SW
def _pad(a,k): return np.concatenate([np.full(k-1,np.nan),a])
def sg_weights(W,deg,m):
    """causal Savitzky-Golay: derivative of order m at the LAST point of a W-window (t=-(W-1)..0)"""
    t=np.arange(-(W-1),1,dtype=float); V=np.vander(t,deg+1,increasing=True); P=np.linalg.pinv(V)
    import math; return P[m]*math.factorial(m)
def compute(d,atr,cl,vrel):
    t,o,h,l,c,v=[d[:,i] for i in range(6)]; n=len(c); F={}
    prev=np.concatenate([[c[0]],c[:-1]]); dc=c-prev
    # ---- calculus: derivatives of a local polynomial fit (velocity / acceleration / jerk) ----
    for W,deg,m,nm in ((9,3,1,"vel9"),(9,3,2,"acc9"),(13,4,3,"jerk13"),(21,3,1,"vel21"),(21,3,2,"acc21")):
        w=sg_weights(W,deg,m); F[nm]=_pad(np.correlate(c,w,mode="valid"),W)/atr
    F["vel_over_acc"]=np.clip(F["vel9"]/(np.abs(F["acc9"])+0.02),-20,20)   # time-to-turn proxy
    # ---- integrals: area between price and VWAP, cumulative impulse ----
    tp=(h+l+c)/3
    for k in (48,96):
        vw=_pad(np.sum(SW(tp*v,k),axis=1)/np.maximum(np.sum(SW(v,k),axis=1),1e-12),k); F[f"vwap{k}"]=(c-vw)/atr
    vw96=_pad(np.sum(SW(tp*v,96),axis=1)/np.maximum(np.sum(SW(v,96),axis=1),1e-12),96)
    dev=(c-vw96)/atr
    for k in (16,48): F[f"int_dev{k}"]=_pad(np.sum(SW(dev,k),axis=1),k)/k
    imp=(dc/atr)*vrel
    for k in (8,32): F[f"impulse{k}"]=_pad(np.sum(SW(imp,k),axis=1),k)
    # ---- physics: kinetic energy, work, Kyle's lambda (price impact) ----
    ke=0.5*vrel*(dc/atr)**2
    for k in (4,16): F[f"KE{k}"]=_pad(np.mean(SW(ke,k),axis=1),k)
    flow=vrel*(2*cl-1); K=48
    Sf=SW(flow,K); Sd=SW(dc/atr,K); mf=Sf.mean(1); md=Sd.mean(1)
    cov=((Sf-mf[:,None])*(Sd-md[:,None])).mean(1); var=Sf.var(1)+1e-12; lam=cov/var
    F["kyle_lambda"]=_pad(lam,K); F["impact_resid"]=(dc/atr)-F["kyle_lambda"]*flow
    F["amihud"]=np.abs(dc/atr)/np.maximum(vrel,1e-3)
    # ---- stochastic process: Ornstein-Uhlenbeck fit on last 96 closes ----
    Wn=96; S=SW(c,Wn+1); X=S[:,:-1]; Y=S[:,1:]
    mx=X.mean(1); my=Y.mean(1); vx=((X-mx[:,None])**2).mean(1)+1e-18; b=((X-mx[:,None])*(Y-my[:,None])).mean(1)/vx
    b=np.clip(b,0.001,0.99999); a=my-b*mx; mu=a/(1-b); res=Y-(a[:,None]+b[:,None]*X); sig=res.std(1)/np.sqrt(1-b**2)+1e-18
    F["ou_z"]=_pad((c[Wn:]-mu)/sig,Wn+1); F["ou_kappa"]=_pad(1-b,Wn+1); F["ou_loghl"]=_pad(np.log(np.log(2)/np.maximum(-np.log(b),1e-4)),Wn+1)
    F["ou_pull"]=F["ou_z"]*F["ou_kappa"]     # expected reversion force  -k(p-mu)
    # variance ratio (Lo-MacKinlay): >0 trending, <0 mean reverting
    r1=dc; W2=96
    v1=np.var(SW(r1,W2),axis=1); r4=np.concatenate([np.full(4,np.nan),c[4:]-c[:-4]]); v4=np.nanvar(SW(r4,W2),axis=1)
    F["vr4"]=_pad(v4/(4*np.maximum(v1,1e-18))-1,W2)
    # ---- Hawkes self-excitation: exponentially decaying intensity of shocks ----
    z=dc/np.maximum(atr,1e-12); shock=(np.abs(z)>2.0).astype(float); sgn=np.sign(z)*shock
    for tau,nm in ((8,"hawkes8"),(48,"hawkes48")):
        al=np.exp(-1/tau); lam_=np.zeros(n); sl=np.zeros(n)
        for i in range(1,n): lam_[i]=al*lam_[i-1]+shock[i]; sl[i]=al*sl[i-1]+sgn[i]
        F[nm]=lam_; F[nm+"_dir"]=sl
    # ---- information theory: permutation entropy (order 3), spectral entropy ----
    a_,b_,c_=c[:-2],c[1:-1],c[2:]
    pat=(a_<b_).astype(int)*4+(b_<c_).astype(int)*2+(a_<c_).astype(int); pat=np.concatenate([[0,0],pat])
    Kp=48; ent=np.full(n,np.nan)
    cs=np.zeros((n+1,8))
    for j in range(8): cs[1:,j]=np.cumsum(pat==j)
    cnt=(cs[Kp:]-cs[:-Kp])/Kp; p=np.clip(cnt,1e-12,1); ent[Kp-1:]=-(cnt*np.log(p)).sum(1)/np.log(6)
    F["perm_ent"]=ent
    # ---- spectral: dominant cycle period / phase from FFT of detrended 64-bar window ----
    L=64; Sw=SW(c,L); tt=np.arange(L); tt=tt-tt.mean()
    Sxy=(Sw*tt).sum(1)/ (tt**2).sum(); Sd_=Sw-Sw.mean(1)[:,None]-Sxy[:,None]*tt
    Fz=np.fft.rfft(Sd_*np.hanning(L),axis=1); pw=np.abs(Fz)**2; pw[:,:2]=0
    k=pw.argmax(1); tot=pw.sum(1)+1e-18
    F["cyc_period"]=_pad(L/np.maximum(k,1),L); F["cyc_dom"]=_pad(pw[np.arange(len(k)),k]/tot,L)
    ph=np.angle(Fz[np.arange(len(k)),k]); F["cyc_sin"]=_pad(np.sin(ph),L); F["cyc_cos"]=_pad(np.cos(ph),L)
    q=pw/tot[:,None]; F["spec_ent"]=_pad(-(q*np.log(np.clip(q,1e-12,1))).sum(1)/np.log(pw.shape[1]),L)
    # ---- Kalman local-linear-trend filter: slope SNR and innovation ----
    lp=np.log(c); ap=np.maximum(atr/c,1e-6)
    lev=lp[0]; slp=0.0; P11=P12=P22=1e-4; inn=np.zeros(n); sl_=np.zeros(n)
    for i in range(1,n):
        Rn=(0.5*ap[i])**2; q1=(0.2*ap[i])**2; q2=(0.02*ap[i])**2
        pl=lev+slp; a11=P11+2*P12+P22+q1; a12=P12+P22; a22=P22+q2
        S_=a11+Rn; e=lp[i]-pl; K1=a11/S_; K2=a12/S_
        lev=pl+K1*e; slp=slp+K2*e; P11=(1-K1)*a11; P12=(1-K1)*a12; P22=a22-K2*a12
        inn[i]=e/np.sqrt(S_); sl_[i]=slp/ap[i]
    F["kal_innov"]=inn; F["kal_slope"]=sl_
    # ---- extreme value: Hill tail index on |returns| (last 200, top 20) ----
    lr=np.abs(np.concatenate([[0],np.diff(lp)]))+1e-12; Wh=200; Sh=SW(lr,Wh); top=np.partition(Sh,Wh-20,axis=1)[:,Wh-20:]
    top.sort(axis=1); F["hill"]=_pad(1/np.maximum(np.log(top[:,1:]/top[:,[0]]).mean(1),1e-6),Wh)
    # ---- fractal dimension (Katz) over 32 bars ----
    Wk=32; Sk=SW(c,Wk); Ls=np.abs(np.diff(Sk,axis=1)).sum(1)+1e-12; dd=np.abs(Sk-Sk[:,[0]]).max(1)+1e-12
    F["katz_fd"]=_pad(np.log10(Wk-1)/(np.log10(Wk-1)+np.log10(dd/Ls)),Wk)
    # ---- volume "potential well": gradient / curvature of ln(volume density) at price; distance to POC ----
    Wv=96; nb=24; grad=np.full(n,np.nan); curv=np.full(n,np.nan); dpoc=np.full(n,np.nan); wid=np.full(n,np.nan)
    ker=np.array([0.25,0.5,0.25])
    for i in range(Wv,n):
        lo=l[i-Wv:i].min(); hi=h[i-Wv:i].max()
        if hi<=lo: continue
        hist,_=np.histogram(tp[i-Wv:i],bins=nb,range=(lo,hi),weights=v[i-Wv:i]); f=np.convolve(hist,ker,mode="same")+1e-9; lf=np.log(f)
        bw=(hi-lo)/nb; bi=int(np.clip((c[i]-lo)/bw,1,nb-2))
        grad[i]=(lf[bi+1]-lf[bi-1])/2; curv[i]=lf[bi+1]+lf[bi-1]-2*lf[bi]; dpoc[i]=(lo+(np.argmax(f)+0.5)*bw-c[i])/atr[i]; wid[i]=(hi-lo)/atr[i]
    F["well_grad"]=grad; F["well_curv"]=curv; F["poc_dist"]=dpoc; F["prof_width"]=wid
    return F

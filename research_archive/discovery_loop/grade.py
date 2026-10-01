import pickle, math, random, sys, collections
rows=sorted(pickle.load(open("gradedata.pkl","rb")),key=lambda r:r["t"])
NUM=["rp","disc","dvol","sweep","comp","fvg","wait","room","drift","btc","btcatr","atrp","gap","prevol","liq"]
def mean(v): return sum(v)/len(v) if v else 0
def welch(a,b):
    if len(a)<8 or len(b)<8: return 0
    ma,mb=mean(a),mean(b); va=sum((x-ma)**2 for x in a)/(len(a)-1); vb=sum((x-mb)**2 for x in b)/(len(b)-1)
    se=math.sqrt(va/len(a)+vb/len(b)); return (ma-mb)/se if se>0 else 0
def cuts(vals):
    s=sorted(vals); return s[len(s)//3],s[2*len(s)//3]
def bucket(x,c): return 0 if x<=c[0] else (1 if x<=c[1] else 2)
def build(dev,Rkey="R",tmin=2.0,maxf=4):
    """learn: for each numeric feature pick best/worst tercile if extreme buckets differ significantly AND in both chronological halves of dev"""
    h=len(dev)//2; sel=[]
    for f in NUM:
        c=cuts([r[f] for r in dev]); B=[[r[Rkey] for r in dev if bucket(r[f],c)==k] for k in range(3)]
        m=[mean(b) for b in B]; hi=max(range(3),key=lambda k:m[k]); lo=min(range(3),key=lambda k:m[k])
        t=welch(B[hi],B[lo])
        if abs(t)<tmin: continue
        # consistency across chronological halves
        ok=True
        for part in (dev[:h],dev[h:]):
            a=[r[Rkey] for r in part if bucket(r[f],c)==hi]; b=[r[Rkey] for r in part if bucket(r[f],c)==lo]
            if len(a)<5 or len(b)<5 or mean(a)<=mean(b): ok=False
        if ok: sel.append((abs(t),f,c,hi,lo))
    sel.sort(reverse=True); return sel[:maxf]
def score(r,sel): return sum((1 if bucket(r[f],c)==hi else -1 if bucket(r[f],c)==lo else 0) for _,f,c,hi,lo in sel)
def grade(s): return "A" if s>=2 else ("B" if s==1 else ("C" if s==0 else "D"))
def evaluate(test,sel,label):
    print(f" {label}: base {len(test)} trades avg {mean([r['R'] for r in test]):+.2f}R total {sum(r['R'] for r in test):+.1f}R")
    for g in "ABCD":
        v=[r["R"] for r in test if grade(score(r,sel))==g]
        if v: print(f"    grade {g}: {len(v):4d} trades  avg {mean(v):+.2f}R  win {sum(x>0 for x in v)/len(v)*100:3.0f}%  total {sum(v):+.1f}R")
    kept=[r["R"] for r in test if grade(score(r,sel))!="D"]
    print(f"    drop D  : {len(kept):4d} trades  avg {mean(kept):+.2f}R  total {sum(kept):+.1f}R")
    return mean(kept)-mean([r['R'] for r in test])
def run(fit,test,label):
    sel=build(fit)
    print(f"\n== {label}: fit {len(fit)} trades / test {len(test)} trades ==")
    print(" selected features (t, feature, cuts, best bucket, worst bucket) [0=low,1=mid,2=high tercile]:")
    for t,f,c,hi,lo in sel: print(f"    {f:7s} t={t:.1f} cuts=({c[0]:.3g},{c[1]:.3g}) best={hi} worst={lo}")
    if not sel: print("    none passed the significance + consistency filters"); return None
    evaluate(fit,sel,"FIT (in-sample)"); lift=evaluate(test,sel,"TEST (out-of-sample)")
    return sel,lift
n=len(rows); k=int(n*0.6)
res=run(rows[:k],rows[k:],"forward: fit first 60% -> test last 40%")
res2=run(rows[n-k:],rows[:n-k],"reverse: fit last 60% -> test first 40%")
# permutation null for the forward split
def null_lift(fit,test,perms=300):
    rnd=random.Random(5); lifts=[]; base=mean([r["R"] for r in test])
    for _ in range(perms):
        Rp=[r["R"] for r in fit]; rnd.shuffle(Rp)
        fit2=[dict(r,R=x) for r,x in zip(fit,Rp)]
        sel=build(fit2)
        if not sel: lifts.append(0.0); continue
        kept=[r["R"] for r in test if grade(score(r,sel))!="D"]
        lifts.append(mean(kept)-base if kept else 0.0)
    return lifts
for lbl,fit,test,rs in (("forward",rows[:k],rows[k:],res),("reverse",rows[n-k:],rows[:n-k],res2)):
    L=null_lift(fit,test); real=rs[1] if rs else 0.0
    p=sum(1 for x in L if x>=real)/len(L)
    nsel=sum(1 for x in L if x!=0.0)
    print(f"\n[{lbl}] lift from dropping grade D: real {real:+.3f}R | null (shuffled labels) mean {mean(L):+.3f}, 95th pct {sorted(L)[int(.95*len(L))]:+.3f}; p={p:.3f}; null runs that selected any feature: {nsel}/{len(L)}")
pickle.dump((res,res2),open("grade_res.pkl","wb"))

import sys, io, contextlib, pickle, json
with contextlib.redirect_stdout(io.StringIO()):
    import port
from port import *
import disc
rows=[]
for t,s,r,sd,xt,rp in T:
    f=disc.FEAT.get((s,t))
    if f: rows.append(dict(t=t,sym=s,R=r,exit=xt,**f))
pickle.dump(rows,open("gradedata.pkl","wb")); print(len(rows),"trades with features; missing",len(T)-len(rows))
import collections; print(collections.Counter(r["side"] for r in rows))

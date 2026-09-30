import pickle, io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    import grade
    from port import portfolio
rows,(fw,rv)=grade.rows,pickle.load(open("grade_res.pkl","rb"))
n=len(rows); k=int(n*.6)
def tr(rs): return [(r["t"],r["sym"],r["R"],r["side"],r["exit"],r["rp"]/100) for r in rs]
for lbl,test,sel in (("forward TEST (last 40%)",rows[k:],fw[0]),("reverse TEST (first 40%)",rows[:n-k],rv[0])):
    print(lbl)
    for name,fil in (("no grading (all trades)",lambda r:True),("drop grade D",lambda r:grade.grade(grade.score(r,sel))!="D"),("only grade A/B",lambda r:grade.grade(grade.score(r,sel)) in "AB")):
        e,nn,sk,w,dd=portfolio(tr([r for r in test if fil(r)]),risk=0.02)
        print(f"   {name:26s} $10 -> ${e:8.2f} | trades taken {nn:3d} (skipped {sk:3d}) win {w/max(nn,1)*100:3.0f}% maxDD {dd:5.1f}%")

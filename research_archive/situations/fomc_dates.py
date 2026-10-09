"""FOMC decision dates 2020-2026 from the Federal Reserve's calendar pages (downloaded to dl_fomc/, read as data only).
Decision day = last day of the meeting; cancelled meetings and notation votes are skipped. Output: fomc_dates.json ["YYYY-MM-DD", ...]"""
import re, sys, json, os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dl_fomc")
MON = {m: i + 1 for i, m in enumerate("January February March April May June July August September October November December".split())}
AB = {k[:3]: v for k, v in MON.items()}
out = set()

cal = open(os.path.join(D, "cal.htm"), encoding="utf-8", errors="replace").read()
heads = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", cal)]
for k, (pos, yr) in enumerate(heads):
    end = heads[k + 1][0] if k + 1 < len(heads) else len(cal)
    block = cal[pos:end]
    months = re.findall(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong>', block)
    dates = re.findall(r'fomc-meeting__date[^>]*>([^<]+)<', block)
    for mo, da in zip(months, dates):
        if "notation" in da.lower() or "cancel" in da.lower(): continue
        mo = mo.strip(); last_mo = mo.split("/")[-1].strip()
        m_ = MON.get(last_mo) or AB.get(last_mo[:3])
        days = re.findall(r"\d+", da)
        if not m_ or not days: continue
        out.add(f"{yr}-{m_:02d}-{int(days[-1]):02d}")

h20 = open(os.path.join(D, "hist2020.htm"), encoding="utf-8", errors="replace").read()
for title in re.findall(r"<h5[^>]*>([^<]*Meeting - 2020)</h5>", h20):
    if "cancel" in title.lower(): continue
    m = re.match(r"\s*(\w+)\s+([\d\-]+)", title); mo = MON.get(m.group(1)); d = int(re.findall(r"\d+", m.group(2))[-1])
    out.add(f"2020-{mo:02d}-{d:02d}")

dates = sorted(x for x in out if "2020-01-01" <= x <= "2026-12-31")
json.dump(dates, open("fomc_dates.json", "w"))
for y in range(2020, 2027): print(y, [d[5:] for d in dates if d.startswith(str(y))])
print("total", len(dates))

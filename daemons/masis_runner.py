#!/usr/bin/env python3
"""MASIS strategy runner: 4h trend pullback + 4h EMA20 snap-back, with account-level risk limits.

  trend4h   places orders (Bybit demo endpoint unless MASIS_ALLOW_REAL_MONEY=1) on 42 coins:
            validated on Jan 2023 - Sep 2026 and on 22 coins the research never used.
  snapback  runs in PAPER mode by default: its edge did not carry over to unseen coins, so it
            collects forward evidence (tagged core20 / extra22) before it gets any real orders.
            RUNNER_SNAP_LIVE=1 gives it real orders on the 20 coins it was researched on.

Run        python daemons/masis_runner.py [--paper] [--once]
Control    --status | --report | --scan
           --halt [note] | --unhalt | --pause NAME | --resume NAME|all     (NAME: trend4h, snapback)
Switches   MASIS_PAPER=1 simulates everything (no orders); MASIS_HALT=1 stops new entries;
           MASIS_RUNNER=0 (in launcher.py) disables the runner.
           All limits and sizes are environment variables, see RUNNER.md and core.CFG.

Safety     every order carries its stop; one position per coin; caps on open risk, gross exposure,
           daily loss and drawdown; a failed stop closes the position; state survives restarts;
           time comes from Bybit, not this machine.
"""
import argparse
import json
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_env():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("\"'")
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass


_load_env()

from daemons.runner import core, scans, snapback, trading, trend4h  # noqa: E402

LOOP_SECONDS = 15
STATUS_FILE = "runner_status.json"


class Runtime:
    def __init__(self, paper=False, snap_live=None):
        self.log = core.Log()
        self.state = core.State()
        self.ex_paper = core.Exchange(True, self.state, self.log)
        self.ctx_paper = core.Ctx(self.ex_paper, self.state, self.log)
        self.ctx_live = None
        self.startup_error = ""
        if not paper:
            try:
                self.ctx_live = core.Ctx(core.Exchange(False, self.state, self.log), self.state, self.log)
            except Exception as e:           # no keys / not the demo endpoint: run on paper rather than crash-loop
                self.startup_error = f"live trading unavailable, running on paper: {e}"
                self.log("critical", what=self.startup_error)
                paper = True
        if paper:
            trend_ctx = snap_ctx = self.ctx_paper
        else:
            if snap_live is None:
                snap_live = core._truthy("RUNNER_SNAP_LIVE")
            trend_ctx, snap_ctx = self.ctx_live, (self.ctx_live if snap_live else self.ctx_paper)
        self.trend_on = core._truthy("RUNNER_ENABLE_TREND", "1")
        self.snap_on = core._truthy("RUNNER_ENABLE_SNAP", "1")
        self.trend = trend4h.Trend4h(trend_ctx)
        self.snap = snapback.Snapback(snap_ctx)
        self.modes = {"trend4h": trend_ctx.mode, "snapback": snap_ctx.mode}
        self.ctxs = []
        for c in (self.ctx_live, self.ctx_paper):          # live first; paper is always needed for the bookkeeping of paper trades
            if c is not None and (c is trend_ctx or c is snap_ctx or c is self.ctx_paper):
                self.ctxs.append(c)
        self.monitor = scans.Monitor(self.state, self.log, self.modes, self.trend.universe, self.snap.universe)
        self.stop = False
        self.started = core.now_ms()


def status_path():
    return os.path.join(core.CFG["data_dir"], STATUS_FILE)


def write_status(rt, now):
    st = rt.state
    accounts = {}
    for c in rt.ctxs:
        g = c.gov
        a = c.acct
        accounts[c.mode] = {"endpoint": c.ex.endpoint, "equity": round(g.equity, 2), "day_start_equity": round(a["day"]["start_equity"], 2),
                            "peak_equity": round(a["peak_equity"], 2), "halted": g.halted, "open_positions": len(c.pos),
                            "open_risk_usd": round(g.open_risk(), 2), "open_risk_pct": round(100 * g.open_risk() / g.equity, 2) if g.equity else None,
                            "account_ok": bool(g.snapshot and g.snapshot["ok"])}
    positions = []
    for c in rt.ctxs:
        for sym, p in c.pos.items():
            positions.append({"account": c.mode, "strategy": p["strategy"], "symbol": sym, "side": "LONG" if p["side"] == 1 else "SHORT",
                              "entry": p["entry"], "stop": p["stop"], "target": p.get("target"), "qty": p["qty"], "risk_usd": round(p["risk_usd"], 2),
                              "age_h": round((now - p["opened"]) / 3_600_000, 1), "rules": p.get("rules"), "group": p.get("group")})
    untracked = []
    if rt.ctx_live is not None and rt.ctx_live.gov.snapshot and rt.ctx_live.gov.snapshot["ok"]:
        untracked = sorted(s for s in rt.ctx_live.gov.snapshot["positions"] if s not in rt.ctx_live.pos)
    out = {"version": core.VERSION, "startup_error": rt.startup_error, "server_time": core.iso(now), "started": core.iso(rt.started), "loop_ts": now, "modes": rt.modes,
           "strategies_enabled": {"trend4h": rt.trend_on, "snapback": rt.snap_on},
           "universe": {"trend4h": len(rt.trend.universe), "snapback": len(rt.snap.universe)},
           "risk": {"trend_pct": core.CFG["risk_trend"] * 100, "snap_pct": core.CFG["risk_snap"] * 100,
                    "max_open_total_pct": core.CFG["max_open_total"] * 100, "daily_loss_halt_pct": core.CFG["day_loss_halt"] * 100,
                    "drawdown_halt_pct": core.CFG["dd_halt"] * 100},
           "accounts": accounts, "paused": st["paused"], "positions": positions, "untracked_positions": untracked,
           "recent_signals": st["signals"][-20:], "recent_exits": st["history"][-20:],
           "stats": {k: scans.agg_stats(v) for k, v in st["agg"].items()}, "scan": st["scan"]}
    path = status_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, default=str)
    os.replace(tmp, path)
    return out


def cycle(rt):
    now = core.now_ms()
    core.apply_control(rt.state, rt.log)
    for c in rt.ctxs:
        c.ex.paper_step()
        c.gov.refresh()
        trading.reconcile(c)
        if c.mode == "live":
            for sym in sorted(c.gov.snapshot["positions"] if c.gov.snapshot["ok"] else []):
                if sym not in c.pos:
                    rt.log.once(f"orphan:{sym}", "untracked_position", symbol=sym, note="open in the account but not opened by the runner; left alone")
    if rt.snap_on:
        rt.snap.manage(now)
        due, boundary = rt.snap.due(now)
        if due:
            rt.snap.process(now, boundary)
    if rt.trend_on:
        due, boundary = rt.trend.due(now)
        if due:
            rt.trend.process(now, boundary)
    rt.monitor.tick(now)
    write_status(rt, now)


def run(rt, once=False):
    rt.log("start", mode=rt.modes, endpoint={c.mode: c.ex.endpoint for c in rt.ctxs}, trend_coins=len(rt.trend.universe),
           snap_coins=len(rt.snap.universe), risk_trend_pct=core.CFG["risk_trend"] * 100, risk_snap_pct=core.CFG["risk_snap"] * 100,
           strategies={"trend4h": rt.trend_on, "snapback": rt.snap_on}, snap_rules=snapback.rule_mode(),
           managed={c.mode: sorted(c.pos) for c in rt.ctxs})
    with rt.state.lock:
        rt.state["started"] = rt.started
    signal.signal(signal.SIGTERM, lambda *a: setattr(rt, "stop", True))
    try:
        signal.signal(signal.SIGINT, lambda *a: setattr(rt, "stop", True))
    except Exception:
        pass
    while not rt.stop:
        try:
            cycle(rt)
        except Exception as e:
            rt.log("error", what=f"cycle: {type(e).__name__}", msg=str(e))
        if once:
            break
        for _ in range(LOOP_SECONDS):
            if rt.stop:
                break
            time.sleep(1)
    rt.monitor.stop()
    rt.state.save()
    rt.log("stop")


# ─── command-line controls ───────────────────────────────────────────────────
def cmd_status():
    p = status_path()
    if not os.path.exists(p):
        print("no status file yet (is the runner running on this machine?)")
        return
    s = json.load(open(p))
    age = (core.now_ms() - s["loop_ts"]) / 1000
    print(f"{s['version']}  last loop {age:.0f}s ago  (server time {s['server_time']}, started {s['started']})")
    print(f"modes: {s['modes']}   strategies enabled: {s['strategies_enabled']}   coins: {s['universe']}")
    for name, a in s["accounts"].items():
        print(f"  {name:5s} account: equity {a['equity']}  day start {a['day_start_equity']}  peak {a['peak_equity']}  open risk {a['open_risk_pct']}%  "
              f"positions {a['open_positions']}  {'HALTED: ' + a['halted'] if a['halted'] else 'ok'}")
    if s["paused"]:
        print("  PAUSED:", {k: v["reason"] for k, v in s["paused"].items()})
    for p in s["positions"]:
        print(f"    {p['account']:5s} {p['strategy']:8s} {p['symbol']:13s} {p['side']:5s} entry {p['entry']} stop {p['stop']} target {p['target']} age {p['age_h']}h risk ${p['risk_usd']}")
    if s["untracked_positions"]:
        print("  untracked positions in the account:", s["untracked_positions"])
    for k, v in sorted(s["stats"].items()):
        if v.get("n"):
            print(f"  {k:28s} n={v['n']:4d} win {v['win']:.0%} avg {v['mean_R']:+.3f}R total {v['total_R']:+.1f}R")
    d = s["scan"].get("drift")
    if d and not d.get("failed"):
        print("  last history replay:", {g: f"n={v.get('n')} win {v.get('win')} avg {v.get('mean_R')}R" for g, v in d.get("snapback", {}).items()}, "| trend:",
              {g: f"n={v.get('n')} avg {v.get('mean_R')}R" for g, v in d.get("trend4h", {}).items()})
        for w in d.get("warnings", []):
            print("  WARNING:", w)


def cmd_report():
    st = core.State()
    print(f"{core.VERSION} lifetime results (R net of fees)  | research expectation: trend4h win ~34% avg +0.10R, snapback win ~60% avg +0.14R (original coins)")
    for k, a in sorted(st["agg"].items()):
        s = scans.agg_stats(a)
        print(f"  {k:30s} n={s['n']:4d} win {s['win']:.0%} (90% CI {s['win_lo']:.0%}-{s['win_hi']:.0%}) avg {s['mean_R']:+.3f}R ±{s['se']:.3f} total {s['total_R']:+.1f}R")
    if not st["agg"]:
        print("  no closed trades yet")
    for h in st["history"][-10:]:
        print(f"    {core.iso(h['closed'])} {h['strategy']:8s} {h['sym']:13s} {'L' if h['side'] == 1 else 'S'} {h['how']:16s} R_net {h['R_net']} ({'paper' if h['paper'] else 'live'}, {h.get('group')})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper", action="store_true", help="simulate both strategies; place no orders")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--scan", action="store_true", help="run the history replay now and print it")
    ap.add_argument("--halt", nargs="?", const="manual", metavar="NOTE")
    ap.add_argument("--unhalt", action="store_true")
    ap.add_argument("--pause", metavar="NAME")
    ap.add_argument("--resume", metavar="NAME|all")
    a = ap.parse_args()
    if a.status:
        return cmd_status()
    if a.report:
        return cmd_report()
    if a.halt is not None:
        core.set_control(halt=True, note=a.halt)
        print("halt switch ON: no new entries (open positions keep their stops and keep being managed)")
        return
    if a.unhalt:
        core.set_control(halt=False, note="")
        print("halt switch OFF")
        return
    if a.pause:
        ctl = core.get_control()
        core.set_control(pause={**(ctl.get("pause") or {}), a.pause: True})
        print(f"{a.pause} paused")
        return
    if a.resume:
        core.control_resume(a.resume)
        print(f"resume requested for {a.resume}")
        return
    if a.scan:
        log = core.Log(quiet=True)
        r = scans.drift_scan(log, trend4h.coins(), snapback.coins(False))
        print(json.dumps(r, indent=1))
        return
    rt = Runtime(paper=a.paper or core._truthy("MASIS_PAPER"))
    run(rt, once=a.once)


if __name__ == "__main__":
    main()

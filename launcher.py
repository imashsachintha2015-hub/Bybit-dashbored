"""
MASIS — Unified Production Launcher for Railway.

Processes:
  - Strategy Runner (daemons/masis_runner.py): 4h trend pullback (demo orders) and the 4h EMA20
    snap-back (paper by default), with account-level risk limits and self-monitoring.
  - Forward Lab (daemons/forward_lab.py): record-only research, no orders.  MASIS_FORWARD_LAB=0 switches it off.
  - WVBR forward test (daemons/forward_wvbr.py): paper only, no orders.  MASIS_WVBR_FORWARD=0 switches it off.
  - HTTP server (server.py): dashboard, API, runner status at /api/runner/status.

The previous CME-X5 / Championship engine is OFF.  Set MASIS_LEGACY_ENGINE=1 to run it again (it is record-only
unless CME_X5_RECORD_ONLY=0), and MASIS_RUNNER=0 to switch the new runner off.  A crashed daemon is restarted with a growing delay.
"""
import os
import signal
import subprocess
import sys
import threading
import time


def _on(name, default):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def main():
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("\"'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

    port = os.environ.get("PORT", "8080" if (os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PROJECT_ID")) else "8070")
    os.environ["PORT"] = port

    daemon_cmds = []
    if _on("MASIS_RUNNER", "1"):
        daemon_cmds.append(("Strategy Runner", [sys.executable, "daemons/masis_runner.py"]))
    if _on("MASIS_FORWARD_LAB", "1"):
        # Record-only research (AMD-FVG 1H forward test + order-flow recorder): places no orders, ever.
        daemon_cmds.append(("Forward Lab", [sys.executable, "daemons/forward_lab.py"]))
    if _on("MASIS_WVBR_FORWARD", "1"):
        # Blind PAPER forward test of the weekly value breakout retest + BTC gate (and an SBGZ gate log): reads public prices, places no orders, ever.
        daemon_cmds.append(("WVBR Forward Test (paper)", [sys.executable, "daemons/forward_wvbr.py"]))
    if _on("MASIS_LEGACY_ENGINE", "0"):
        daemon_cmds.append(("Legacy CME-X5 / Championship Engine", [sys.executable, "daemons/cme_x5_pure_engine.py"]))

    print("=" * 70)
    print(f"  MASIS — STRATEGY RUNNER PLATFORM  (PORT: {port})")
    print("  Strategies: 4h trend pullback (orders) + 4h EMA20 snap-back (paper by default)")
    print(f"  Daemons: {', '.join(n for n, _ in daemon_cmds) or 'none (MASIS_RUNNER=0)'}")
    print("=" * 70)

    procs = {}
    stopping = threading.Event()

    def supervise(name, cmd):
        delay = 5
        while not stopping.is_set():
            started = time.time()
            try:
                print(f"[DAEMON] Spawning {name} ({' '.join(cmd)})...", flush=True)
                p = subprocess.Popen(cmd)
                procs[name] = p
                print(f"  -> {name} PID: {p.pid}", flush=True)
                code = p.wait()
            except Exception as e:
                code = f"spawn failed: {e}"
            if stopping.is_set():
                return
            delay = 5 if time.time() - started > 300 else min(delay * 2, 300)
            print(f"[DAEMON] {name} exited ({code}); restarting in {delay}s", flush=True)
            stopping.wait(delay)

    for name, cmd in daemon_cmds:
        threading.Thread(target=supervise, args=(name, cmd), name=f"supervise-{name}", daemon=True).start()

    def cleanup(sig=None, frame=None):
        print("\n[SHUTDOWN] Terminating daemons...")
        stopping.set()
        for name, p in list(procs.items()):
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"\n[WEB] Starting Unified HTTP Server on port {port}...")
    from server import run_server
    run_server()


if __name__ == "__main__":
    main()

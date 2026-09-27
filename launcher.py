"""
Unified Production Platform Launcher for Railway.
CME-X5 Pure Profitable Engine -- only positive-EV systems run.
Eliminated: continuous_market_scanner, deepseek_gatekeeper,
            historical_pattern_archaeologist, cme_x4_live_research,
            cme_x4_shadow_engine (all negative or zero EV per 10k-trade audit).
"""
import os
import sys
import subprocess
import signal


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

    port = os.environ.get("PORT", "8070")
    os.environ["PORT"] = port
    print("=" * 70)
    print(f"  CME-X5 24/7 AUTONOMOUS PLATFORM & EXECUTOR  (PORT: {port})")
    print("  S2 POC Reclaim +0.683R  |  AMD FVG  |  S7 Gated +0.34R")
    print("  Zero Browser Tab Dependency -- Full Cloud Headless Execution")
    print("=" * 70)

    # Background daemons -- only proven positive-EV processes
    daemon_cmds = [
        # PRIMARY: CME-X5 Pure Engine & 24/7 Bybit Autonomous Executor
        ("CME-X5 Pure Engine & 24H Executor", [sys.executable, "daemons/cme_x5_pure_engine.py"]),
        # Lightweight forensic audit trail
        ("Decision Trace",                    [sys.executable, "daemons/trade_decision_trace_daemon.py"]),
    ]

    import shutil
    if shutil.which("node") and os.path.exists("server/live-engine.js"):
        daemon_cmds.append(
            ("Node Live Engine", ["node", "--max-old-space-size=192", "server/live-engine.js"])
        )

    procs = []
    for name, cmd in daemon_cmds:
        try:
            joined = " ".join(cmd)
            print(f"[DAEMON] Spawning {name} ({joined})...")
            p = subprocess.Popen(cmd)
            procs.append((name, p))
            print(f"  -> {name} PID: {p.pid}")
        except Exception as e:
            print(f"[WARN] Failed to spawn {name}: {e}")

    def cleanup(sig=None, frame=None):
        print("\n[SHUTDOWN] Terminating child background daemons...")
        for name, p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"\n[WEB] Starting Unified HTTP Server on port {port}...")
    from server import run_server
    run_server()


if __name__ == "__main__":
    main()

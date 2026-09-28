"""
CME-X5 Model B — Unified Production Launcher for Railway.
Single daemon: Model B Engine with intelligent agent layer.

Eliminated: continuous_market_scanner, deepseek_gatekeeper,
            historical_pattern_archaeologist, cme_x4 engines,
            smart_growth_executor, trade_decision_trace,
            Node live-engine, and all JS-side agents/swarm.
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
    print(f"  CME-X5 MODEL B — FINAL AUTONOMOUS PLATFORM  (PORT: {port})")
    print("  Agent Layer: S/R(40%) + POC(30%) + FVG(30%) = Confluence Scorer")
    print("  Zero Browser Tab Dependency — Full Cloud Headless Execution")
    print("=" * 70)

    # Autonomous daemons: Model B Engine & Championship Dual-Regime System
    daemon_cmds = [
        ("CME-X5 Model B Engine", [sys.executable, "daemons/cme_x5_pure_engine.py"]),
        ("Championship Dual-Regime Daemon", [sys.executable, "daemons/championship_mode_daemon.py"]),
    ]

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
        print("\n[SHUTDOWN] Terminating Model B engine...")
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


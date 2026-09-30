"""
MASIS — Unified Production Launcher for Railway.
Single daemon process with runtime-selectable strategies:
  • CME-X5 Model B Pure
  • Championship Dual-Regime

The dashboard's shared strategy state controls which strategy the daemon
executes; there is no second competing Championship daemon.
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

    port = os.environ.get("PORT", "8080" if (os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PROJECT_ID")) else "8070")
    os.environ["PORT"] = port
    print("=" * 70)
    print(f"  MASIS — UNIFIED AUTONOMOUS PLATFORM  (PORT: {port})")
    print("  Runtime strategy: CME-X5 Pure <-> Championship Dual-Regime")
    print("  Zero Browser Tab Dependency — Full Cloud Headless Execution")
    print("=" * 70)

    # One authoritative daemon. The engine itself selects the active strategy
    # from shared auto_trade_state and can switch at runtime.
    daemon_cmds = [
        ("Unified Strategy Engine", [sys.executable, "daemons/cme_x5_pure_engine.py"]),
        # Record-only research: AMD-FVG 1H forward test + order-flow recorder (no orders).
        ("Forward Lab", [sys.executable, "daemons/forward_lab.py"]),
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
        print("\n[SHUTDOWN] Terminating unified strategy engine...")
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


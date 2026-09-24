"""
Unified Production Platform Launcher for Railway.
Boots 24/7 background trading daemons and runs the HTTP Server Gateway on PORT.
"""
import os
import sys
import subprocess
import time
import signal

def main():
    port = os.environ.get("PORT", "8080")
    print("=" * 70)
    print(f"  LAUNCHING MASIS V3 UNIFIED PLATFORM ON RAILWAY (PORT: {port})")
    print("=" * 70)

    # 1. Background Daemons
    daemon_cmds = [
        ("Market Scanner", [sys.executable, "daemons/continuous_market_scanner.py"]),
        ("Smart Growth Executor", [sys.executable, "daemons/smart_growth_executor.py"]),
        ("Decision Trace", [sys.executable, "daemons/trade_decision_trace_daemon.py"]),
        ("Pattern Archaeologist", [sys.executable, "daemons/historical_pattern_archaeologist.py"]),
        # Research-only: collects public Bybit Demo market state and forward
        # barrier outcomes. It never places, modifies, or cancels orders.
        ("CME-X4 Live Research", [sys.executable, "daemons/cme_x4_live_research.py"]),
        ("CME-X4 V4 Shadow Engine", [sys.executable, "daemons/cme_x4_shadow_engine.py"]),
    ]

    import shutil
    if shutil.which("node") and os.path.exists("server/live-engine.js"):
        daemon_cmds.append(("Node Live Engine", ["node", "--max-old-space-size=192", "server/live-engine.js"]))

    procs = []
    for name, cmd in daemon_cmds:
        try:
            print(f"[DAEMON] Spawning {name} ({' '.join(cmd)})...")
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

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"\n[WEB] Starting Unified HTTP Server on port {port}...")
    # Run server.py in the main thread
    from server import run_server
    run_server()

if __name__ == "__main__":
    main()

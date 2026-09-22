#!/bin/bash
set -e

echo "========================================================================"
echo "  LAUNCHING MASIS V3 UNIFIED PLATFORM ON RAILWAY"
echo "  PORT: ${PORT:-8080}"
echo "========================================================================"

# 1. Launch 24/7 Continuous Market Scanner Daemon in background
echo "[DAEMON] Launching Continuous Market Scanner (Scalps + HTF Swings)..."
python daemons/continuous_market_scanner.py &
SCANNER_PID=$!
echo "  Continuous Market Scanner PID: $SCANNER_PID"

# 2. Launch 24/7 Smart Growth Executor Daemon in background
echo "[DAEMON] Launching Smart Growth Execution Engine & Profit Claimer..."
python daemons/smart_growth_executor.py &
EXECUTOR_PID=$!
echo "  Smart Growth Executor PID: $EXECUTOR_PID"

# 3. Launch live decision trace observer
echo "[DAEMON] Launching Live Trade Decision Trace..."
python daemons/trade_decision_trace_daemon.py &
TRACE_PID=$!
echo "  Trade Decision Trace PID: $TRACE_PID"

# 4. Launch 24/7 Historical Archaeologist Daemon in background (runs every 30m)
echo "[DAEMON] Launching Historical Archaeologist Background Learner..."
python daemons/historical_pattern_archaeologist.py &
ARCHAEOLOGIST_PID=$!
echo "  Historical Archaeologist PID: $ARCHAEOLOGIST_PID"

# 5. Launch Node.js Live WebSocket Engine in background if node is present
NODE_PID=""
if command -v node >/dev/null 2>&1 && [ -f "server/live-engine.js" ]; then
    echo "[DAEMON] Launching Node.js Live WebSocket Engine..."
    node --max-old-space-size=192 server/live-engine.js &
    NODE_PID=$!
    echo "  Live Engine PID: $NODE_PID"
fi

# Trap shutdown signals to terminate child background processes cleanly
trap "echo 'Terminating daemons...'; kill -TERM $SCANNER_PID $EXECUTOR_PID $TRACE_PID $ARCHAEOLOGIST_PID $NODE_PID 2>/dev/null || true; exit 0" SIGINT SIGTERM

# 6. Launch Unified HTTP Server & API Gateway in foreground
echo "[WEB] Starting Unified Dashboard & API Gateway on port ${PORT:-8080}..."
exec python server.py

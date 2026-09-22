#!/bin/bash
set -e

echo "========================================================================"
echo "  LAUNCHING MASIS V3 UNIFIED PLATFORM ON RAILWAY"
echo "  PORT: ${PORT:-8080}"
echo "========================================================================"

# 1. Run initial Historical Archaeology scan to warm super-trade knowledge
echo "[INIT] Priming Historical Market Archaeologist knowledge base..."
python daemons/historical_pattern_archaeologist.py || echo "[WARN] Archaeology initial warm-up non-fatal notice"

# 2. Launch 24/7 Continuous Market Scanner Daemon in background
echo "[DAEMON] Launching Continuous Market Scanner (Scalps + HTF Swings)..."
python daemons/continuous_market_scanner.py &
SCANNER_PID=$!
echo "  Continuous Market Scanner PID: $SCANNER_PID"

# 3. Launch 24/7 Smart Growth Executor Daemon in background
echo "[DAEMON] Launching Smart Growth Execution Engine & Profit Claimer..."
python daemons/smart_growth_executor.py &
EXECUTOR_PID=$!
echo "  Smart Growth Executor PID: $EXECUTOR_PID"

# 4. Launch live decision trace observer. This does NOT change trading logic;
#    it correlates scanner/executor logs so every candidate has a trace ID and
#    explicit Supervisor/Risk/Execution status. Missing gates are reported as
#    NOT_IN_EXECUTION_PATH rather than being falsely marked as passed.
echo "[DAEMON] Launching Live Trade Decision Trace..."
python daemons/trade_decision_trace_daemon.py &
TRACE_PID=$!
echo "  Trade Decision Trace PID: $TRACE_PID"

# 5. Launch 24/7 Historical Archaeologist Daemon in background (runs every 30m)
echo "[DAEMON] Launching Historical Archaeologist Background Learner..."
python -c "from daemons.historical_pattern_archaeologist import run_archaeologist_daemon; run_archaeologist_daemon()" &
ARCHAEOLOGIST_PID=$!
echo "  Historical Archaeologist PID: $ARCHAEOLOGIST_PID"

# Trap shutdown signals to terminate child background processes cleanly
trap "echo 'Terminating daemons...'; kill -TERM $SCANNER_PID $EXECUTOR_PID $TRACE_PID $ARCHAEOLOGIST_PID 2>/dev/null || true; exit 0" SIGINT SIGTERM

# 6. Launch Unified HTTP Server & API Gateway in foreground
echo "[WEB] Starting Unified Dashboard & API Gateway on port ${PORT:-8080}..."
exec python server.py

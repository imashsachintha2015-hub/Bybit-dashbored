#!/usr/bin/env python3
"""Live decision-trace daemon.

Observes the existing scanner/executor logs and builds a persistent trace for
 each candidate without changing trading decisions. It deliberately reports
 Archeologist/Red-Team as NOT_IN_EXECUTION_PATH when the current executor does
 not call those gates, rather than pretending they approved a trade.
"""
import json
import os
import re
import time
import hashlib
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCANNER_LOG = os.path.join(ROOT, "scratch", "scanner_live.log")
EXECUTOR_LOG = os.path.join(ROOT, "scratch", "smart_executor.log")
TRACE_FILE = os.path.join(ROOT, "scratch", "trade_decision_traces.jsonl")
STATE_FILE = os.path.join(ROOT, "scratch", "trade_decision_trace_state.json")

traces = {}
log_offsets = {SCANNER_LOG: 0, EXECUTOR_LOG: 0}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def trace_id(symbol, direction, target=""):
    bucket = str(round(time.time() / 30))
    raw = f"{symbol}|{direction}|{target}|{bucket}"
    return f"HTF-{symbol}-{direction}-{hashlib.sha1(raw.encode()).hexdigest()[:8].upper()}"


def emit(t):
    with open(TRACE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"[TRACE {t['traceId']}] {t['stage'].upper()} -> {t['status']} | {t.get('reason','')}", flush=True)


def save_state():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"offsets": log_offsets, "traces": traces}, f)
    os.replace(tmp, STATE_FILE)


def load_state():
    global log_offsets, traces
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            log_offsets.update({k: int(v) for k, v in data.get("offsets", {}).items()})
            traces.update(data.get("traces", {}))
    except Exception:
        pass


def new_trace(symbol, direction, target, raw):
    tid = trace_id(symbol, direction, target)
    if tid in traces:
        return traces[tid]
    t = {
        "traceId": tid,
        "symbol": symbol,
        "direction": direction,
        "strategy": "HTF_SWING",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "stages": {},
        "source": raw,
    }
    traces[tid] = t
    emit({"traceId": tid, "stage": "candidate", "status": "PASS", "reason": raw})
    # These are intentionally explicit. Current smart_growth_executor.py does
    # not call either gate per candidate, so never fabricate PASS results.
    for stage in ("archaeologist", "redTeam"):
        t["stages"][stage] = {"status": "NOT_IN_EXECUTION_PATH"}
        emit({"traceId": tid, "stage": stage, "status": "NOT_IN_EXECUTION_PATH",
              "reason": "Current live executor does not invoke this gate for the candidate."})
    t["updatedAt"] = now_iso()
    return t


def update(t, stage, status, reason="", **details):
    t["stages"][stage] = {"status": status, "reason": reason, **details, "at": now_iso()}
    t["updatedAt"] = now_iso()
    emit({"traceId": t["traceId"], "stage": stage, "status": status, "reason": reason, **details})


def read_new(path):
    try:
        size = os.path.getsize(path)
        if size < log_offsets.get(path, 0):
            log_offsets[path] = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(log_offsets.get(path, 0))
            data = f.read()
            log_offsets[path] = f.tell()
        return data.splitlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[TRACE] read error {path}: {e}", flush=True)
        return []


def find_candidate(symbol, direction):
    candidates = [t for t in traces.values() if t.get("symbol") == symbol and t.get("direction") == direction]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.get("updatedAt", ""))


def process_scanner(line):
    m = re.search(r"HTF SWING CANDIDATE\s+([A-Z0-9]+)\s+(BUY|SELL):\s+([0-9.]+)%", line, re.I)
    if not m:
        return
    sym, direction, target = m.group(1).upper(), m.group(2).upper(), m.group(3)
    t = new_trace(sym, direction, target, line)
    t["targetPct"] = float(target)
    t["mode"] = (re.search(r"mode=([A-Z_]+)", line) or [None, "UNKNOWN"])[1]
    t["updatedAt"] = now_iso()


def process_executor(line):
    # DeepSeek gatekeeper is the actual per-candidate approval gate in the
    # current executor. Treat it as supervisor/decision gate in the trace.
    m = re.search(r"(?:DEEPSEEK GATEKEEPER VETO)\]\s+([A-Z0-9]+)\s+(BUY|SELL).*Score:\s*([0-9.]+)/100", line)
    if m:
        sym, direction, score = m.group(1).upper(), m.group(2).upper(), float(m.group(3))
        t = find_candidate(sym, direction)
        if t:
            update(t, "supervisor", "VETO", "DeepSeek pre-trade gatekeeper rejected candidate.", score=score)
            update(t, "risk", "SKIPPED", "Supervisor vetoed before execution risk gate.")
            update(t, "execution", "BLOCKED", "Supervisor veto.")
        return

    m = re.search(r"(?:DEEPSEEK APPROVED)\]\s+([A-Z0-9]+)\s+(BUY|SELL).*Score:\s*([0-9.]+)/100", line)
    if m:
        sym, direction, score = m.group(1).upper(), m.group(2).upper(), float(m.group(3))
        t = find_candidate(sym, direction)
        if t:
            update(t, "supervisor", "CONFIRM", "DeepSeek pre-trade gatekeeper approved candidate.", score=score)
        return

    m = re.search(r"\[ENTER TRADE \(([^)]+)\)\].*?:\s+([A-Z0-9]+)\s+(BUY|SELL)", line)
    if m:
        mode, sym, direction = m.group(1), m.group(2).upper(), m.group(3).upper()
        t = find_candidate(sym, direction)
        if t:
            update(t, "risk", "PASS", "Candidate reached order construction after existing cooldown/BTC/score checks.", mode=mode)
        return

    m = re.search(r"Order Placed Successfully! OrderId:\s*(\S+)", line)
    if m:
        # Associate with the most recently updated trace awaiting execution.
        pending = [t for t in traces.values() if t.get("stages", {}).get("risk", {}).get("status") == "PASS"]
        if pending:
            t = max(pending, key=lambda x: x.get("updatedAt", ""))
            update(t, "execution", "SUBMITTED", "Bybit order accepted by client.", orderId=m.group(1))
        return

    m = re.search(r"Order Failed:\s*(.*)$", line)
    if m:
        pending = [t for t in traces.values() if t.get("stages", {}).get("risk", {}).get("status") == "PASS"]
        if pending:
            t = max(pending, key=lambda x: x.get("updatedAt", ""))
            update(t, "execution", "BLOCKED", "Broker rejected order: " + m.group(1).strip())


def main():
    load_state()
    print("[TRACE DAEMON] Live trade decision tracing online.", flush=True)
    print("[TRACE DAEMON] Archaeologist/Red Team are reported honestly as NOT_IN_EXECUTION_PATH when not invoked.", flush=True)
    while True:
        for path, processor in ((SCANNER_LOG, process_scanner), (EXECUTOR_LOG, process_executor)):
            for line in read_new(path):
                processor(line)
        save_state()
        time.sleep(2)


if __name__ == "__main__":
    main()

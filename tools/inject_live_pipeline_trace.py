"""Build-time patch for app.js: make the HTF candidate -> supervisor -> risk ->
execution path explicitly visible in the Live Data Analysis log.

The repository keeps app.js large and actively edited, so this is deliberately
an assertion-based patch rather than a second copy of app.js. Railway applies
it to the freshly copied source during Docker build. If an expected anchor
moves, the build fails instead of silently shipping an incomplete trace.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.js"
MARKER = "HTF_PIPELINE_TRACE_PATCH_V1"

text = APP.read_text(encoding="utf-8")
if MARKER in text:
    print("[HTF TRACE] app.js already patched")
    raise SystemExit(0)

# 1) Add a tiny helper immediately before the supervisor function.
anchor = "  async function maybeConsultSupervisor(sym, s) {\n"
helper = '''  // HTF_PIPELINE_TRACE_PATCH_V1\n  // Keep HTF candidates auditable in Live Data Analysis. This is observability\n  // only; it does not change the supervisor/risk decision itself.\n  function logHtfPipeline(sym, direction, stage, status, reason = '') {\n    const suffix = reason ? ` — ${reason}` : '';\n    logEvent(`[HTF PIPELINE] ${sym} ${direction} | ${stage} -> ${status}${suffix}`);\n  }\n\n'''
if anchor not in text:
    raise SystemExit("Missing supervisor function anchor")
text = text.replace(anchor, helper + anchor, 1)

# 2) Mark the candidate's supervisor routing decision, including candidates
# that never qualify for a supervisor call.
old = """      logEvent(`HTF SWING CANDIDATE ${sym} ${candidate.direction}: ${movePct.toFixed(2)}% target | mode=${currentStrategyMode.toUpperCase()} | routing through supervisor/risk`);\n\n      if (candidate.grade && candidate.grade !== 'C' && candidate.grade !== 'D' && candidate.stopLoss) {\n        maybeConsultSupervisor(sym, candidate);\n        // The supervisor is async; executeEntry on the next scan/tick will only\n        // submit after the exact candidate verdict is present.\n"""
new = """      logEvent(`HTF SWING CANDIDATE ${sym} ${candidate.direction}: ${movePct.toFixed(2)}% target | mode=${currentStrategyMode.toUpperCase()} | routing through supervisor/risk`);\n\n      if (candidate.grade && candidate.grade !== 'C' && candidate.grade !== 'D' && candidate.stopLoss) {\n        logHtfPipeline(sym, candidate.direction, 'SUPERVISOR', 'QUEUED', `grade=${candidate.grade} score=${candidate.score || '--'}`);\n        maybeConsultSupervisor(sym, candidate);\n        // The supervisor is async; executeEntry on the next scan/tick will only\n        // submit after the exact candidate verdict is present.\n"""
if old not in text:
    raise SystemExit("Missing HTF candidate routing anchor")
text = text.replace(old, new, 1)

# If the candidate is below the supervisor-call requirements, say so explicitly.
old = """        // submit after the exact candidate verdict is present.\n      }\n"""
new = """        // submit after the exact candidate verdict is present.\n      } else {\n        const why = !candidate.grade\n          ? 'missing grade'\n          : (candidate.grade === 'C' || candidate.grade === 'D')\n            ? `grade=${candidate.grade}`\n            : 'missing stopLoss';\n        logHtfPipeline(sym, candidate.direction, 'SUPERVISOR', 'SKIPPED', why);\n      }\n"""
if old not in text:
    raise SystemExit("Missing HTF candidate else anchor")
text = text.replace(old, new, 1)

# 3) Mark supervisor entry and all meaningful exit paths.
old = """  async function maybeConsultSupervisor(sym, s) {\n    const candidateKey = executionCandidateKey(sym, s);\n"""
new = """  async function maybeConsultSupervisor(sym, s) {\n    const candidateKey = executionCandidateKey(sym, s);\n    if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n      logHtfPipeline(sym, s.direction, 'SUPERVISOR', 'START', `candidate=${candidateKey}`);\n    }\n"""
if old not in text:
    raise SystemExit("Missing supervisor start anchor")
text = text.replace(old, new, 1)

old = """        engines[sym].setLlmVerdict(cached);\n        supervisorCompleted.set(candidateKey, cached);\n        return cached;\n"""
new = """        engines[sym].setLlmVerdict(cached);\n        supervisorCompleted.set(candidateKey, cached);\n        if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n          logHtfPipeline(sym, s.direction, 'SUPERVISOR', String(cached.verdict || 'CACHED').toUpperCase(), cached.rationale || 'cached supervisor verdict');\n        }\n        return cached;\n"""
if old not in text:
    raise SystemExit("Missing cached supervisor anchor")
text = text.replace(old, new, 1)

old = """        const verdict = Object.assign({}, res, {\n          symbol: sym, direction: s.direction, at: Date.now(), candidateKey\n        });\n"""
if old not in text:
    raise SystemExit("Missing supervisor verdict anchor")

old = """        logEvent(`Supervisor on ${sym} ${s.direction} ${s.setupType}: ${res.verdict}${res.rationale ? ' — ' + res.rationale : ''}${res.is_fallback ? ' (local fallback)' : ''}`);\n        renderSupervisorPanel(sym, res, { setup: s.setupType, direction: s.direction });\n"""
new = """        logEvent(`Supervisor on ${sym} ${s.direction} ${s.setupType}: ${res.verdict}${res.rationale ? ' — ' + res.rationale : ''}${res.is_fallback ? ' (local fallback)' : ''}`);\n        if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n          logHtfPipeline(sym, s.direction, 'SUPERVISOR', String(res.verdict || 'UNKNOWN').toUpperCase(), res.rationale || '');\n        }\n        renderSupervisorPanel(sym, res, { setup: s.setupType, direction: s.direction });\n"""
if old not in text:
    raise SystemExit("Missing supervisor final logging anchor")
text = text.replace(old, new, 1)

old = """      } catch (e) {\n        llmGovernor.failCall();\n        logEvent(`Supervisor call failed for ${sym}: ${e.message} — execution blocked until a valid supervisor verdict is available`);\n"""
new = """      } catch (e) {\n        llmGovernor.failCall();\n        if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n          logHtfPipeline(sym, s.direction, 'SUPERVISOR', 'ERROR', e.message);\n        }\n        logEvent(`Supervisor call failed for ${sym}: ${e.message} — execution blocked until a valid supervisor verdict is available`);\n"""
if old not in text:
    raise SystemExit("Missing supervisor error anchor")
text = text.replace(old, new, 1)

# 4) Make risk + execution stages explicit in executeEntry.
old = """    const gate = riskGovernor.canOpen({ symbol: sym, openPositions: openPositionsSnapshot });\n    if (!gate.allowed) {\n      logEvent(`${s.decision} ${sym} (${s.setupType}, grade ${s.grade}) not taken — ${gate.reasons[0]}`);\n"""
new = """    if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n      logHtfPipeline(sym, s.direction, 'RISK', 'CHECK', `candidate=${candidateKey}`);\n    }\n    const gate = riskGovernor.canOpen({ symbol: sym, openPositions: openPositionsSnapshot });\n    if (!gate.allowed) {\n      if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n        logHtfPipeline(sym, s.direction, 'RISK', 'BLOCK', gate.reasons[0] || 'risk governor blocked');\n      }\n      logEvent(`${s.decision} ${sym} (${s.setupType}, grade ${s.grade}) not taken — ${gate.reasons[0]}`);\n"""
if old not in text:
    raise SystemExit("Missing risk gate anchor")
text = text.replace(old, new, 1)

old = """    if (!sized.qty) {\n      logEvent(`${sym} entry skipped — ${sized.rejected}`);\n      return;\n    }\n"""
new = """    if (!sized.qty) {\n      if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n        logHtfPipeline(sym, s.direction, 'RISK', 'BLOCK', sized.rejected || 'position sizing rejected');\n      }\n      logEvent(`${sym} entry skipped — ${sized.rejected}`);\n      return;\n    }\n    if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n      logHtfPipeline(sym, s.direction, 'RISK', 'PASS', `qty=${sized.qty}`);\n      logHtfPipeline(sym, s.direction, 'EXECUTION', 'SUBMITTING');\n    }\n"""
if old not in text:
    raise SystemExit("Missing risk sizing anchor")
text = text.replace(old, new, 1)

old = """      if (res.retCode === 0 && res.result && res.result.orderId) {\n        pendingEntries[sym] = {\n"""
new = """      if (res.retCode === 0 && res.result && res.result.orderId) {\n        if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n          logHtfPipeline(sym, s.direction, 'EXECUTION', 'SUBMITTED', `orderId=${res.result.orderId}`);\n        }\n        pendingEntries[sym] = {\n"""
if old not in text:
    raise SystemExit("Missing execution success anchor")
text = text.replace(old, new, 1)

# 5) Make supervisor wait/veto visible for HTF candidates.
old = """        supervisorLogged.add(pendingKey);\n        logEvent(`EXECUTION WAIT ${sym} ${s.direction} ${s.setupType}: waiting for supervisor verdict for this exact candidate`);\n"""
new = """        supervisorLogged.add(pendingKey);\n        if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n          logHtfPipeline(sym, s.direction, 'SUPERVISOR', 'PENDING', 'execution waits for exact candidate verdict');\n        }\n        logEvent(`EXECUTION WAIT ${sym} ${s.direction} ${s.setupType}: waiting for supervisor verdict for this exact candidate`);\n"""
if old not in text:
    raise SystemExit("Missing supervisor wait anchor")
text = text.replace(old, new, 1)

old = """        supervisorLogged.add(vetoKey);\n        logEvent(`EXECUTION VETO ${sym} ${s.direction} ${s.setupType}: ${verdict.rationale || 'Supervisor vetoed this exact candidate'}`);\n"""
new = """        supervisorLogged.add(vetoKey);\n        if (s.htfSwing === true || s.strategy === 'HTF_SWING') {\n          logHtfPipeline(sym, s.direction, 'EXECUTION', 'BLOCKED', `supervisor VETO: ${verdict.rationale || 'no rationale'}`);\n        }\n        logEvent(`EXECUTION VETO ${sym} ${s.direction} ${s.setupType}: ${verdict.rationale || 'Supervisor vetoed this exact candidate'}`);\n"""
if old not in text:
    raise SystemExit("Missing supervisor veto anchor")
text = text.replace(old, new, 1)

APP.write_text(text, encoding="utf-8")
print("[HTF TRACE] app.js patched successfully")

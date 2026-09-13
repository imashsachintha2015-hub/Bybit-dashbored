#!/usr/bin/env node
/**
 * Walk-forward backtest for MASIS V3, with the reconstructed V2 logic run over
 * the identical bars, costs and fill rules for comparison.
 *
 * Design commitments, because a backtest is only worth reading if you know what
 * it refuses to do:
 *
 *  - NO LOOK-AHEAD. Decisions at bar i use only bars 0..i. Entry fills at bar
 *    i+1's OPEN, never at the close the decision was made on.
 *  - PESSIMISTIC INTRABAR ORDERING. Stop and target are checked on 1-minute
 *    bars. If a single 1-minute bar contains both the stop and a target, the
 *    stop is assumed to have been hit first. Real fills are sometimes kinder;
 *    assuming otherwise is how backtests lie.
 *  - REAL COSTS. Taker fee both sides plus slippage on every fill, including
 *    every partial scale-out.
 *  - NO SURVIVORSHIP CHOICE. Whatever symbols are in backtest/data are run.
 *
 * Usage: node backtest/run-backtest.js [--symbols BTCUSDT,ETHUSDT] [--equity 1000] [--json out.json]
 */
const fs = require('fs');
const path = require('path');

const { MasisEngine } = require('../masis-engine.js');
const { PositionManager } = require('../agents/position-manager.js');
const { RiskGovernor } = require('../agents/risk-governor.js');
const { FlowProxy } = require('./flow-proxy.js');
const { MetaLearner } = require('../swarm/meta-learner.js');
const baselineV2 = require('./baseline-v2.js');

const TAKER_FEE = 0.00055;   // Bybit linear perp taker fee
const MAKER_FEE = 0.00010;   // Bybit linear perp maker fee (VIP-3 tier)
const SLIPPAGE = 0.0002;     // 2 bps assumed on market fills

/**
 * Execution style.
 *
 *   'taker'  — market in, market out. What the system does today.
 *   'maker'  — resting limit entry at the setup price, limit take-profits.
 *              Stops stay market (a stop that waits for a fill is not a stop).
 *
 * This is not a detail. Every losing result in this repository was dominated by
 * the cost term, and the cost term is the only quantity in the whole system
 * under the operator's direct control. A taker round trip costs ~15 bps once
 * slippage is counted; a maker round trip costs ~2. Against a stop roughly 1%
 * wide, that is the difference between paying 15% of the risked amount in fees
 * on every trade and paying 2%.
 *
 * The cost of maker entries is that some trades never fill. That is modelled:
 * the limit only fills if price actually trades through it, and if it does not
 * within `MAKER_ENTRY_TIMEOUT_BARS` the signal is abandoned. Missed trades are
 * counted and reported, because a fill rate below 100% is the real price of
 * this and hiding it would make the comparison meaningless.
 */
let EXEC_STYLE = 'taker';
let STOP_MULT = 1.0;
let INVAL_MULT = 1.0;
let MAKER_ENTRY_TIMEOUT_BARS = 3;
/**
 * How far BETTER than the signal price the resting limit is placed, in bps.
 * Zero means joining the touch, which fills almost every time and is therefore
 * the most optimistic assumption available. Larger values mean waiting for a
 * better price: a lower fee AND a better entry, paid for by missing the trades
 * that never come back. The sweep below exists because this parameter, not the
 * signal, is what decides the result.
 */
let MAKER_OFFSET_BPS = 0;

const DATA_DIR = path.join(__dirname, 'data');

/** Aggregates a series into a coarser one (e.g. 15m -> 1h with factor 4). */
function resample(series, factor) {
  const out = [];
  for (let i = 0; i + factor <= series.length; i += factor) {
    const chunk = series.slice(i, i + factor);
    out.push({
      start: chunk[0].start,
      open: chunk[0].open,
      high: Math.max(...chunk.map(c => c.high)),
      low: Math.min(...chunk.map(c => c.low)),
      close: chunk[chunk.length - 1].close,
      volume: chunk.reduce((a, c) => a + c.volume, 0),
      confirm: true
    });
  }
  return out;
}

/**
 * Which timeframes the engine runs on.
 *  fast  — 5m structure under a 15m regime. More opportunities, tighter stops,
 *          and therefore a much larger share of each R eaten by fees.
 *  swing — 15m structure under a 1h regime (resampled from 15m). Fewer trades,
 *          wider stops, and proportionally far less cost drag.
 * The backtest runs whichever is asked for so the choice can be made on
 * evidence rather than preference.
 */
function selectTimeframes(bundle, mode) {
  const m1 = bundle.series['1'] || [];
  const m5 = bundle.series['5'] || [];
  const m15 = bundle.series['15'] || [];
  // Each series is capped at the same BAR COUNT by the fetcher, so the finer
  // ones cover far less calendar time: 12,000 one-minute bars is 8 days, while
  // 12,000 fifteen-minute bars is 126 days. The intrabar path therefore has to
  // be a LIST of candidates, finest first, and the walker must fall back when
  // the fine series does not reach back far enough. Using only the 1m series
  // (as the first version did) left stops unchecked intrabar across 93% of a
  // swing-mode run, which let losses run past 1R before the bar closed.
  if (mode === 'swing') {
    return { ltf: m5, mtf: m15, htf: resample(m15, 4), paths: [m1, m5], mtfMs: 15 * 60 * 1000, label: '15m structure / 1h regime' };
  }
  return { ltf: m1, mtf: m5, htf: m15, paths: [m1], mtfMs: 5 * 60 * 1000, label: '5m structure / 15m regime' };
}

function args() {
  const a = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    a[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  }
  return a;
}

function fillPrice(price, side, isEntry) {
  // Market orders pay the spread in the unfavourable direction either way.
  const adverse = (side === 'Buy') === isEntry ? 1 : -1;
  return price * (1 + adverse * SLIPPAGE);
}

function feeOn(notional, style) {
  const isMaker = (style || EXEC_STYLE) === 'maker';
  return notional * (isMaker ? MAKER_FEE : TAKER_FEE);
}

/**
 * Maps a timestamp to "how many bars of `series` have closed by then".
 * Binary search rather than a moving pointer: the caller needs to ask about
 * both the start and the end of a bar within the same iteration, and a
 * forward-only pointer silently returns the wrong answer for the earlier of
 * the two.
 */
function buildAligner(series) {
  const starts = series.map(c => c.start);
  return function upTo(ts) {
    let lo = 0, hi = starts.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] < ts) lo = mid + 1; else hi = mid;
    }
    return lo;
  };
}

// ────────────────────────────────────────────────────────────────────────
// V3 run
// ────────────────────────────────────────────────────────────────────────
function runV3(symbol, bundle, startEquity, mode, opts = {}) {
  const tf = selectTimeframes(bundle, mode);
  const m1 = tf.paths[0];
  const m5 = tf.mtf;
  const m15 = tf.htf;
  const mLtf = tf.ltf;
  const MTF_MS = tf.mtfMs;
  if (m5.length < 200 || m15.length < 100) {
    return { symbol, error: `not enough bars (mtf ${m5.length}, htf ${m15.length})` };
  }

  // Clock is driven by the bar being replayed, so the engine's staleness and
  // recency checks evaluate against simulated time rather than wall time.
  let simNow = m5[0].start;
  const metaLearner = new MetaLearner();
  if (opts.seedStudy) metaLearner.seedFromStudy(opts.seedStudy);
  const engine = new MasisEngine({
    symbol, now: () => simNow, metaLearner,
    swarmEnabled: opts.swarmEnabled !== false,
    swarmMode: opts.swarmMode || (opts.swarmEnabled === false ? 'off' : 'advisory')
  });

  // Derivatives context, replayed causally: only rows timestamped at or before
  // the bar being evaluated are ever visible to the engine.
  const derivAll = opts.derivs || [];
  let derivPtr = 0;
  const flow = new FlowProxy();
  engine.flow = flow;

  const pm = new PositionManager(Object.assign({ now: () => simNow }, (global.__MASIS_PM_OVERRIDE__ || {})));
  const gov = new RiskGovernor({ riskPerTradePct: 0.5, maxConcurrentPositions: 1, cooldownAfterLossMs: 20 * 60 * 1000, now: () => simNow });
  gov.updateEquity(startEquity);

  let equity = startEquity;
  let peakEquity = startEquity;
  let maxDrawdown = 0;
  const trades = [];
  const gradeCounts = {};
  const blockerCounts = {};
  let evaluations = 0, signalsRaised = 0, missedEntries = 0;
  const entryRejects = {};

  const seek15 = buildAligner(m15);
  const seek1 = buildAligner(m1);
  const seekLtf = buildAligner(mLtf);

  // Finest available intrabar path for a given decision bar, with an explicit
  // fallback chain. When no finer series reaches this far back, the decision
  // bar itself is used — which is the most pessimistic option available, since
  // it means the stop is evaluated against the bar's full high/low range.
  const pathSeekers = tf.paths.map(series => ({ series, seek: buildAligner(series) }));
  function intrabarPath(bar, barCloseTs) {
    for (const { series, seek } of pathSeekers) {
      const from = seek(bar.start);
      const to = seek(barCloseTs);
      if (to > from) return series.slice(from, to);
    }
    return [bar];
  }

  let open = null; // { trade, qty, entryFillPrice, riskAmount }

  for (let i = 60; i < m5.length - 1; i++) {
    const bar = m5[i];
    const barClose = bar.start + MTF_MS;
    simNow = barClose;

    flow.push(bar);
    engine.seedCandles('mtf', m5.slice(Math.max(0, i - 399), i + 1));
    engine.seedCandles('htf', m15.slice(Math.max(0, seek15(barClose) - 399), seek15(barClose)));
    engine.seedCandles('ltf', mLtf.slice(Math.max(0, seekLtf(barClose) - 120), seekLtf(barClose)));
    engine.ticker = { lastPrice: String(bar.close), symbol };

    while (derivPtr < derivAll.length && derivAll[derivPtr].ts <= barClose) derivPtr++;
    const visibleDerivs = derivAll.slice(0, derivPtr);
    engine.setDerivatives(visibleDerivs);
    flow.setTakerData(visibleDerivs);

    const state = engine.getState();
    evaluations++;
    gradeCounts[state.grade || 'none'] = (gradeCounts[state.grade || 'none'] || 0) + 1;
    for (const b of state.blockers || []) {
      const k = b.split(/[:(]/)[0].trim().slice(0, 60);
      blockerCounts[k] = (blockerCounts[k] || 0) + 1;
    }

    // ── Manage an open position across this bar's 1-minute path ──
    if (open) {
      const path = intrabarPath(bar, barClose);
      const t = open.trade;
      const isLong = t.side === 'Buy';

      for (const min of path) {
        // Pessimistic ordering: stop before target inside the same minute.
        const stopHit = isLong ? min.low <= t.stopLoss : min.high >= t.stopLoss;
        if (stopHit) {
          closeRemainder(t.stopLoss, 'STOP');
          break;
        }
        let tpDone = false;
        for (let k = 0; k < t.targets.length && !tpDone; k++) {
          if (t.tpFilled[k]) continue;
          const level = t.targets[k];
          const reached = isLong ? min.high >= level : min.low <= level;
          if (!reached) break;
          scaleOut(k, level);
          tpDone = true;
        }
        if (open && open.trade.remainingFraction <= 0.0001) { closeRemainder(t.targets[t.targets.length - 1], 'TARGET'); break; }
      }
    }

    // ── Max favourable / adverse excursion ──
    // Recorded in R on every bar the trade is open, using the bar's extremes
    // rather than its close, so it reflects what the trade actually offered.
    // This is what separates "the entry was wrong" from "the entry was right
    // and the exit gave it back": a loser whose MFE reached +1R was a winning
    // trade that was managed into a loss, and no entry filter can fix that.
    if (open) {
      const t = open.trade;
      const dir = t.side === 'Buy' ? 1 : -1;
      const best = dir === 1 ? bar.high : bar.low;
      const worst = dir === 1 ? bar.low : bar.high;
      const rOf = px => (dir * (px - t.entryPrice)) / open.riskAmountPerUnit;
      if (open.riskAmountPerUnit > 0) {
        open.mfeR = Math.max(open.mfeR, rOf(best));
        open.maeR = Math.min(open.maeR, rOf(worst));
        const barR = (bar.high - bar.low) / open.riskAmountPerUnit;
        open.barRSum = (open.barRSum || 0) + barR;
        open.barRCount = (open.barRCount || 0) + 1;
      }
    }

    // ── Position manager decision at the bar close ──
    if (open) {
      const t = open.trade;
      const action = pm.evaluate(t, {
        price: bar.close,
        confirmedCandle: bar,
        atr: state.regimeMetrics.htfAtr || null,
        spreadPct: 0.02,
        dataValid: state.dataStatus !== 'INVALID',
        opposingSignal: state.grade && state.direction ? { direction: state.direction, grade: state.grade, score: state.score } : null
      });
      if (action.action === 'CLOSE_ALL') {
        closeRemainder(bar.close, action.reason);
      } else if (action.action === 'MOVE_STOP') {
        pm.markStopMoved(t, action.newStop, action.reason);
      } else if (action.action === 'SCALE_OUT') {
        scaleOut(action.tpIndex, t.targets[action.tpIndex]);
      }
    }

    // ── Entry ──
    if (!open && (state.decision === 'BUY' || state.decision === 'SELL')) {
      signalsRaised++;
      const gate = gov.canOpen({ symbol, openPositions: [] });
      if (!gate.allowed) {
        for (const r of gate.reasons) {
          const k = r.split(/[(]/)[0].trim().slice(0, 50);
          entryRejects[k] = (entryRejects[k] || 0) + 1;
        }
      }
      if (gate.allowed) {
        const next = m5[i + 1];
        const side = state.decision === 'BUY' ? 'Buy' : 'Sell';

        // Entry fill. In taker mode we cross the spread at the next open. In
        // maker mode we rest a limit at the signal price and only trade if the
        // market comes to us — no slippage, maker fee, and some signals simply
        // never fill. Missed fills are counted, not quietly dropped.
        let entry = null;
        if (EXEC_STYLE === 'maker') {
          const base = state.entry || bar.close;
          const limit = side === 'Buy'
            ? base * (1 - MAKER_OFFSET_BPS / 10000)
            : base * (1 + MAKER_OFFSET_BPS / 10000);
          let filled = false;
          for (let k = 1; k <= MAKER_ENTRY_TIMEOUT_BARS && i + k < m5.length; k++) {
            const b = m5[i + k];
            if (side === 'Buy' ? b.low <= limit : b.high >= limit) { filled = true; break; }
          }
          if (filled) entry = limit; else missedEntries++;
        } else {
          entry = fillPrice(next.open, side, true);
        }

        // Re-anchor the stop to the actual fill so risk stays exactly 0.5%.
        const rawStop = state.stopLoss;
        // Push the stop away from the fill by STOP_MULT. Anchored on the FILL,
        // not the signal price, so the widening is measured from where the trade
        // actually sits rather than from where it was proposed.
        const stop = (entry === null || STOP_MULT === 1)
          ? rawStop
          : entry - Math.sign(entry - rawStop) * Math.abs(entry - rawStop) * STOP_MULT;
        const rawInval = state.invalidation;
        const invalidation = (entry === null || INVAL_MULT === 1 || rawInval == null)
          ? rawInval
          : entry - Math.sign(entry - rawInval) * Math.abs(entry - rawInval) * INVAL_MULT;
        const riskDist = entry === null ? 0 : Math.abs(entry - stop);
        if (entry !== null && riskDist > 0) {
          const sized = gov.sizePosition({ entry, stop, equity, symbol, qtyStep: 0.0001, minQty: 0.0001 });
          if (sized.qty <= 0) entryRejects[`sizing: ${sized.rejected}`.slice(0, 60)] = (entryRejects[`sizing: ${sized.rejected}`.slice(0, 60)] || 0) + 1;
          if (sized.qty > 0) {
            equity -= feeOn(sized.qty * entry);
            const t = pm.open({
              symbol, side,
              entryPrice: entry,
              stopLoss: stop,
              invalidation,
              targets: state.takeProfit,
              riskDist,
              qty: sized.qty,
              setupName: state.setupType,
              grade: state.grade,
              score: state.score,
              regime: state.regime,
              narrative: state.narrative,
              entryBar: i,
              // The panel AS IT WAS at entry. Scoring analysts against a later
              // snapshot would be marking their work with the answer sheet.
              entryPanel: (state.panel || []).map(x => ({ id: x.id, read: x.read, conviction: x.conviction })),
              panelConviction: state.panelConviction,
              patternScore: state.patternScore
            });
            open = { trade: t, qty: sized.qty, riskAmount: sized.riskAmount, remainingQty: sized.qty, netMoney: -feeOn(sized.qty * entry),
              riskAmountPerUnit: riskDist, mfeR: 0, maeR: 0,
              // Stop distance relative to a typical bar. If one bar routinely
              // spans more than 1R the stop sits inside normal noise, and both
              // the giveback and the stop-out rate are explained by geometry
              // rather than by the signal being wrong.
              stopVsBar: null };
          }
        }
      }
    }

    peakEquity = Math.max(peakEquity, equity);
    maxDrawdown = Math.max(maxDrawdown, (peakEquity - equity) / peakEquity);
    gov.updateEquity(equity);
  }

  if (open) closeRemainder(m5[m5.length - 1].close, 'END_OF_DATA');

  function scaleOut(index, level) {
    const t = open.trade;
    const fraction = pm.config.scaleOutFractions[index];
    const qty = open.qty * fraction;
    // A take-profit is a resting limit by nature — price comes to it.
    const exit = EXEC_STYLE === 'maker' ? level : fillPrice(level, t.side, false);
    const gross = (t.side === 'Buy' ? exit - t.entryPrice : t.entryPrice - exit) * qty;
    const net = gross - feeOn(qty * exit, EXEC_STYLE === 'maker' ? 'maker' : 'taker');
    equity += net;
    open.netMoney += net;
    open.remainingQty -= qty;
    pm.markTpFilled(t, index, exit);
  }

  function closeRemainder(price, reason) {
    const t = open.trade;
    const qty = open.remainingQty;
    const exit = fillPrice(price, t.side, false);
    if (qty > 0) {
      const gross = (t.side === 'Buy' ? exit - t.entryPrice : t.entryPrice - exit) * qty;
      // Stops and invalidation exits are market orders: a stop that waits for a
      // maker fill is not a stop.
      const net = gross - feeOn(qty * exit, 'taker');
      equity += net;
      open.netMoney += net;
    }
    pm.finalise(t, exit, reason);
    // R is reported NET of every fee and every unit of slippage actually paid,
    // entry fee included. Gross R flatters an intraday system badly: at these
    // stop distances the round trip costs a meaningful fraction of 1R, and a
    // report that hides it is telling you about a system nobody can trade.
    const pnl = +open.netMoney.toFixed(6);
    t.finalR = +(pnl / open.riskAmount).toFixed(3);
    gov.recordOutcome({ symbol, pnl, rMultiple: t.finalR });
    if (t.entryPanel && t.entryPanel.length) {
      metaLearner.recordOutcome(t.regime, t.entryPanel, t.side === 'Buy' ? 'LONG' : 'SHORT', t.finalR);
    }
    trades.push({
      symbol, side: t.side, setup: t.setupName, grade: t.grade, score: t.score,
      regime: t.regime, entry: +t.entryPrice.toFixed(6), exit: +exit.toFixed(6),
      r: t.finalR, reason, barsHeld: t.barsHeld, explain: pm.explain(t),
      patternScore: t.patternScore, panelConviction: t.panelConviction,
      mfeR: open.mfeR != null ? +open.mfeR.toFixed(3) : null,
      maeR: open.maeR != null ? +open.maeR.toFixed(3) : null,
      avgBarR: open.barRCount ? +(open.barRSum / open.barRCount).toFixed(3) : null
    });
    pm.forget(t.symbol, t.side);
    open = null;
  }

  return summarise({ symbol, engine: opts.swarmEnabled === false ? 'V3 (swarm off)' : 'V3 (swarm)', mode, tfLabel: tf.label,
    trades, equity, startEquity, maxDrawdown, evaluations, signalsRaised, gradeCounts, blockerCounts, entryRejects,
    analystReport: metaLearner.report(), missedEntries, execStyle: EXEC_STYLE,
    bars: m5.length, source: bundle.source });
}

// ────────────────────────────────────────────────────────────────────────
// V2 baseline run — same data, same costs, same fill rules
// ────────────────────────────────────────────────────────────────────────
function runV2(symbol, bundle, startEquity) {
  const m1 = bundle.series['1'] || [];
  if (m1.length < 300) return { symbol, error: 'not enough 1m bars' };

  let equity = startEquity;
  let peakEquity = startEquity, maxDrawdown = 0;
  const trades = [];
  let open = null;
  let signalsRaised = 0, evaluations = 0;

  // V2 sized by a flat margin per trade, not by risk. Reproduced here as the
  // same 0.5%-of-equity notional the dashboard's default $50-on-a-$10k account
  // implies, so the comparison is on decision quality rather than on sizing.
  for (let i = 60; i < m1.length - 1; i++) {
    const window = m1.slice(Math.max(0, i - 199), i + 1);
    const bar = m1[i];
    const sig = baselineV2.decide(window);
    evaluations++;

    if (open) {
      const t = open;
      const isLong = t.side === 'Buy';
      const stopHit = isLong ? bar.low <= t.stopLoss : bar.high >= t.stopLoss;
      const tpHit = isLong ? bar.high >= t.takeProfit : bar.low <= t.takeProfit;
      if (stopHit) {
        closeV2(t.stopLoss, 'STOP');
      } else if (tpHit) {
        closeV2(t.takeProfit, 'TARGET');
      } else if ((sig.decision === 'BUY' || sig.decision === 'SELL')) {
        // The Position Guardian: flip against the position closes it at market,
        // irrespective of P&L or how long it has been open.
        const flipped = (isLong && sig.decision === 'SELL') || (!isLong && sig.decision === 'BUY');
        if (flipped) closeV2(bar.close, 'GUARDIAN_FLIP');
      }
    }

    if (!open && (sig.decision === 'BUY' || sig.decision === 'SELL')) {
      signalsRaised++;
      const next = m1[i + 1];
      const side = sig.decision === 'BUY' ? 'Buy' : 'Sell';
      const entry = fillPrice(next.open, side, true);
      const riskDist = Math.abs(entry - sig.stopLoss);
      if (riskDist > 0) {
        const riskAmount = equity * 0.005;
        const qty = riskAmount / riskDist;
        equity -= feeOn(qty * entry);
        open = {
          symbol, side, entryPrice: entry, stopLoss: sig.stopLoss,
          takeProfit: sig.takeProfit[0], qty, riskDist, riskAmount, entryBar: i
        };
      }
    }

    peakEquity = Math.max(peakEquity, equity);
    maxDrawdown = Math.max(maxDrawdown, (peakEquity - equity) / peakEquity);
  }
  if (open) closeV2(m1[m1.length - 1].close, 'END_OF_DATA');

  function closeV2(price, reason) {
    const t = open;
    const exit = fillPrice(price, t.side, false);
    const gross = (t.side === 'Buy' ? exit - t.entryPrice : t.entryPrice - exit) * t.qty;
    const net = gross - feeOn(t.qty * exit);
    equity += net;
    const r = net / t.riskAmount;
    trades.push({ symbol, side: t.side, setup: 'V2_VOTE', grade: 'n/a', entry: +t.entryPrice.toFixed(6), exit: +exit.toFixed(6), r: +r.toFixed(3), reason, barsHeld: 0 });
    open = null;
  }

  return summarise({ symbol, engine: 'V2 (reconstruction)', trades, equity, startEquity, maxDrawdown, evaluations, signalsRaised, gradeCounts: {}, blockerCounts: {}, bars: m1.length, source: bundle.source });
}

// ────────────────────────────────────────────────────────────────────────
function summarise(r) {
  const { trades } = r;
  const wins = trades.filter(t => t.r > 0);
  const losses = trades.filter(t => t.r <= 0);
  const rs = trades.map(t => t.r);
  const grossWin = wins.reduce((a, t) => a + t.r, 0);
  const grossLoss = Math.abs(losses.reduce((a, t) => a + t.r, 0));
  const avgWin = wins.length ? grossWin / wins.length : 0;
  const avgLoss = losses.length ? grossLoss / losses.length : 0;
  const winRate = trades.length ? wins.length / trades.length : 0;

  const byReason = {};
  for (const t of trades) byReason[t.reason] = (byReason[t.reason] || 0) + 1;
  const bySetup = {};
  for (const t of trades) {
    bySetup[t.setup] = bySetup[t.setup] || { n: 0, wins: 0, totalR: 0 };
    bySetup[t.setup].n++;
    if (t.r > 0) bySetup[t.setup].wins++;
    bySetup[t.setup].totalR += t.r;
  }
  for (const k of Object.keys(bySetup)) {
    bySetup[k].winRate = +((bySetup[k].wins / bySetup[k].n) * 100).toFixed(1);
    bySetup[k].totalR = +bySetup[k].totalR.toFixed(2);
    bySetup[k].expectancyR = +(bySetup[k].totalR / bySetup[k].n).toFixed(3);
  }

  return Object.assign({}, r, {
    tradeCount: trades.length,
    winRate: +(winRate * 100).toFixed(1),
    avgWinR: +avgWin.toFixed(2),
    avgLossR: +avgLoss.toFixed(2),
    expectancyR: trades.length ? +(rs.reduce((a, b) => a + b, 0) / trades.length).toFixed(3) : 0,
    totalR: +rs.reduce((a, b) => a + b, 0).toFixed(2),
    profitFactor: grossLoss > 0 ? +(grossWin / grossLoss).toFixed(2) : (grossWin > 0 ? Infinity : 0),
    netPnl: +(r.equity - r.startEquity).toFixed(2),
    returnPct: +(((r.equity - r.startEquity) / r.startEquity) * 100).toFixed(2),
    maxDrawdownPct: +(r.maxDrawdown * 100).toFixed(2),
    signalRate: r.evaluations ? +((r.signalsRaised / r.evaluations) * 100).toFixed(2) : 0,
    exitBreakdown: byReason,
    setupBreakdown: bySetup
  });
}

function fmtPct(v) { return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`; }

function report(results) {
  const line = '─'.repeat(78);
  console.log(`\n${line}\n  MASIS BACKTEST — V3 vs. reconstructed V2, identical bars and costs\n${line}`);
  console.log(`  Costs modelled: ${(TAKER_FEE * 100).toFixed(3)}% taker fee + ${(SLIPPAGE * 100).toFixed(2)}% slippage per fill, both sides.`);
  console.log(`  Intrabar rule: when a 1m bar contains both stop and target, the STOP is taken.`);

  for (const pair of results) {
    const { v3, v2 } = pair;
    console.log(`\n  ${v3.symbol}  (data source: ${v3.source}, ${v3.bars} decision bars, V3 on ${v3.tfLabel})`);
    console.log(`  ${'metric'.padEnd(22)}${'V3'.padStart(14)}${'V2 (recon)'.padStart(16)}`);
    const row = (label, a, b) => console.log(`  ${label.padEnd(22)}${String(a).padStart(14)}${String(b).padStart(16)}`);
    row('trades', v3.tradeCount, v2.tradeCount);
    row('win rate %', v3.winRate, v2.winRate);
    row('avg win (R)', v3.avgWinR, v2.avgWinR);
    row('avg loss (R)', v3.avgLossR, v2.avgLossR);
    row('expectancy (R)', v3.expectancyR, v2.expectancyR);
    row('profit factor', v3.profitFactor, v2.profitFactor);
    row('total R', v3.totalR, v2.totalR);
    row('return %', fmtPct(v3.returnPct), fmtPct(v2.returnPct));
    row('max drawdown %', v3.maxDrawdownPct, v2.maxDrawdownPct);
    if (pair.v3NoSwarm) {
      const ns = pair.v3NoSwarm;
      console.log(`  Swarm OFF (same bars): ${ns.tradeCount} trades, win ${ns.winRate}%, expectancy ${ns.expectancyR >= 0 ? '+' : ''}${ns.expectancyR}R, PF ${ns.profitFactor}`);
    }
    console.log(`  V3 signals raised: ${v3.signalsRaised}, entries taken: ${v3.tradeCount}${v3.missedEntries ? `, limit entries missed: ${v3.missedEntries}` : ''} (exec: ${v3.execStyle})`);
    if (v3.entryRejects && Object.keys(v3.entryRejects).length) console.log(`  V3 entry rejections: ${JSON.stringify(v3.entryRejects)}`);
    console.log(`  V3 exits: ${JSON.stringify(v3.exitBreakdown)}`);
    console.log(`  V2 exits: ${JSON.stringify(v2.exitBreakdown)}`);
    if (Object.keys(v3.setupBreakdown).length) {
      console.log('  V3 by setup:');
      for (const [k, s] of Object.entries(v3.setupBreakdown)) {
        console.log(`    ${k.padEnd(20)} n=${String(s.n).padStart(3)}  win ${String(s.winRate).padStart(5)}%  expectancy ${s.expectancyR >= 0 ? '+' : ''}${s.expectancyR}R`);
      }
    }
  }

  // Portfolio aggregate
  const agg = (key) => {
    const all = results.flatMap(p => p[key].trades);
    const wins = all.filter(t => t.r > 0);
    const rs = all.map(t => t.r);
    const gw = wins.reduce((a, t) => a + t.r, 0);
    const gl = Math.abs(all.filter(t => t.r <= 0).reduce((a, t) => a + t.r, 0));
    return {
      n: all.length,
      winRate: all.length ? +((wins.length / all.length) * 100).toFixed(1) : 0,
      expectancyR: all.length ? +(rs.reduce((a, b) => a + b, 0) / all.length).toFixed(3) : 0,
      totalR: +rs.reduce((a, b) => a + b, 0).toFixed(2),
      profitFactor: gl > 0 ? +(gw / gl).toFixed(2) : 0
    };
  };
  const a3 = agg('v3'), a2 = agg('v2');
  console.log(`\n${line}\n  COMBINED ACROSS SYMBOLS\n${line}`);
  console.log(`  ${'metric'.padEnd(22)}${'V3'.padStart(14)}${'V2 (recon)'.padStart(16)}`);
  console.log(`  ${'trades'.padEnd(22)}${String(a3.n).padStart(14)}${String(a2.n).padStart(16)}`);
  console.log(`  ${'win rate %'.padEnd(22)}${String(a3.winRate).padStart(14)}${String(a2.winRate).padStart(16)}`);
  console.log(`  ${'expectancy (R)'.padEnd(22)}${String(a3.expectancyR).padStart(14)}${String(a2.expectancyR).padStart(16)}`);
  console.log(`  ${'profit factor'.padEnd(22)}${String(a3.profitFactor).padStart(14)}${String(a2.profitFactor).padStart(16)}`);
  console.log(`  ${'total R'.padEnd(22)}${String(a3.totalR).padStart(14)}${String(a2.totalR).padStart(16)}`);

  console.log(`\n  Sample size is ${a3.n} V3 trades. Read that number before reading any`);
  console.log(`  other number here: under a few hundred trades, win rate and expectancy`);
  console.log(`  carry wide confidence intervals and neither figure should be treated`);
  console.log(`  as a forecast. What this run can legitimately establish is direction:`);
  console.log(`  whether the selectivity and exit changes move expectancy the right way.\n${line}\n`);

  return { v3: a3, v2: a2 };
}

function main() {
  const a = args();
  if (a.pm) global.__MASIS_PM_OVERRIDE__ = JSON.parse(a.pm);
  if (a.exec === 'maker' || a.exec === 'taker') EXEC_STYLE = a.exec;
  if (a.makerOffset != null) MAKER_OFFSET_BPS = parseFloat(a.makerOffset);
  if (a.makerTimeout != null) MAKER_ENTRY_TIMEOUT_BARS = parseInt(a.makerTimeout, 10);
  // Stop and invalidation distances, as multiples of what the playbook proposed.
  // The 98-trade forensics measured a median bar range of 1.39R while in trade,
  // meaning one ordinary bar reaches the stop: the trade must go right
  // immediately or die. Widening both moves them outside routine noise. Risk per
  // trade is unchanged because sizePosition() derives quantity from the stop
  // distance, so a wider stop simply buys less. The cost is that targets are
  // absolute prices, so each win is worth proportionally fewer R -- which is
  // exactly the trade-off this sweep is meant to price.
  if (a.stopMult != null) STOP_MULT = parseFloat(a.stopMult);
  if (a.invalMult != null) INVAL_MULT = parseFloat(a.invalMult);
  if (a.quiet) { const noop = () => {}; global.__origLog = console.log; console.log = noop; }
  const startEquity = parseFloat(a.equity || '1000');
  // Optional: analyst weights seeded from the offline predictive-value study.
  let seedStudy = null;
  if (a.seed) {
    const sp = path.join(__dirname, 'results', 'analyst-evaluation.json');
    if (fs.existsSync(sp)) {
      seedStudy = JSON.parse(fs.readFileSync(sp, 'utf8')).rows;
      console.log(`  Analyst weights seeded from ${sp} (fitted to that study window — not an out-of-sample result).`);
    } else {
      console.log('  --seed requested but no study found; run backtest/evaluate-analysts.js first.');
    }
  }

  const files = fs.existsSync(DATA_DIR) ? fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.json')) : [];
  if (!files.length) {
    console.error('No cached data. Run: node backtest/fetch-klines.js BTCUSDT,ETHUSDT,SOLUSDT 1,5,15 12000');
    process.exit(1);
  }
  const wanted = a.symbols ? a.symbols.split(',') : null;

  const results = [];
  for (const f of files) {
    const symbol = f.replace('.json', '');
    if (wanted && !wanted.includes(symbol)) continue;
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));

    // Derivatives are published per coin, not per contract symbol.
    const ccy = symbol.replace(/USDT$/, '');
    const derivFile = path.join(DATA_DIR, `${ccy}-derivs.json`);
    const derivs = fs.existsSync(derivFile) ? JSON.parse(fs.readFileSync(derivFile, 'utf8')).series : [];

    const mode = a.mode || 'fast';
    const v3 = runV3(symbol, bundle, startEquity, mode, { derivs, swarmEnabled: true, swarmMode: a.swarmMode || 'advisory', seedStudy });
    const v3NoSwarm = a.ab ? runV3(symbol, bundle, startEquity, mode, { derivs, swarmEnabled: false }) : null;
    const v2 = runV2(symbol, bundle, startEquity);
    if (v3.error) { console.error(`${symbol}: ${v3.error}`); continue; }
    results.push({ v3, v2, v3NoSwarm });
  }
  const combined = report(results);
  if (a.quiet) { console.log = global.__origLog; console.log(JSON.stringify(combined.v3)); }

  // Analyst reliability, aggregated across symbols.
  const analystRows = {};
  for (const p of results) for (const row of (p.v3.analystReport || [])) {
    const k = `${row.regime}|${row.analyst}`;
    analystRows[k] = analystRows[k] || { regime: row.regime, analyst: row.analyst, sample: 0, hits: 0 };
    analystRows[k].sample += row.sample;
    analystRows[k].hits += Math.round((row.accuracy || 0) / 100 * row.sample);
  }
  const analystTable = Object.values(analystRows)
    .filter(r => r.sample >= 5)
    .map(r => ({ ...r, accuracy: +((r.hits / r.sample) * 100).toFixed(1) }))
    .sort((x, y) => y.sample - x.sample);
  if (analystTable.length) {
    console.log(`\n  ANALYST RELIABILITY (agreement with the profitable direction, at entry)`);
    console.log(`  ${'regime'.padEnd(10)}${'analyst'.padEnd(16)}${'n'.padStart(5)}${'agree %'.padStart(10)}`);
    for (const r of analystTable) {
      console.log(`  ${r.regime.padEnd(10)}${r.analyst.padEnd(16)}${String(r.sample).padStart(5)}${String(r.accuracy).padStart(10)}`);
    }
    console.log(`  (Below ~15 observations these are not yet meaningful and the meta-learner leaves the weight at 1.0.)`);
  }

  if (a.json) {
    fs.writeFileSync(a.json, JSON.stringify({ generatedAt: new Date().toISOString(), costs: { TAKER_FEE, SLIPPAGE }, combined, results }, null, 2));
    console.log(`  Full detail written to ${a.json}\n`);
  }
}

main();

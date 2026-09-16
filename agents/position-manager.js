/**
 * DIV-12 — Position Manager (replaces the old "Position Guardian").
 *
 * THE DIAGNOSIS
 * The previous guardian ran inside renderPositionGuardian() on a 3-second
 * timer, and market-closed a position the moment ANY of three things was true:
 *   - the live engine's decision for that symbol had flipped to the other side,
 *   - the whale tracker's dominant side had flipped,
 *   - the trap guard had tripped.
 *
 * All three flip constantly. The engine re-derived its decision every 3 seconds
 * from 1-minute data; the whale side flips on a single $50k print inside a
 * 5-minute window. So the practical behaviour was: enter, and at the first
 * adverse wiggle — usually within a minute or two, and usually the ordinary
 * retrace that happens right after any entry — close at market for a small
 * loss, pay the taker fee and the spread, and repeat. That is precisely the
 * "it always cuts out when it's turning red" symptom, and it is also why it
 * never held a winner: a thesis flicker closed profitable positions on exactly
 * the same trigger.
 *
 * Worse, the exit was unconditional on P&L, so a trade that was +2R could be
 * closed by a 3-second blip in whale side.
 *
 * THE REPLACEMENT
 * A position can only be exited by one of a small number of *named, structural*
 * events. Everything else is noise and is explicitly ignored:
 *
 *   1. STOP    — the broker-side stop loss, set at entry, is the hard boundary.
 *   2. TARGET  — scale-outs at TP1/TP2/TP3.
 *   3. STRUCTURE_BROKEN — a CONFIRMED candle closes beyond the invalidation
 *                level the setup was built on. Not a tick through it: a close.
 *   4. THESIS_FLIP — an opposing signal of grade A or better, persisting across
 *                N consecutive evaluations, AND the position is not in profit.
 *   5. TIME_STOP — the idea has had its allotted bars and has gone nowhere.
 *                Freeing dead risk is a good cut; cutting a working trade is not.
 *   6. EMERGENCY — data invalid, spread blowout, feed halt.
 *
 * Plus two rules that protect winners rather than culling them:
 *   - a grace period after entry during which only the hard stop applies;
 *   - once TP1 is banked the stop moves to break-even, and after TP2 it trails
 *     the structure, so "turning red" on an open winner becomes impossible by
 *     construction rather than by reflex.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisPositionManager = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  const DEFAULTS = {
    gracePeriodMs: 4 * 60 * 1000,     // no discretionary exit at all in this window
    thesisFlipConfirmations: 4,       // consecutive evaluations required, not one blip
    // Chosen from the middle of the stable region of a parameter sweep
    // (backtest/README notes the surface), not from its peak. The sweep showed
    // expectancy varying by less than 0.1R across 12/18/30 bars, which on a
    // 38-trade sample is noise — so these are set to a defensible middle value
    // rather than to whichever cell happened to score best.
    timeStopBars: 18,                 // MTF bars before a going-nowhere trade is retired
    timeStopMinR: 0.25,               // ...unless it has made at least this much
    breakEvenAfterTp: 1,              // move stop to entry once TP1 is banked
    trailAfterTp: 1,                  // start trailing once TP1 is banked (was TP2)
    // ── 3-Target Scaled Exit Protocol ──
    // TP1: 25% → quick profit coverage at nearest S/R wall
    // TP2: 35% → second structural level or VP HVN
    // TP3: 40% → runner that trails with tightening ATR stop
    scaleOutFractions: [0.25, 0.35, 0.40],
    // ── Progressive Trailing Stop (tightens per TP level) ──
    trailAtrMultiples: [2.0, 1.2, 0.8],  // ATR multiplier per stage
    trailAtrMultiple: 2.0,               // fallback for legacy 2-target code
    // Whether a confirmed close beyond the invalidation level closes the trade
    // ahead of the stop. Sounds obviously right; measured below.
    useStructuralExit: true,
    // ── Milestone capital protection & S/R coverage ──
    roiBreakEvenPct: 12.0,            // Automatically move stop to entry once ROI >= +12% (1.2% price move at 10x)
    rBreakEvenThreshold: 1.0,         // ...or once unrealised R reaches +1.0R, even before TP1 fills
    srProximityAtr: 0.20,             // Bank partial profit if price comes within 0.20 ATR of opposing S/R wall
    srCoverageFraction: 0.50,         // Fraction to bank when hitting S/R wall (uses TP1 slot)
    srMinProfitR: 0.70,               // S/R coverage requires at least +0.70R profit
    // ── Momentum Exhaustion Exit ──
    momentumExitEnabled: true,        // Close runner when RSI signals exhaustion
    momentumExitMinR: 2.0,            // Only trigger if position is at least +2.0R
    momentumExitRsiLong: 70,          // RSI crossing below this closes long runner
    momentumExitRsiShort: 30          // RSI crossing above this closes short runner
  };

  const SCALP_DEFAULTS = {
    gracePeriodMs: 60 * 1000,          // 1-minute grace period for fast 5m scalps
    timeStopBars: 3,                   // 3 bars of 5m (~15 min max duration)
    timeStopMinR: 0.20,
    timeStopMaxMs: 12 * 60 * 1000,     // 12-minute wall clock timeout for scalps
    breakEvenAfterTp: 1,
    trailAfterTp: 1,
    scaleOutFractions: [0.60, 0.40],   // 60% bank at TP1, 40% runner to TP2
    trailAtrMultiples: [0.8, 0.5],     // Tight trailing stop
    roiBreakEvenPct: 5.0,              // Ratchet stop to BE at +5% ROI
    rBreakEvenThreshold: 0.40,         // Ratchet stop to BE at +0.40R
    srProximityAtr: 0.15,
    srCoverageFraction: 0.60,
    srMinProfitR: 0.35,
    momentumExitEnabled: true,
    momentumExitMinR: 0.70,            // Scalp momentum exhaustion fires earlier
    momentumExitRsiLong: 68,
    momentumExitRsiShort: 32
  };

  const SURESHOT_DEFAULTS = {
    gracePeriodMs: 30 * 1000,          // 30s grace period
    timeStopBars: 2,                   // 2 bars of 5m (10m max timeout)
    timeStopMinR: 0.15,
    timeStopMaxMs: 8 * 60 * 1000,      // 8-minute max duration
    breakEvenAfterTp: 1,               // Ratchet stop to Entry immediately upon TP1
    trailAfterTp: 1,
    scaleOutFractions: [0.90, 0.10],   // 90% banked at TP1 (+0.50% move), 10% margin runner remains
    trailAtrMultiples: [0.5],          // Tight 0.5 ATR trail for 10% runner
    roiBreakEvenPct: 2.5,              // Instant break-even ratchet at +2.5% ROI (+0.25% price move @ 10x)
    rBreakEvenThreshold: 0.25,         // +0.25R profit moves stop to Entry
    srProximityAtr: 0.10,
    srCoverageFraction: 0.90,          // Bank 90% coverage at opposing structural wall
    srMinProfitR: 0.25,
    momentumExitEnabled: true,
    momentumExitMinR: 0.40,
    momentumExitRsiLong: 70,
    momentumExitRsiShort: 30
  };

  const EXIT = {
    STOP: 'STOP',
    TARGET: 'TARGET',
    SR_COLLISION: 'SR_COLLISION',
    STRUCTURE_BROKEN: 'STRUCTURE_BROKEN',
    THESIS_FLIP: 'THESIS_FLIP',
    TIME_STOP: 'TIME_STOP',
    MOMENTUM_EXIT: 'MOMENTUM_EXIT',
    EMERGENCY: 'EMERGENCY'
  };

  class PositionManager {
    constructor(options = {}) {
      this.config = Object.assign({}, DEFAULTS, options);
      // Injectable clock, so the grace period and age checks mean the same
      // thing under replay as they do live. Without this the backtest would
      // measure a four-minute grace window against wall-clock seconds and
      // never leave it — which is exactly the kind of silent difference that
      // makes a backtest disagree with production.
      this.now = options.now || (() => Date.now());
      this.trades = {}; // key `${symbol}-${side}` -> trade record
    }

    key(symbol, side) { return `${symbol}-${side}`; }

    /** Registers a position we opened, with the full reasoning snapshot. A
     * trade we cannot explain is a trade we cannot review afterwards. */
    open(record) {
      const k = this.key(record.symbol, record.side);
      this.trades[k] = Object.assign({
        openedAt: this.now(),
        barsHeld: 0,
        lastBarSeen: null,
        tpFilled: [false, false, false],
        stopMovedToBreakEven: false,
        trailingStop: null,
        trailStage: 0,             // which ATR multiplier stage (0=initial, 1=after TP1, 2=after TP2)
        flipStreak: 0,
        realisedR: 0,
        remainingFraction: 1,
        exitLog: []
      }, record);
      return this.trades[k];
    }

    get(symbol, side) { return this.trades[this.key(symbol, side)]; }
    forget(symbol, side) { delete this.trades[this.key(symbol, side)]; }
    all() { return Object.values(this.trades); }

    /** R currently achieved, from entry, in units of the original risk. */
    currentR(trade, price) {
      if (!trade || !trade.riskDist) return 0;
      const move = trade.side === 'Buy' ? price - trade.entryPrice : trade.entryPrice - price;
      return move / trade.riskDist;
    }

    /**
     * The single decision point. Returns an action object; the caller executes
     * it. Pure function of the trade record plus current market facts, so it is
     * directly testable and is exercised by the backtest.
     *
     * @param {object} trade
     * @param {object} market  { price, confirmedCandle, atr, spreadPct, dataValid,
     *                           nextResistance, nextSupport,
     *                           opposingSignal: {direction, grade, score}|null }
     */
    evaluate(trade, market) {
      const hold = { action: 'HOLD', reasons: [] };
      if (!trade) return hold;

      const price = market.price;
      const r = this.currentR(trade, price);
      const age = this.now() - trade.openedAt;
      const inProfit = r > 0.05;
      const isLong = trade.side === 'Buy';
      const isSureShot = !!trade.isSureShot || trade.setupName === 'SURESHOT_MICRO_SCALP';
      const isScalp = !!trade.isScalp || isSureShot;

      // ── Dynamic parameter resolution (SureShot vs Scalp vs Standard) ──
      const gracePeriodMs = isSureShot ? SURESHOT_DEFAULTS.gracePeriodMs : (isScalp ? (this.config.scalpGracePeriodMs || SCALP_DEFAULTS.gracePeriodMs) : this.config.gracePeriodMs);
      const srMinProfitR = isSureShot ? SURESHOT_DEFAULTS.srMinProfitR : (isScalp ? (this.config.scalpSrMinProfitR || SCALP_DEFAULTS.srMinProfitR) : (this.config.srMinProfitR || 0.70));
      const srCoverageFraction = isSureShot ? SURESHOT_DEFAULTS.srCoverageFraction : (isScalp ? (this.config.scalpSrCoverageFraction || SCALP_DEFAULTS.srCoverageFraction) : (this.config.srCoverageFraction || 0.50));
      const srProximityAtr = isSureShot ? SURESHOT_DEFAULTS.srProximityAtr : (isScalp ? (this.config.scalpSrProximityAtr || SCALP_DEFAULTS.srProximityAtr) : (this.config.srProximityAtr || 0.20));
      const scaleOutFractions = isSureShot ? SURESHOT_DEFAULTS.scaleOutFractions : (isScalp ? (this.config.scalpScaleOutFractions || SCALP_DEFAULTS.scaleOutFractions) : this.config.scaleOutFractions);
      const rBreakEvenThreshold = isSureShot ? SURESHOT_DEFAULTS.rBreakEvenThreshold : (isScalp ? (this.config.scalpRBreakEvenThreshold || SCALP_DEFAULTS.rBreakEvenThreshold) : (this.config.rBreakEvenThreshold || 1.0));
      const roiBreakEvenPct = isSureShot ? SURESHOT_DEFAULTS.roiBreakEvenPct : (isScalp ? (this.config.scalpRoiBreakEvenPct || SCALP_DEFAULTS.roiBreakEvenPct) : (this.config.roiBreakEvenPct || 12.0));
      const trailAtrMultiples = isSureShot ? SURESHOT_DEFAULTS.trailAtrMultiples : (isScalp ? (this.config.scalpTrailAtrMultiples || SCALP_DEFAULTS.trailAtrMultiples) : (this.config.trailAtrMultiples || [2.0, 1.2, 0.8]));
      const minTrailR = isSureShot ? 0.35 : (isScalp ? 0.5 : 1.2);
      const momentumExitMinR = isSureShot ? SURESHOT_DEFAULTS.momentumExitMinR : (isScalp ? (this.config.scalpMomentumExitMinR || SCALP_DEFAULTS.momentumExitMinR) : (this.config.momentumExitMinR || 2.0));
      const timeStopBars = isSureShot ? SURESHOT_DEFAULTS.timeStopBars : (isScalp ? (this.config.scalpTimeStopBars || SCALP_DEFAULTS.timeStopBars) : this.config.timeStopBars);
      const timeStopMinR = isSureShot ? SURESHOT_DEFAULTS.timeStopMinR : (isScalp ? (this.config.scalpTimeStopMinR || SCALP_DEFAULTS.timeStopMinR) : this.config.timeStopMinR);

      const lev = trade.leverage || 10;
      const priceMovePct = trade.entryPrice > 0 ? (Math.abs(price - trade.entryPrice) / trade.entryPrice) * 100 : 0;
      const inProfitDirection = isLong ? price > trade.entryPrice : price < trade.entryPrice;
      const roiPct = inProfitDirection ? (priceMovePct * lev) : -(priceMovePct * lev);

      // ── 1. Emergency: the feed itself is untrustworthy ──
      if (market.dataValid === false) {
        return { action: 'CLOSE_ALL', reason: EXIT.EMERGENCY, detail: 'Market data failed validation — flattening rather than managing a position blind' };
      }
      if (market.spreadPct != null && market.spreadPct > 0.35) {
        return { action: 'CLOSE_ALL', reason: EXIT.EMERGENCY, detail: `Spread blew out to ${market.spreadPct.toFixed(3)}% — liquidity has gone, exiting before it gets worse` };
      }

      // ── 2A. S/R Resistance / Support Collision: bank profit coverage before reversal ──
      const opposingLevel = isLong ? (market.nextResistance || trade.nextResistance) : (market.nextSupport || trade.nextSupport);
      if (opposingLevel && !trade.tpFilled[0] && r >= srMinProfitR) {
        const atr = market.atr || (trade.riskDist * 0.5);
        const distToSr = isLong ? opposingLevel - price : price - opposingLevel;
        // Within proximity ATR of the opposing structure wall
        if (distToSr <= atr * srProximityAtr) {
          return {
            action: 'SCALE_OUT',
            tpIndex: 0,
            fraction: srCoverageFraction,
            reason: EXIT.SR_COLLISION,
            detail: `Opposing structural ${isLong ? 'resistance' : 'support'} at ${opposingLevel} tested (${r.toFixed(2)}R, +${roiPct.toFixed(1)}% ROI) — banking ${Math.round(srCoverageFraction * 100)}% profit coverage before potential reversal`
          };
        }
      }

      // ── 2B. Planned Targets: bank profit in planned slices ──
      for (let i = 0; i < trade.targets.length; i++) {
        if (trade.tpFilled[i]) continue;
        const level = trade.targets[i];
        const reached = isLong ? price >= level : price <= level;
        if (!reached) break; // ordered near-to-far; never skip ahead
        const frac = scaleOutFractions[Math.min(i, scaleOutFractions.length - 1)];
        return {
          action: 'SCALE_OUT',
          tpIndex: i,
          fraction: frac,
          reason: EXIT.TARGET,
          detail: `TP${i + 1} reached at ${level} (${r.toFixed(2)}R, +${roiPct.toFixed(1)}% ROI) — banking ${Math.round(frac * 100)}% of the original size`
        };
      }

      // ── 3. Stop management: Dual-Trigger Capital Protection & Trailing Stop ──
      // Milestone break-even: if trade hits TP1, OR hits break-even R, OR reaches ROI threshold,
      // move stop to break-even immediately. A trade that is in profit must NEVER turn red.
      const reachedBeMilestone = inProfitDirection && (
        trade.tpFilled[this.config.breakEvenAfterTp - 1] ||
        r >= rBreakEvenThreshold ||
        roiPct >= roiBreakEvenPct
      );

      if (reachedBeMilestone && !trade.stopMovedToBreakEven) {
        return {
          action: 'MOVE_STOP',
          newStop: trade.entryPrice,
          reason: 'BREAK_EVEN',
          detail: `Capital protection milestone reached (${r.toFixed(2)}R, +${roiPct.toFixed(1)}% ROI) — stop ratcheted to break-even at ${trade.entryPrice}. ${isScalp ? 'Scalp' : 'Trade'} is now risk-free.`
        };
      }

      // ── Progressive Trailing Stop (tightens per TP level) ──
      const canTrail = (trade.stopMovedToBreakEven || trade.tpFilled[0]) && market.atr && r >= minTrailR;
      if (canTrail) {
        const stage = trade.trailStage || 0;
        const trailAtrMult = trailAtrMultiples[Math.min(stage, trailAtrMultiples.length - 1)] || 1.0;
        const trail = isLong
          ? price - market.atr * trailAtrMult
          : price + market.atr * trailAtrMult;
        const better = trade.trailingStop == null
          ? true
          : (isLong ? trail > trade.trailingStop : trail < trade.trailingStop);
        if (better && (isLong ? trail > trade.entryPrice : trail < trade.entryPrice)) {
          return {
            action: 'MOVE_STOP',
            newStop: +trail.toFixed(6),
            reason: 'TRAIL',
            detail: `Trailing stop to ${trail.toFixed(4)} (stage ${stage}: ${trailAtrMult} ATR behind price) — locking in profit while letting runner extend`
          };
        }
      }

      // ── Momentum Exhaustion Exit ──
      if (this.config.momentumExitEnabled && market.rsi != null && r >= momentumExitMinR && trade.tpFilled[0]) {
        const exhausted = isLong
          ? market.rsi < (this.config.momentumExitRsiLong || 70)
          : market.rsi > (this.config.momentumExitRsiShort || 30);
        if (exhausted) {
          return {
            action: 'CLOSE_ALL',
            reason: 'MOMENTUM_EXIT',
            detail: `Momentum exhaustion at +${r.toFixed(2)}R: RSI ${market.rsi.toFixed(1)} crossed exhaustion threshold — banking remaining ${(trade.remainingFraction * 100).toFixed(0)}% runner`
          };
        }
      }

      // ── 4. Grace period: the ordinary post-entry retrace is not a signal ──
      if (age < gracePeriodMs) {
        hold.reasons.push(`Within the ${Math.round(gracePeriodMs / 60000) || 1}-minute grace window — only the hard stop applies while trade establishes itself`);
        return hold;
      }

      // ── 5. Structural invalidation: a CLOSE beyond the level, not a poke ──
      const candle = market.confirmedCandle;
      if (this.config.useStructuralExit && candle && trade.invalidation) {
        const brokeStructure = isLong
          ? candle.close < trade.invalidation
          : candle.close > trade.invalidation;
        if (brokeStructure) {
          return {
            action: 'CLOSE_ALL',
            reason: EXIT.STRUCTURE_BROKEN,
            detail: `Confirmed close at ${candle.close} is beyond the invalidation level ${trade.invalidation} the setup was built on — the premise is gone, not merely uncomfortable`
          };
        }
      }

      // ── 6. Thesis flip: must be strong, must persist, and must not be a profitable trade ──
      const opp = market.opposingSignal;
      const oppAgainstUs = opp && ((isLong && opp.direction === 'SHORT') || (!isLong && opp.direction === 'LONG'));
      if (oppAgainstUs && (opp.grade === 'A+' || opp.grade === 'A')) {
        trade.flipStreak = (trade.flipStreak || 0) + 1;
      } else {
        trade.flipStreak = 0;
      }
      if (trade.flipStreak >= this.config.thesisFlipConfirmations && !inProfit) {
        return {
          action: 'CLOSE_ALL',
          reason: EXIT.THESIS_FLIP,
          detail: `A grade-${opp.grade} opposing setup has persisted across ${trade.flipStreak} consecutive evaluations while this position is underwater (${r.toFixed(2)}R) — standing aside rather than defending a broken idea`
        };
      }
      if (trade.flipStreak >= this.config.thesisFlipConfirmations && inProfit) {
        hold.reasons.push(`Opposing signal present, but this position is +${r.toFixed(2)}R — tightening via the stop ladder instead of closing on an opinion`);
      }

      // ── 7. Time stop: dead trades release their risk budget ──
      if (market.confirmedCandle && trade.lastBarSeen !== market.confirmedCandle.start) {
        trade.lastBarSeen = market.confirmedCandle.start;
        trade.barsHeld++;
      }
      const isFreeOption = trade.stopMovedToBreakEven || trade.tpFilled[0];

      // Wall-clock scalp timeout (10-12 minutes max)
      if (isScalp && !isFreeOption && age >= (this.config.scalpTimeStopMaxMs || SCALP_DEFAULTS.timeStopMaxMs) && r < timeStopMinR) {
        return {
          action: 'CLOSE_ALL',
          reason: EXIT.TIME_STOP,
          detail: `Scalp time limit reached (${Math.round(age / 60000)}m elapsed) at only ${r.toFixed(2)}R — closing dead 5m/10m scalp to free margin`
        };
      }

      if (!isFreeOption && trade.barsHeld >= timeStopBars && r < timeStopMinR) {
        return {
          action: 'CLOSE_ALL',
          reason: EXIT.TIME_STOP,
          detail: `${trade.barsHeld} bars held and still only ${r.toFixed(2)}R — the ${isScalp ? 'scalp momentum' : 'move this setup predicted'} has not happened, freeing risk capital`
        };
      }

      hold.reasons.push(`Holding: ${r >= 0 ? '+' : ''}${r.toFixed(2)}R, structure intact, ${trade.barsHeld}/${timeStopBars} bars used`);
      return hold;
    }

    markTpFilled(trade, index, fillPrice) {
      trade.tpFilled[index] = true;
      const isLong = trade.side === 'Buy';
      const sliceR = ((isLong ? fillPrice - trade.entryPrice : trade.entryPrice - fillPrice) / trade.riskDist);
      const fractions = trade.isScalp ? (this.config.scalpScaleOutFractions || SCALP_DEFAULTS.scaleOutFractions) : this.config.scaleOutFractions;
      const fraction = fractions[Math.min(index, fractions.length - 1)];
      trade.realisedR += sliceR * fraction;
      trade.remainingFraction = Math.max(0, trade.remainingFraction - fraction);
      trade.exitLog.push({ type: 'TP', index, price: fillPrice, r: +sliceR.toFixed(2), fraction });
      // Advance trailing stop stage so the trail tightens after each TP
      const multipliers = trade.isScalp ? (this.config.scalpTrailAtrMultiples || SCALP_DEFAULTS.trailAtrMultiples) : (this.config.trailAtrMultiples || [2.0, 1.2, 0.8]);
      trade.trailStage = Math.min((trade.trailStage || 0) + 1, multipliers.length - 1);
      return trade;
    }

    markStopMoved(trade, newStop, reason) {
      trade.stopLoss = newStop;
      if (reason === 'BREAK_EVEN') trade.stopMovedToBreakEven = true;
      if (reason === 'TRAIL') trade.trailingStop = newStop;
      trade.exitLog.push({ type: 'STOP_MOVE', price: newStop, reason });
      return trade;
    }

    /** Final R for the whole trade, weighting each slice by the size it closed. */
    finalise(trade, exitPrice, reason) {
      const isLong = trade.side === 'Buy';
      const remainR = ((isLong ? exitPrice - trade.entryPrice : trade.entryPrice - exitPrice) / trade.riskDist);
      const totalR = trade.realisedR + remainR * trade.remainingFraction;
      trade.exitLog.push({ type: 'CLOSE', price: exitPrice, reason, r: +remainR.toFixed(2) });
      trade.finalR = +totalR.toFixed(3);
      trade.exitReason = reason;
      return trade;
    }

    /** Human-readable account of why a trade ended the way it did. */
    explain(trade) {
      const parts = [];
      parts.push(`${trade.setupName} ${trade.side === 'Buy' ? 'LONG' : 'SHORT'} ${trade.symbol} @ ${trade.entryPrice}, grade ${trade.grade} (${trade.score}/100).`);
      parts.push(`Risk ${trade.riskDist.toFixed(6)} to ${trade.stopLoss}, invalidation ${trade.invalidation}.`);
      if (trade.exitLog.length) {
        parts.push('Path: ' + trade.exitLog.map(e => {
          if (e.type === 'TP') return `TP${e.index + 1} +${e.r}R on ${Math.round(e.fraction * 100)}%`;
          if (e.type === 'STOP_MOVE') return `stop→${e.reason === 'BREAK_EVEN' ? 'BE' : e.price}`;
          return `close ${e.reason} ${e.r >= 0 ? '+' : ''}${e.r}R`;
        }).join(' · '));
      }
      if (typeof trade.finalR === 'number') parts.push(`Net ${trade.finalR >= 0 ? '+' : ''}${trade.finalR}R.`);
      return parts.join(' ');
    }
  }

  return { PositionManager, DEFAULTS, SCALP_DEFAULTS, EXIT };
});

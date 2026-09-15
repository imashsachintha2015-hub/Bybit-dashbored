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
    trailAfterTp: 2,                  // start trailing once TP2 is banked
    trailAtrMultiple: 2.0,
    // Research §8.4: 50/50 Scaled Liquidity Exit Protocol.
    // TP1: 50% of position at first structural shelf (min 2:1 R:R), stop to BE.
    // TP2: remaining 50% trails along HTF swing levels for macro trend capture.
    scaleOutFractions: [0.50, 0.50],
    // Whether a confirmed close beyond the invalidation level closes the trade
    // ahead of the stop. Sounds obviously right; measured below.
    useStructuralExit: true
  };

  const EXIT = {
    STOP: 'STOP',
    TARGET: 'TARGET',
    STRUCTURE_BROKEN: 'STRUCTURE_BROKEN',
    THESIS_FLIP: 'THESIS_FLIP',
    TIME_STOP: 'TIME_STOP',
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
        tpFilled: [false, false],
        stopMovedToBreakEven: false,
        trailingStop: null,
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
     *                           opposingSignal: {direction, grade, score}|null }
     */
    evaluate(trade, market) {
      const hold = { action: 'HOLD', reasons: [] };
      if (!trade) return hold;

      const price = market.price;
      const r = this.currentR(trade, price);
      const age = this.now() - trade.openedAt;
      const inProfit = r > 0.05;

      // ── 1. Emergency: the feed itself is untrustworthy ──
      if (market.dataValid === false) {
        return { action: 'CLOSE_ALL', reason: EXIT.EMERGENCY, detail: 'Market data failed validation — flattening rather than managing a position blind' };
      }
      if (market.spreadPct != null && market.spreadPct > 0.35) {
        return { action: 'CLOSE_ALL', reason: EXIT.EMERGENCY, detail: `Spread blew out to ${market.spreadPct.toFixed(3)}% — liquidity has gone, exiting before it gets worse` };
      }

      // ── 2. Targets: bank profit in planned slices ──
      const isLong = trade.side === 'Buy';
      for (let i = 0; i < trade.targets.length; i++) {
        if (trade.tpFilled[i]) continue;
        const level = trade.targets[i];
        const reached = isLong ? price >= level : price <= level;
        if (!reached) break; // ordered near-to-far; never skip ahead
        return {
          action: 'SCALE_OUT',
          tpIndex: i,
          fraction: this.config.scaleOutFractions[i],
          reason: EXIT.TARGET,
          detail: `TP${i + 1} reached at ${level} (${r.toFixed(2)}R) — banking ${Math.round(this.config.scaleOutFractions[i] * 100)}% of the original size`
        };
      }

      // ── 3. Stop management: protect what is already made ──
      // This is the inverse of the old behaviour. Rather than closing a trade
      // because it wobbled, raise the floor under it and let it work.
      if (trade.tpFilled[this.config.breakEvenAfterTp - 1] && !trade.stopMovedToBreakEven) {
        return {
          action: 'MOVE_STOP',
          newStop: trade.entryPrice,
          reason: 'BREAK_EVEN',
          detail: `TP${this.config.breakEvenAfterTp} banked — stop to break-even at ${trade.entryPrice}. The remainder is now a free option; it will not be cut for wobbling.`
        };
      }
      if (trade.tpFilled[this.config.trailAfterTp - 1] && market.atr) {
        const trail = isLong
          ? price - market.atr * this.config.trailAtrMultiple
          : price + market.atr * this.config.trailAtrMultiple;
        const better = trade.trailingStop == null
          ? true
          : (isLong ? trail > trade.trailingStop : trail < trade.trailingStop);
        if (better && (isLong ? trail > trade.entryPrice : trail < trade.entryPrice)) {
          return {
            action: 'MOVE_STOP',
            newStop: +trail.toFixed(6),
            reason: 'TRAIL',
            detail: `Trailing stop to ${trail.toFixed(4)} (${this.config.trailAtrMultiple} ATR behind price) — runner protected, still free to extend`
          };
        }
      }

      // ── 4. Grace period: the ordinary post-entry retrace is not a signal ──
      // Almost every entry goes slightly against you first. The old guardian
      // treated that universal fact as an invalidation.
      if (age < this.config.gracePeriodMs) {
        hold.reasons.push(`Within the ${Math.round(this.config.gracePeriodMs / 60000)}-minute grace window — only the hard stop applies while the trade is establishing itself`);
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

      // ── 6. Thesis flip: must be strong, must persist, and must not be a
      // profitable trade. A winner never gets cut on an opinion. ──
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
      // Once TP1 is banked and the stop is at break-even, the remainder costs
      // nothing to hold: its worst case is zero. Retiring it on a clock throws
      // away the only part of the distribution that pays for the losers, and
      // the first cut of this did exactly that — the backtest showed the time
      // stop closing two thirds of all trades, most of them still alive.
      const isFreeOption = trade.stopMovedToBreakEven || trade.tpFilled[0];
      if (!isFreeOption && trade.barsHeld >= this.config.timeStopBars && r < this.config.timeStopMinR) {
        return {
          action: 'CLOSE_ALL',
          reason: EXIT.TIME_STOP,
          detail: `${trade.barsHeld} bars held and still only ${r.toFixed(2)}R — the move this setup predicted has not happened, so the risk is better deployed elsewhere`
        };
      }

      hold.reasons.push(`Holding: ${r >= 0 ? '+' : ''}${r.toFixed(2)}R, structure intact, ${trade.barsHeld}/${this.config.timeStopBars} bars used`);
      return hold;
    }

    markTpFilled(trade, index, fillPrice) {
      trade.tpFilled[index] = true;
      const isLong = trade.side === 'Buy';
      const sliceR = ((isLong ? fillPrice - trade.entryPrice : trade.entryPrice - fillPrice) / trade.riskDist);
      const fraction = this.config.scaleOutFractions[index];
      trade.realisedR += sliceR * fraction;
      trade.remainingFraction = Math.max(0, trade.remainingFraction - fraction);
      trade.exitLog.push({ type: 'TP', index, price: fillPrice, r: +sliceR.toFixed(2), fraction });
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

  return { PositionManager, DEFAULTS, EXIT };
});

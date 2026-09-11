/**
 * DIV-11 — Whale Flow Tracker.
 *
 * Monitors the public trade tape for large-notional taker orders and maintains
 * a rolling buy/sell pressure picture the confluence gate consults.
 *
 * Two things changed from V2:
 *
 *  1. THE THRESHOLD ADAPTS TO THE SYMBOL. A flat $50,000 floor meant "whale"
 *     described completely different things on different books: on BTC it is an
 *     ordinary print that occurs many times a minute, on DOGE it is a genuinely
 *     rare event. So the same "dominant whale side" reading carried strong
 *     evidence on one symbol and near-noise on another, while the gate treated
 *     them identically. The floor is now a rolling percentile of the symbol's
 *     own recent print sizes, with a sane absolute minimum.
 *
 *  2. A DOMINANT SIDE IS ONLY REPORTED WHEN THERE IS ENOUGH OF IT. V2 would
 *     report BUY or SELL dominance off a single print, and the flip of that one
 *     reading could close an open position through the old guardian.
 */

class WhaleTracker {
  constructor(options = {}) {
    this.symbol = options.symbol || '';
    this.absoluteFloorUsd = options.absoluteFloorUsd || 25000;
    this.thresholdUsd = options.thresholdUsd || this.absoluteFloorUsd;
    this.windowMs = options.windowMs || 5 * 60 * 1000;
    this.maxTracked = options.maxTracked || 60;
    this.minCountForDominance = options.minCountForDominance || 4;

    this.whaleTrades = [];
    this.recentNotionals = [];   // rolling sample of ALL prints, for the percentile
  }

  /** Recomputes the "large print" floor as the 99th percentile of this symbol's
   * own recent prints, never below the absolute floor. */
  recomputeThreshold() {
    if (this.recentNotionals.length < 200) return;
    const sorted = this.recentNotionals.slice().sort((a, b) => a - b);
    const p99 = sorted[Math.floor(sorted.length * 0.99)];
    this.thresholdUsd = Math.max(this.absoluteFloorUsd, p99);
  }

  ingest(trades) {
    if (!Array.isArray(trades)) return;
    const now = Date.now();

    for (const t of trades) {
      const price = parseFloat(t.p || 0);
      const size = parseFloat(t.v || 0);
      if (!price || !size) continue;
      const notional = price * size;

      this.recentNotionals.push(notional);
      if (this.recentNotionals.length > 3000) this.recentNotionals.splice(0, this.recentNotionals.length - 3000);

      if (notional < this.thresholdUsd) continue;
      this.whaleTrades.push({
        time: parseInt(t.T) || now,
        side: t.S === 'Buy' ? 'BUY' : 'SELL',
        price, size, notional
      });
    }

    this.recomputeThreshold();
    this.prune(now);
  }

  prune(now) {
    now = now || Date.now();
    const cutoff = now - this.windowMs;
    this.whaleTrades = this.whaleTrades.filter(w => w.time >= cutoff);
    if (this.whaleTrades.length > this.maxTracked) {
      this.whaleTrades = this.whaleTrades.slice(this.whaleTrades.length - this.maxTracked);
    }
  }

  getSummary() {
    this.prune();
    let buyUsd = 0, sellUsd = 0;
    for (const w of this.whaleTrades) {
      if (w.side === 'BUY') buyUsd += w.notional; else sellUsd += w.notional;
    }
    const total = buyUsd + sellUsd;
    const count = this.whaleTrades.length;

    // Dominance needs both a lopsided split AND enough independent prints to
    // make the split mean something. One large order is one actor's decision,
    // not a flow.
    let dominantSide = 'NEUTRAL';
    if (total > 0 && count >= this.minCountForDominance) {
      const buyShare = buyUsd / total;
      if (buyShare >= 0.62) dominantSide = 'BUY';
      else if (buyShare <= 0.38) dominantSide = 'SELL';
    }

    return {
      whaleCount: count,
      thresholdUsd: Math.round(this.thresholdUsd),
      whaleBuyUsd: buyUsd,
      whaleSellUsd: sellUsd,
      netDeltaUsd: buyUsd - sellUsd,
      dominantSide,
      confirmed: count >= this.minCountForDominance,
      pressurePct: total > 0 ? Math.round((buyUsd / total) * 100) : 50,
      largestTrades: this.whaleTrades.slice().sort((a, b) => b.notional - a.notional).slice(0, 8)
    };
  }
}

if (typeof window !== 'undefined') window.WhaleTracker = WhaleTracker;
if (typeof module === 'object' && module.exports) module.exports = { WhaleTracker };

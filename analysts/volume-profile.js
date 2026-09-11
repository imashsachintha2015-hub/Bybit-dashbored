/**
 * ANALYST — Volume Profile.
 *
 * Where did business actually get done? Time-based charts weight every minute
 * equally, but the market does not: most of the volume trades in a narrow band,
 * and that band is where the auction found agreement. Price returns to it, and
 * accelerates away from where it found none.
 *
 * Built from candle data by distributing each bar's volume across its range —
 * an approximation of a true tick profile, but a well-established one that
 * captures what matters.
 *
 * What it produces:
 *  - POC (point of control): the single most-traded price. The strongest magnet
 *    on the chart and the level price mean-reverts toward in a balanced market.
 *  - Value area (70% of volume): the region of agreement. Entering against the
 *    value area edge is a very different trade from entering in its middle.
 *  - LVN (low volume nodes): prices the auction rejected quickly. Price tends to
 *    move THROUGH these fast — which makes them poor targets and good stop
 *    locations, the opposite of how most systems treat them.
 *  - HVN (high volume nodes): prices the auction accepted. Moves stall here.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnalystVolumeProfile = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  function buildProfile(candles, bins = 60) {
    if (!candles.length) return null;
    const hi = Math.max(...candles.map(c => c.high));
    const lo = Math.min(...candles.map(c => c.low));
    const range = hi - lo;
    if (range <= 0) return null;
    const binSize = range / bins;
    const hist = new Array(bins).fill(0);

    for (const c of candles) {
      const barRange = c.high - c.low;
      if (barRange <= 0) {
        const idx = Math.min(bins - 1, Math.max(0, Math.floor((c.close - lo) / binSize)));
        hist[idx] += c.volume;
        continue;
      }
      // Spread the bar's volume evenly across the prices it traded. Cruder than
      // a tick profile, standard practice when only bars are available.
      const startBin = Math.max(0, Math.floor((c.low - lo) / binSize));
      const endBin = Math.min(bins - 1, Math.floor((c.high - lo) / binSize));
      const spread = endBin - startBin + 1;
      const per = c.volume / spread;
      for (let b = startBin; b <= endBin; b++) hist[b] += per;
    }

    const totalVol = hist.reduce((a, b) => a + b, 0);
    let pocBin = 0;
    for (let i = 1; i < bins; i++) if (hist[i] > hist[pocBin]) pocBin = i;

    // Value area: expand outward from the POC until 70% of volume is enclosed.
    let included = hist[pocBin];
    let lower = pocBin, upper = pocBin;
    while (included < totalVol * 0.7 && (lower > 0 || upper < bins - 1)) {
      const below = lower > 0 ? hist[lower - 1] : -1;
      const above = upper < bins - 1 ? hist[upper + 1] : -1;
      if (above >= below) { upper++; included += hist[upper]; }
      else { lower--; included += hist[lower]; }
    }

    const priceOf = b => lo + (b + 0.5) * binSize;
    const avg = totalVol / bins;

    const lvn = [], hvn = [];
    for (let i = 1; i < bins - 1; i++) {
      if (hist[i] < avg * 0.45 && hist[i] < hist[i - 1] && hist[i] < hist[i + 1]) lvn.push({ price: priceOf(i), volume: hist[i] });
      if (hist[i] > avg * 1.7 && hist[i] > hist[i - 1] && hist[i] > hist[i + 1]) hvn.push({ price: priceOf(i), volume: hist[i] });
    }

    return {
      poc: priceOf(pocBin),
      vah: priceOf(upper),
      val: priceOf(lower),
      high: hi, low: lo, binSize,
      lvn: lvn.sort((a, b) => a.volume - b.volume).slice(0, 5),
      hvn: hvn.sort((a, b) => b.volume - a.volume).slice(0, 5),
      totalVol
    };
  }

  function analyse(ctx) {
    const { mtf, price, atrMtf } = ctx;
    const none = { id: 'volumeProfile', name: 'Volume Profile', read: 'NEUTRAL', conviction: 0,
                   evidence: ['Insufficient history to build a profile'], levels: [], vetoes: [], facts: {} };
    if (!mtf || mtf.length < 60 || !price) return none;

    // Two horizons: the developing session and the broader composite.
    const session = buildProfile(mtf.slice(-96), 50);
    const composite = buildProfile(mtf.slice(-300), 70);
    if (!session || !composite) return none;

    const evidence = [];
    const vetoes = [];
    const levels = [];
    let read = 'NEUTRAL';
    let conviction = 0;

    const inValue = price >= session.val && price <= session.vah;
    const distToPocAtr = atrMtf ? Math.abs(price - session.poc) / atrMtf : 0;

    evidence.push(`Session value area ${session.val.toFixed(4)}–${session.vah.toFixed(4)}, point of control ${session.poc.toFixed(4)} (${distToPocAtr.toFixed(2)} ATR from price)`);
    evidence.push(inValue
      ? 'Price is INSIDE value — the auction is balanced here, which favours rotation between the edges over directional continuation'
      : `Price is OUTSIDE value, ${price > session.vah ? 'above the high' : 'below the low'} — either acceptance is being established at a new level, or this is an excursion that reverts to the point of control`);

    levels.push({ price: session.poc, kind: 'POC' }, { price: session.vah, kind: 'VAH' }, { price: session.val, kind: 'VAL' });
    for (const h of composite.hvn.slice(0, 2)) levels.push({ price: h.price, kind: 'HVN' });
    for (const l of session.lvn.slice(0, 2)) levels.push({ price: l.price, kind: 'LVN' });

    const concerns = [];
    if (inValue) {
      // Inside value is a real observation, but it is not fatal and it is not
      // even a negative in a trending market — a trend spends most of its life
      // inside a value area that is itself migrating. Making this a veto (as
      // the first cut did) blocked trades in exactly the regime they work in.
      const inTrend = ctx.regime === 'TREND';
      if (!inTrend) {
        concerns.push({ side: null, weight: 0.35,
          note: 'Price is inside the value area of a non-trending auction — directional entries here pay both edges of the range to find out which way it breaks' });
      }
      conviction = 0.3;
    } else {
      // Outside value, the base case is reversion to the point of control —
      // BUT only where reversion is the right model at all. In a trending
      // auction the value area migrates with price, so "outside value" is the
      // normal condition of a trend, not evidence against it. This analyst
      // therefore abstains from a directional vote in TREND rather than voting
      // against the trend, which is what it was doing.
      const trending = ctx.regime === 'TREND';
      if (trending) {
        read = 'NEUTRAL';
        conviction = 0;
        evidence.push('Regime is trending, so value is migrating with price. Reversion to the point of control is the wrong model here and this analyst abstains from a directional call rather than fading a trend.');
      } else {
        read = price > session.vah ? 'BEARISH' : 'BULLISH';
        conviction = Math.min(0.6, 0.25 + distToPocAtr * 0.15);
        evidence.push(`Untested point of control at ${session.poc.toFixed(4)} is the natural magnet from outside value in a balanced auction`);
      }
    }

    // An LVN directly in the path is a warning: price crosses those quickly,
    // so a target placed inside one rarely fills and a stop placed inside one
    // gets run through.
    const nearestLvn = session.lvn
      .map(l => ({ ...l, dist: Math.abs(l.price - price) }))
      .sort((a, b) => a.dist - b.dist)[0];
    if (nearestLvn && atrMtf && nearestLvn.dist / atrMtf < 0.5) {
      evidence.push(`Low-volume node at ${nearestLvn.price.toFixed(4)} sits right at price — the auction rejected this level quickly before, so expect fast travel through it rather than a reaction at it`);
    }

    return {
      id: 'volumeProfile', name: 'Volume Profile', read, conviction,
      evidence, levels, vetoes, concerns,
      facts: {
        poc: session.poc, vah: session.vah, val: session.val, inValue,
        compositePoc: composite.poc,
        distToPocAtr: +distToPocAtr.toFixed(2),
        lvnCount: session.lvn.length, hvnCount: composite.hvn.length
      }
    };
  }

  return { analyse, buildProfile };
});

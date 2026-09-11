/**
 * SWARM — Analyst Panel.
 *
 * Runs every specialist independently over the same market snapshot and
 * collects their reads. Independence is the point: each analyst sees the raw
 * market, not the others' conclusions, so their agreement carries information.
 * If they were chained, agreement would only mean the first one was persuasive.
 *
 * The one deliberate exception is that Trap Forensics receives the Liquidity
 * Cartographer's pools, because a trap is by definition a trap against a
 * specific pool of liquidity — the concept does not exist without it.
 */
(function (root, factory) {
  const deps = typeof require === 'function'
    ? {
        liquidity: require('../analysts/liquidity-map.js'),
        traps: require('../analysts/traps.js'),
        derivatives: require('../analysts/derivatives.js'),
        structure: require('../analysts/structure.js'),
        volumeProfile: require('../analysts/volume-profile.js'),
        manipulation: require('../analysts/manipulation.js'),
        sessions: require('../analysts/sessions.js')
      }
    : {
        liquidity: root.AnalystLiquidityMap,
        traps: root.AnalystTraps,
        derivatives: root.AnalystDerivatives,
        structure: root.AnalystStructure,
        volumeProfile: root.AnalystVolumeProfile,
        manipulation: root.AnalystManipulation,
        sessions: root.AnalystSessions
      };
  const api = factory(deps);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SwarmPanel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (A) {

  /** A read that failed to produce anything, rather than a silent omission.
   * An analyst that cannot see is different from an analyst that sees nothing. */
  function blank(id, name, why) {
    return { id, name, read: 'UNAVAILABLE', conviction: 0, evidence: [why], levels: [], vetoes: [], facts: {} };
  }

  function run(ctx) {
    const reads = [];
    const safe = (id, name, fn) => {
      try {
        const r = fn();
        reads.push(r || blank(id, name, 'Analyst returned nothing'));
      } catch (e) {
        reads.push(blank(id, name, `Analyst errored: ${e.message}`));
      }
    };

    // Liquidity first — its pools are an input to trap detection.
    safe('liquidity', 'Liquidity Cartographer', () => A.liquidity.analyse(ctx));
    const liq = reads[0];
    const trapCtx = Object.assign({}, ctx, { liquidityPools: liq.levels || [] });

    safe('traps', 'Trap Forensics', () => A.traps.analyse(trapCtx));
    safe('structure', 'Market Structure', () => A.structure.analyse(ctx));
    safe('volumeProfile', 'Volume Profile', () => A.volumeProfile.analyse(ctx));
    safe('derivatives', 'Positioning & Derivatives', () => A.derivatives.analyse(ctx));
    safe('manipulation', 'Manipulation Forensics', () => A.manipulation.analyse(ctx));
    safe('sessions', 'Session & Time', () => A.sessions.analyse(ctx));

    return reads;
  }

  return { run };
});

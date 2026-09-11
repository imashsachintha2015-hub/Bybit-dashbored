/**
 * SWARM — Debate & Red Team.
 *
 * This is what makes the panel a swarm rather than a list. Collecting seven
 * opinions and averaging them is not deliberation; it is the same
 * count-the-bullets mistake V2 made, with better vocabulary.
 *
 * The process here has four stages, and a thesis has to survive all of them:
 *
 *  1. WEIGHTED VOTE. Each analyst contributes conviction × reliability weight.
 *     Weights come from the meta-learner, which tracks how often each analyst
 *     has actually been right IN THIS REGIME — a positioning read that is
 *     excellent in a trend and useless in chop should not carry the same weight
 *     in both.
 *
 *  2. RED TEAM. The provisional thesis is handed to an adversary whose only job
 *     is to argue against it, using the dissenting analysts' own evidence plus
 *     every veto that names this direction. It produces an opposition score.
 *     A thesis that cannot outweigh its own best counter-argument is not traded.
 *
 *  3. HARD VETOES. Some objections are not weighed at all, they are fatal —
 *     entering directly into a dense untapped liquidity shelf, joining a
 *     crowded and well-paid position that price is refusing to reward, or
 *     leaning on a level that has been shown to be spoofed. These cannot be
 *     outvoted by enthusiasm elsewhere. V2 had nothing like this; its
 *     "adversarial engine" could be overridden by three weak confirmations.
 *
 *  4. DISSENT PENALTY. Even when nothing is fatal, a thesis that several
 *     analysts actively contradict gets its conviction cut. Confident
 *     disagreement among specialists is information, and the correct response
 *     to it is a smaller opinion, not a louder one.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SwarmDebate = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  const BULLISH = ['BULLISH', 'DRAW_UP'];
  const BEARISH = ['BEARISH', 'DRAW_DOWN'];

  function sideOf(read) {
    if (BULLISH.includes(read)) return 'LONG';
    if (BEARISH.includes(read)) return 'SHORT';
    return null;
  }

  /** Which direction does this veto forbid? Returns 'LONG', 'SHORT' or null
   * (null = forbids both / is a general hazard). */
  function vetoTarget(veto) {
    const v = veto.toUpperCase();
    if (v.startsWith('LONG_INTO_LIQUIDITY') || v.startsWith('CROWDED_LONG') ||
        v.startsWith('BUYING_PREMIUM') || v.startsWith('FRESH_BULL_TRAP') ||
        v.startsWith('HOLLOW_RALLY') || v.startsWith('VACUUM_MOVE_UP') ||
        v.startsWith('RECENT_IGNITION_UP') || v.startsWith('SPOOFED_BID')) return 'LONG';
    if (v.startsWith('SHORT_INTO_LIQUIDITY') || v.startsWith('CROWDED_SHORT') ||
        v.startsWith('SELLING_DISCOUNT') || v.startsWith('FRESH_BEAR_TRAP') ||
        v.startsWith('VACUUM_MOVE_DOWN') || v.startsWith('RECENT_IGNITION_DOWN') ||
        v.startsWith('SPOOFED_ASK')) return 'SHORT';
    return null;
  }

  /** Vetoes that end the discussion rather than joining it. */
  const FATAL = ['LONG_INTO_LIQUIDITY', 'SHORT_INTO_LIQUIDITY', 'CROWDED_LONG', 'CROWDED_SHORT',
                 'SPOOFED_BID', 'SPOOFED_ASK', 'FRESH_BULL_TRAP', 'FRESH_BEAR_TRAP'];

  function isFatal(veto) {
    const tag = veto.split(':')[0].trim().toUpperCase();
    return FATAL.includes(tag);
  }

  /**
   * @param {Array} reads    analyst reads from the panel
   * @param {Object} weights analystId -> reliability weight (default 1)
   * @param {Object} opts    { dissentPenalty, minNetConviction }
   */
  function deliberate(reads, weights = {}, opts = {}) {
    // A thesis must outweigh its counter-case, but the bar is set so that
    // ordinary disagreement narrows the result rather than killing it. Only
    // fatal vetoes kill.
    const dissentPenalty = opts.dissentPenalty != null ? opts.dissentPenalty : 0.45;
    const minNet = opts.minNetConviction != null ? opts.minNetConviction : 0.15;

    // ── 1. Weighted vote ──
    let longScore = 0, shortScore = 0;
    const supporters = { LONG: [], SHORT: [] };
    for (const r of reads) {
      const side = sideOf(r.read);
      if (!side || !r.conviction) continue;
      const w = weights[r.id] != null ? weights[r.id] : 1;
      const contribution = r.conviction * w;
      if (side === 'LONG') { longScore += contribution; supporters.LONG.push({ id: r.id, name: r.name, contribution: +contribution.toFixed(3) }); }
      else { shortScore += contribution; supporters.SHORT.push({ id: r.id, name: r.name, contribution: +contribution.toFixed(3) }); }
    }

    const total = longScore + shortScore;
    let thesis = null;
    if (total > 0) thesis = longScore > shortScore ? 'LONG' : (shortScore > longScore ? 'SHORT' : null);

    const transcript = [];
    transcript.push(`Vote: LONG ${longScore.toFixed(2)} vs SHORT ${shortScore.toFixed(2)} across ${reads.filter(r => sideOf(r.read)).length} analysts with an opinion.`);

    if (!thesis) {
      return {
        thesis: null, conviction: 0, blocked: true,
        blockers: ['No directional consensus among the panel'],
        transcript, supporters, opposition: [], fatalVetoes: [],
        longScore: +longScore.toFixed(3), shortScore: +shortScore.toFixed(3)
      };
    }

    // ── 2. Red team ──
    // The adversary's case is built from the dissenting analysts' evidence and
    // from every veto that names this direction. Both count; a veto from an
    // analyst who otherwise agrees with the thesis is especially telling.
    const opposition = [];
    const opposingSide = thesis === 'LONG' ? 'SHORT' : 'LONG';
    const opposingScore = thesis === 'LONG' ? shortScore : longScore;
    const supportScore = thesis === 'LONG' ? longScore : shortScore;

    for (const r of reads) {
      if (sideOf(r.read) === opposingSide && r.conviction > 0) {
        opposition.push({
          source: r.name, weight: +(r.conviction * (weights[r.id] != null ? weights[r.id] : 1)).toFixed(3),
          argument: r.evidence[0] || `${r.name} reads the opposite direction`
        });
      }
      if (r.read === 'DANGER' && r.conviction > 0) {
        opposition.push({ source: r.name, weight: +(r.conviction * 0.8).toFixed(3), argument: r.evidence[0] });
      }
    }

    const applicableVetoes = [];
    const fatalVetoes = [];
    for (const r of reads) {
      for (const v of (r.vetoes || [])) {
        const target = vetoTarget(v);
        if (target && target !== thesis) continue;   // forbids the other side; irrelevant here
        applicableVetoes.push({ source: r.name, veto: v, fatal: isFatal(v) });
        if (isFatal(v)) fatalVetoes.push({ source: r.name, veto: v });
        else opposition.push({ source: r.name, weight: 0.3, argument: v });
      }
    }

    // ── Concerns ──
    // Graded objections that reduce confidence without arguing the other side.
    // Keeping them out of the opposition sum matters: a concern is a reason to
    // trade smaller, an opposing read is a reason not to trade. Conflating the
    // two (the first cut of this did) made near-universal observations like
    // "price is in the upper half of its range" fatal, and nothing survived.
    const concerns = [];
    let confidenceScale = 1;
    for (const r of reads) {
      for (const c of (r.concerns || [])) {
        if (c.side && c.side !== thesis) continue;   // concerns the other direction
        concerns.push({ source: r.name, note: c.note, weight: c.weight });
      }
      if (typeof r.confidenceScale === 'number') confidenceScale *= r.confidenceScale;
    }
    const concernWeight = concerns.reduce((a, c) => a + c.weight, 0);
    const oppositionWeight = opposition.reduce((a, o) => a + o.weight, 0);
    transcript.push(`Red team assembled ${opposition.length} counter-argument(s) worth ${oppositionWeight.toFixed(2)} against a supporting case of ${supportScore.toFixed(2)}.`);

    // ── 3. Fatal vetoes ──
    if (fatalVetoes.length) {
      transcript.push(`FATAL: ${fatalVetoes.length} objection(s) cannot be outvoted — the thesis is abandoned regardless of support.`);
      return {
        thesis: null, rejectedThesis: thesis, conviction: 0, blocked: true,
        blockers: fatalVetoes.map(f => `${f.source}: ${f.veto}`),
        transcript, supporters, opposition, concerns, fatalVetoes, applicableVetoes,
        longScore: +longScore.toFixed(3), shortScore: +shortScore.toFixed(3)
      };
    }

    // ── 4. Net conviction after dissent ──
    const net = supportScore - oppositionWeight * dissentPenalty;
    const denom = supportScore + oppositionWeight;
    let conviction = denom > 0 ? Math.max(0, net / denom) : 0;

    // Concerns scale the result down; they never flip it. Same for the session
    // confidence factor — a good setup in a thin book is a smaller version of a
    // good setup, not a bad one.
    const concernFactor = 1 / (1 + concernWeight * 0.5);
    conviction = conviction * concernFactor * Math.min(1.15, confidenceScale);

    transcript.push(`Support ${supportScore.toFixed(2)} vs opposition ${oppositionWeight.toFixed(2)}; ${concerns.length} concern(s) worth ${concernWeight.toFixed(2)} scale it by ${concernFactor.toFixed(2)}${confidenceScale !== 1 ? `, session factor ${confidenceScale.toFixed(2)}` : ''}.`);
    transcript.push(`Net conviction ${conviction.toFixed(3)} against a floor of ${minNet}.`);

    const blocked = conviction < minNet;
    if (blocked) transcript.push('The thesis did not survive its own counter-argument — standing down.');

    return {
      thesis: blocked ? null : thesis,
      rejectedThesis: blocked ? thesis : null,
      conviction: +conviction.toFixed(3),
      blocked,
      blockers: blocked ? [`Thesis ${thesis} could not outweigh the panel's counter-case (${conviction.toFixed(2)} < ${minNet})`] : [],
      transcript, supporters, opposition, concerns,
      applicableVetoes, fatalVetoes,
      concernWeight: +concernWeight.toFixed(3),
      confidenceScale: +confidenceScale.toFixed(3),
      longScore: +longScore.toFixed(3), shortScore: +shortScore.toFixed(3),
      oppositionWeight: +oppositionWeight.toFixed(3)
    };
  }

  return { deliberate, sideOf, vetoTarget, isFatal, FATAL };
});

# Novel, indicator-free signals (1H x 12 coins x 365d; 15m x 19 coins x 120d; 14 bps cost)
Families: shock fade/continue (big-body, high-volume bar), thin-move fade, absorption (big volume, tiny range), 5-bar run exhaustion,
BTC->alt lead-lag, 4-bar BTC-beta-residual reversion/continuation, hour-of-day drift, data-derived 3-bar candle-alphabet n-grams.
Protocol: dev 50% / val 25%; ~330 event x horizon tests. Result: 0 with dev t>=2.5; none positive net on val.
Only stable-sign effect: 15m bar reversal (+5..+15 bps gross at 1-2h, same sign in all three thirds) -- below the 14 bps cost, no dose-response with |z|.
n-grams: best dev |t| equals the chance maximum; 1H val gross +9 bps (< cost), 15m ~0.

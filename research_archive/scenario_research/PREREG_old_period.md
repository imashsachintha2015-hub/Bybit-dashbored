# Pre-registered tests on 2021-06-01 .. 2024-04-08 (OKX 1H), written 2026-10-08T12:36Z BEFORE that data was loaded
None of this period was used to design or select anything. Each line is ONE test, rules frozen as below.
Pass = avg net R > 0 with t >= 2.0 (Bonferroni-strict for 6 tests: t >= 2.6). Costs: 14 bps market / 8 bps limit + funding.
1. CMEGAP_fill_g0.01_s1.0 | coin 1D trend against | BTC 1D aligned | target = fill (dev-selected config, round 2)
2. Weekend gap fill, no filters, gap >= 2%, stop 2x gap, target fill, 120h max (best unfiltered row seen in exploratory 2024-26 data)
3. Weekend vs placebo fill rate: weekend gaps (>= 1%) fill within 24h more often than mid-week windows (Tue->Thu)  [descriptive test]
4. AMD_NO_GATE (backend_lib/amd_fvg.py, frozen), all coins, both sides
5. PWL_SWEEP_RECLAIM | coin 1D trend with | BTC 1D aligned | target previous week high (round-2 lead)
6. AMD_PRIMARY (frozen) for reference

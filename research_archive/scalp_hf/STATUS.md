# Status (work in progress)
First run found a numerical bug in the rolling statistics (an infinity from a zero-volume outage poisoned the VWAP family). Fixed in `hf_core.py` / `hf_fam.py`, tested against brute force in `test_core.py`; the first-run outputs are in `first_run_bug/`.
Done after the fix: Stage 1 on DEV (`stage1_dev_out.txt`: 0 of 262 cells pass the screen). The ladder and its parity check do not use the affected functions (`ladder_*_out.txt`, `parity_ladder_out.txt`).
Still to run with the fixed code: Stage 2 selection on DEV, then the frozen rules on VAL, FINAL, OLD and the unseen coins, then the README.

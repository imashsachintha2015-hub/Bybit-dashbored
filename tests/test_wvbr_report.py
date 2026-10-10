"""The WVBR report as data (daemons/forward_wvbr.summary, served at /api/wvbr/report): counts and group statistics from a synthetic ledger."""
import json, os, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from daemons import forward_wvbr as W


def ex(R, gate, t):
    return dict(type="exit", t=t, R=R, gate_ok=gate, ret_pct=R * 1.2, mfe_R=abs(R) + 0.5, mae_R=-0.4, symbol="SOLUSDT")


class WvbrReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.old = (W.LOG_P, W.SBGZ_LOG_P)
        W.LOG_P = os.path.join(self.tmp, "wvbr_log.jsonl"); W.SBGZ_LOG_P = os.path.join(self.tmp, "sbgz_gate_log.jsonl")

    def tearDown(self): W.LOG_P, W.SBGZ_LOG_P = self.old

    def test_empty_ledger(self):
        s = W.summary()
        self.assertFalse(s["ledger_exists"]); self.assertEqual((s["signals"], s["closed"]), (0, 0))
        self.assertIsNone(s["groups"]["all"]["avg_r"]); self.assertIsNone(s["last_event_t"])

    def test_counts_and_groups(self):
        ev = [dict(type="signal", t=1), dict(type="signal", t=2), dict(type="signal", t=3), dict(type="fill", t=4), dict(type="fill", t=5), dict(type="skipped_busy", t=6),
              ex(3.0, True, 10), ex(-1.0, True, 11), ex(-1.0, False, 12), dict(type="error", t=13, what="X", msg="m")]
        with open(W.LOG_P, "w") as f: f.write("\n".join(json.dumps(e) for e in ev))
        with open(W.SBGZ_LOG_P, "w") as f: f.write("\n".join(json.dumps(e) for e in [dict(type="sbgz_setup", sbgz_gate_ok=True), dict(type="sbgz_setup", sbgz_gate_ok=False)]))
        s = W.summary()
        self.assertEqual((s["signals"], s["fills"], s["closed"], s["skipped_busy"], s["errors"]), (3, 2, 3, 1, 1))
        self.assertEqual(s["groups"]["all"]["n"], 3); self.assertAlmostEqual(s["groups"]["all"]["avg_r"], 1 / 3)
        self.assertEqual(s["groups"]["gate_aligned"]["n"], 2); self.assertAlmostEqual(s["groups"]["gate_aligned"]["avg_r"], 1.0); self.assertAlmostEqual(s["groups"]["gate_aligned"]["win_rate"], 50.0)
        self.assertEqual(s["groups"]["against_gate"]["n"], 1); self.assertAlmostEqual(s["groups"]["against_gate"]["total_r"], -1.0)
        self.assertEqual(s["sbgz_gate"], dict(setups=2, pass_gate=1)); self.assertEqual(s["last_event_t"], 13)
        self.assertIn("WVBR-v1: 3 signals", s["text"])


if __name__ == "__main__":
    unittest.main()

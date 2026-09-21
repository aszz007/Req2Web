"""CPU-only watchdog decision tests; no Linux process claim."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from phase7_e2_supervise import watch_reason


class Watchdog(unittest.TestCase):
    def test_startup_session_loading_and_budget(self):
        self.assertIsNone(watch_reason(None,100,0,900))
        self.assertEqual(watch_reason(None,181,0,900),"startup_deadline")
        self.assertEqual(watch_reason({"phase":"session","deadline_unix":120},120,0,900),"session_deadline")
        self.assertEqual(watch_reason({"phase":"loading","deadline_unix":180},180,0,900),"loading_deadline")
        self.assertEqual(watch_reason({"phase":"idle"},901,0,900),"batch_or_rental_time_cap")

    def test_missing_deadline_not_success(self):
        self.assertEqual(watch_reason({"phase":"session"},100,0,900),"missing_deadline")
        self.assertEqual(watch_reason({"phase":"unknown"},100,0,900),"invalid_active_phase")


if __name__ == "__main__":unittest.main()

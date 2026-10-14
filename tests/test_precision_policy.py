import math
import unittest

from precision_policy import precision_gate_decision


class PrecisionGateDecisionTests(unittest.TestCase):
    def test_score_just_below_cap_passes_when_e_checks_pass(self):
        result = precision_gate_decision({"e_pass": True}, 64.999, True, 65)
        self.assertTrue(result["pass"])
        self.assertTrue(result["score_ok"])

    def test_score_equal_to_cap_is_rejected(self):
        result = precision_gate_decision({"e_pass": True}, 65, True, 65)
        self.assertFalse(result["pass"])
        self.assertFalse(result["score_ok"])

    def test_score_above_cap_is_rejected(self):
        result = precision_gate_decision({"e_pass": True}, 82.4, True, 65)
        self.assertFalse(result["pass"])

    def test_e_checks_fail_even_when_score_is_eligible(self):
        result = precision_gate_decision({"e_pass": False}, 31.9, True, 65)
        self.assertFalse(result["pass"])
        self.assertTrue(result["score_ok"])

    def test_non_finite_score_fails_closed(self):
        for score in (float("nan"), float("inf"), float("-inf"), None, "bad"):
            with self.subTest(score=score):
                result = precision_gate_decision({"e_pass": True}, score, True, 65)
                self.assertFalse(result["pass"])
                self.assertFalse(result["score_ok"])

    def test_disabled_gate_does_not_veto_but_reports_bad_score(self):
        result = precision_gate_decision({"e_pass": False}, 90, False, 65)
        self.assertTrue(result["pass"])
        self.assertFalse(result["score_ok"])
        self.assertFalse(result["score_gate_applied"])

    def test_invalid_cap_fails_closed_when_enabled(self):
        result = precision_gate_decision({"e_pass": True}, 30, True, math.nan)
        self.assertFalse(result["pass"])


if __name__ == "__main__":
    unittest.main()

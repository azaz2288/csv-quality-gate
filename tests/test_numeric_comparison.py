"""Independent numeric fixtures: no private data or float-overflow oracle."""
import copy
from decimal import Decimal, localcontext
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from csvgate.core import compare, load_profile, profile


def numeric_profile(mean, deviation, *, count=2):
    # Explicit imported-profile statistics, not a claim that Welford can
    # generate profiles whose variance overflows. Inputs remain finite.
    maximum = sys.float_info.max
    return {"version": 1, "source_sha256": "0" * 64, "rows": 2,
            "columns": ["value"], "missing": {"value": 2 - count},
            "numeric": {"value": {"count": count, "mean": mean,
                                  "stddev": deviation,
                                  "min": -maximum if count else None,
                                  "max": maximum if count else None}},
            "categories": {},
            "keys": {"columns": [], "missing_rows": 0, "duplicate_rows": 0}}


def passes(old_mean, new_mean, deviation, limit):
    return compare(numeric_profile(old_mean, deviation), numeric_profile(new_mean, 0),
                   1, limit, 1)["passed"]


class NumericComparisonTests(unittest.TestCase):
    def test_finite_opposite_means_do_not_false_alarm_on_overflow(self):
        self.assertTrue(passes(-8e307, 1.6e308, 8e307, 4))
        self.assertTrue(passes(8e307, -1.6e308, 8e307, 4))

    def test_overflow_difference_still_detects_real_violation(self):
        self.assertFalse(passes(-8e307, 1.6e308, 8e307, 2))

    def test_integer_profiles_do_not_raise_during_float_division(self):
        self.assertTrue(passes(-(10 ** 308), 10 ** 308, 10 ** 308, 3))
        self.assertFalse(passes(-(10 ** 308), 10 ** 308, 10 ** 308, 1))

    def test_subnormal_ratio_rounding_does_not_hide_violation(self):
        tiny = math.ulp(0.0)
        # Exact binary rationals: tiny/(3*tiny) > float(1/3), but a float
        # division rounds to the very same threshold and misses the excess.
        self.assertFalse(passes(0, tiny, 3 * tiny, 1 / 3))

    def test_generated_csv_profiles_obey_strict_float_threshold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_path, new_path = root / "old.csv", root / "new.csv"
            old_path.write_text("value\n-3\n3\n", encoding="utf-8")
            new_path.write_text("value\n1\n", encoding="utf-8")
            old = profile(old_path, ["value"], [], [])
            new = profile(new_path, ["value"], [], [])
            self.assertEqual(old["numeric"]["value"]["stddev"], 3)
            self.assertFalse(compare(old, new, 1, 1 / 3, 1)["passed"])
            self.assertTrue(compare(old, new, 1, math.nextafter(1 / 3, math.inf), 1)["passed"])

    def test_huge_and_subnormal_exact_threshold_boundaries(self):
        for scale in (math.ulp(0.0), math.ldexp(1.0, 1022), 1.0):
            with self.subTest(scale=scale):
                self.assertTrue(passes(-scale, scale, scale, 2))
                self.assertFalse(passes(-scale, scale, scale, math.nextafter(2.0, 0)))
                self.assertTrue(passes(-scale, scale, scale, math.nextafter(2.0, math.inf)))

    def test_zero_deviation_and_signed_zero_contract(self):
        self.assertTrue(passes(-0.0, 0.0, -0.0, 0))
        self.assertTrue(passes(1e308, 1e308, 0, 0))
        self.assertFalse(passes(0, math.ulp(0.0), 0, sys.float_info.max))

    def test_empty_numeric_column_still_skips_mean_comparison(self):
        absent = numeric_profile(None, None, count=0)
        old = numeric_profile(1, 0)
        self.assertTrue(compare(old, absent, 1, 0, 1)["passed"])
        self.assertTrue(compare(absent, old, 1, 0, 1)["passed"])

    def test_comparison_preserves_input_profiles_and_finite_report(self):
        old, new = numeric_profile(-8e307, 8e307), numeric_profile(1.6e308, 0)
        before = copy.deepcopy((old, new))
        report = compare(old, new, 1, 4, 1)
        self.assertEqual((old, new), before)
        self.assertTrue(json.loads(json.dumps(report, allow_nan=False))["passed"])

    def test_seeded_binary_values_match_independent_decimal_oracle(self):
        rng = random.Random(20261006)
        for index in range(240):
            old = math.ldexp(rng.uniform(-1, 1), rng.randint(-1070, 1023))
            new = math.ldexp(rng.uniform(-1, 1), rng.randint(-1070, 1023))
            deviation = math.ldexp(rng.uniform(0.5, 1), rng.randint(-1070, 1023))
            limit = math.ldexp(rng.uniform(0.5, 1), rng.randint(-1000, 1023))
            # 2500 decimal digits cover all digits of these exact IEEE-754
            # conversions/products, independently of Fraction implementation.
            with localcontext() as context:
                context.prec = 2500
                expected = abs(Decimal.from_float(new) - Decimal.from_float(old)) <= (
                    Decimal.from_float(deviation) * Decimal.from_float(limit))
            with self.subTest(index=index):
                self.assertEqual(passes(old, new, deviation, limit), expected)

    def test_cli_extreme_profiles_pass_fail_and_input_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_path, new_path = root / "old.json", root / "new.json"
            old_path.write_text(json.dumps(numeric_profile(-8e307, 8e307)), encoding="utf-8")
            new_path.write_text(json.dumps(numeric_profile(1.6e308, 0)), encoding="utf-8")
            self.assertEqual(load_profile(old_path)["numeric"]["value"]["mean"], -8e307)
            for limit, code in (("4", 0), ("2", 1), ("inf", 2)):
                result_path = root / f"result-{limit}.json"
                result = subprocess.run([sys.executable, "-m", "csvgate", "compare",
                                         str(old_path), str(new_path), str(result_path),
                                         "--max-mean-shift-sd", limit],
                                        capture_output=True, text=True)
                with self.subTest(limit=limit):
                    self.assertEqual(result.returncode, code, result.stderr)
                    if code < 2:
                        self.assertIs(json.loads(result_path.read_text())["passed"], code == 0)
                    else:
                        self.assertFalse(result_path.exists())


if __name__ == "__main__":
    unittest.main()

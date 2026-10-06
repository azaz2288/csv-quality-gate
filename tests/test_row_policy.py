"""Synthetic count policy fixtures; independent integer cross-product oracle."""
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from csvgate.core import DataError, compare, load_profile, profile


def counted(rows):
    return {"version": 1, "source_sha256": "0" * 64, "rows": rows,
            "columns": ["id"], "missing": {"id": 0}, "numeric": {},
            "categories": {}, "keys": {"columns": [], "missing_rows": 0,
                                       "duplicate_rows": 0}}


def gate(before, after, limit=None, policy="fail"):
    return compare(counted(before), counted(after), 1, 3, 1,
                   max_row_count_change=limit, empty_policy=policy)


class RowPolicyTests(unittest.TestCase):
    def test_drift_opt_in_detects_same_distribution_data_loss(self):
        self.assertTrue(compare(counted(4), counted(1), 1, 3, 1)["passed"])
        self.assertFalse(gate(4, 1, 0.5)["passed"])
        self.assertFalse(gate(4, 8, 0.5)["passed"])
        self.assertTrue(gate(4, 8, 1)["passed"])

    def test_equality_adjacent_limits_and_zero_tolerance(self):
        for after in (2, 6):
            self.assertTrue(gate(4, after, 0.5)["passed"])
            self.assertFalse(gate(4, after, math.nextafter(0.5, 0))["passed"])
            self.assertTrue(gate(4, after, math.nextafter(0.5, math.inf))["passed"])
        self.assertTrue(gate(4, 4, 0)["passed"])
        self.assertFalse(gate(4, 5, 0)["passed"])
        self.assertFalse(gate(3, 4, 1 / 3)["passed"])
        self.assertTrue(gate(3, 4, math.nextafter(1 / 3, math.inf))["passed"])

    def test_huge_counts_no_overflow_and_unit_drift_not_rounded_away(self):
        before = 10 ** 400
        self.assertTrue(gate(before, before * 2, 1)["passed"])
        self.assertFalse(gate(before, before * 2 + 1, 1)["passed"])
        self.assertFalse(gate(before, before + 1, 0)["passed"])
        self.assertFalse(gate(1, before, sys.float_info.max)["passed"])

    def test_seeded_counts_match_integer_oracle(self):
        rng = random.Random(20261006)
        for index in range(300):
            before = rng.randrange(1, 10 ** rng.randrange(1, 401))
            after = rng.randrange(1, before * 4 + 1)
            limit = rng.choice([0.0, 0.1, 1 / 3, 0.5, 1.0, 2.0])
            numerator, denominator = limit.as_integer_ratio()
            expected = abs(after - before) * denominator <= before * numerator
            with self.subTest(index=index):
                self.assertIs(gate(before, after, limit)["passed"], expected)

    def test_empty_policy_and_zero_baseline_contract(self):
        self.assertFalse(gate(4, 0)["passed"])
        self.assertFalse(gate(0, 0)["passed"])
        self.assertTrue(gate(4, 0, policy="allow")["passed"])
        self.assertTrue(gate(0, 0, 0, "allow")["passed"])
        self.assertTrue(gate(0, 4)["passed"])
        self.assertFalse(gate(0, 4, 100)["passed"])
        self.assertFalse(gate(4, 0, 0.5, "allow")["passed"])
        self.assertTrue(gate(4, 0, 1, "allow")["passed"])

    def test_empty_does_not_bypass_schema_or_selection_checks(self):
        old, new = counted(0), counted(0)
        new.update(columns=["other"], missing={"other": 0})
        result = compare(old, new, 1, 3, 1, empty_policy="allow")
        self.assertFalse(result["passed"])
        self.assertIn("Missing column: id", result["violations"])

    def test_empty_profiles_skip_unavailable_distribution_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            path.write_text("id,value,group\n1,3,A\n2,3,A\n", encoding="utf-8")
            old = profile(path, ["value"], ["group"], ["id"])
            path.write_text("id,value,group\n", encoding="utf-8")
            new = profile(path, ["value"], ["group"], ["id"], allow_empty=True)
            self.assertTrue(compare(old, new, 0, 0, 0, empty_policy="allow")["passed"])
            self.assertTrue(compare(new, old, 0, 0, 0)["passed"])
            self.assertFalse(compare(new, old, 0, 0, 0, max_row_count_change=1)["passed"])

    def test_header_only_profile_default_rejects_explicit_opt_in_is_canonical(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            data = b"\xef\xbb\xbfid,value,group\r\n"
            path.write_bytes(data)
            with self.assertRaisesRegex(DataError, "no data rows"):
                profile(path, ["value"], ["group"], ["id"])
            result = profile(path, ["value"], ["group"], ["id"], allow_empty=True)
            self.assertEqual(result["rows"], 0)
            self.assertEqual(result["source_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(result["numeric"]["value"],
                             dict(count=0, mean=None, stddev=None, min=None, max=None))
            self.assertEqual(result["categories"], {"group": {}})
            report = Path(directory) / "profile.json"
            report.write_text(json.dumps(result), encoding="utf-8")
            self.assertEqual(load_profile(report), result)

    def test_allow_empty_never_allows_missing_header_or_invalid_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            for content, selection in (("", []), ("id,id\n", []), ("id\n", ["unknown"])):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(DataError):
                    profile(path, selection, [], [], allow_empty=True)

    def test_zero_row_invariants_and_invalid_policies(self):
        for change in (lambda p: p.update(rows=-1), lambda p: p.update(rows=False),
                       lambda p: p["missing"].update(id=1),
                       lambda p: p["keys"].update(columns=["id"], duplicate_rows=1),
                       lambda p: p.update(categories={"id": {"A": 1}}),
                       lambda p: p.update(numeric={"id": dict(count=0, mean=0, stddev=None, min=None, max=None)})):
            candidate = counted(0)
            change(candidate)
            with self.assertRaises(DataError):
                compare(candidate, counted(0), 1, 3, 1, empty_policy="allow")
        for limit in (-1, True, "1", float("nan"), float("inf")):
            with self.subTest(limit=limit), self.assertRaises(DataError):
                gate(4, 4, limit)
        for policy in (None, True, "ignore", [], {}):
            with self.subTest(policy=policy), self.assertRaises(DataError):
                gate(4, 4, policy=policy)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.csv"
            path.write_text("id\n", encoding="utf-8")
            for allow in (1, None, "yes"):
                with self.assertRaises(DataError):
                    profile(path, [], [], [], allow_empty=allow)

    def test_report_records_policy_counts_without_mutating_inputs(self):
        old, new = counted(4), counted(0)
        original = copy.deepcopy((old, new))
        result = compare(old, new, 1, 3, 1, max_row_count_change=1, empty_policy="allow")
        self.assertEqual((old, new), original)
        self.assertEqual(result["row_count"], {"baseline": 4, "current": 0,
                                             "max_relative_change": 1, "empty_policy": "allow"})
        self.assertTrue(json.loads(json.dumps(result, allow_nan=False))["passed"])

    def test_real_cli_empty_and_drift_exit_codes_and_output_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, old, empty = root / "source.csv", root / "old.json", root / "empty.json"
            source.write_text("id\n1\n2\n3\n4\n", encoding="utf-8")
            def cli(*args):
                return subprocess.run([sys.executable, "-m", "csvgate", *map(str, args)],
                                      capture_output=True, text=True)
            self.assertEqual(cli("profile", source, old).returncode, 0)
            source.write_text("id\n", encoding="utf-8")
            self.assertEqual(cli("profile", source, empty).returncode, 2)
            self.assertFalse(empty.exists())
            self.assertEqual(cli("profile", source, empty, "--allow-empty").returncode, 0)
            cases = [([], 1), (["--empty-policy", "allow"], 0),
                     (["--empty-policy", "allow", "--max-row-count-change", "0.5"], 1),
                     (["--empty-policy", "allow", "--max-row-count-change", "1"], 0),
                     (["--max-row-count-change", "nan"], 2),
                     (["--max-row-count-change", "-1"], 2), (["--empty-policy", "ignore"], 2)]
            for index, (options, expected) in enumerate(cases):
                output = root / f"result-{index}.json"
                result = cli("compare", old, empty, output, *options)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(output.exists(), expected != 2)
                if expected != 2:
                    self.assertIs(json.loads(output.read_text())["passed"], expected == 0)
            original = old.read_bytes()
            self.assertEqual(cli("compare", old, empty, old, "--force").returncode, 2)
            self.assertEqual(old.read_bytes(), original)

    def test_generated_same_value_csv_loss_fails_cli_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            def cli(*args):
                return subprocess.run([sys.executable, "-m", "csvgate", *map(str, args)],
                                      capture_output=True, text=True)
            for label, count in (("before", 4), ("after", 1)):
                source.write_text("value\n" + "3\n" * count, encoding="utf-8")
                self.assertEqual(cli("profile", source, root / f"{label}.json",
                                     "--numeric", "value").returncode, 0)
            args = ("compare", root / "before.json", root / "after.json")
            self.assertEqual(cli(*args, root / "default.json").returncode, 0)
            self.assertEqual(cli(*args, root / "gated.json", "--max-row-count-change", "0.5").returncode, 1)
            result = json.loads((root / "gated.json").read_text())
            self.assertEqual(len(result["violations"]), 1)
            self.assertIn("Row count changed from 4 to 1", result["violations"][0])

    def test_empty_policy_allow_does_not_disable_current_key_failures(self):
        old, new = counted(0), counted(2)
        for candidate in (old, new):
            candidate["keys"]["columns"] = ["id"]
        new["keys"].update(missing_rows=1, duplicate_rows=1)
        result = compare(old, new, 1, 3, 1, empty_policy="allow")
        self.assertIn("Missing keys: 1 rows", result["violations"])
        self.assertIn("Duplicate keys: 1 rows", result["violations"])


if __name__ == "__main__":
    unittest.main()

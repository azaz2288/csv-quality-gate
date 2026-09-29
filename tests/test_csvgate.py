import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from csvgate.core import DataError, compare, load_profile, profile


def table(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)


class ProfileTests(unittest.TestCase):
    def test_aggregates_numeric_missing_categories_and_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.csv"
            table(path, [["id", "group", "value"], ["1", "A", "2"], ["2", "B", "4"], ["2", "B", ""], ["", "", "6"]])
            report = profile(path, ["value"], ["group"], ["id"])
            self.assertEqual(report["rows"], 4)
            self.assertEqual(report["missing"], {"id": 1, "group": 1, "value": 1})
            self.assertEqual(report["numeric"]["value"]["mean"], 4)
            self.assertAlmostEqual(report["numeric"]["value"]["stddev"], (8 / 3) ** 0.5)
            self.assertEqual(report["categories"]["group"], {"A": 1, "B": 2})
            self.assertEqual(report["keys"]["duplicate_rows"], 1)
            self.assertEqual(report["keys"]["missing_rows"], 1)
            self.assertEqual(len(report["source_sha256"]), 64)

    def test_rejects_invalid_rows_and_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.csv"
            table(path, [["id", "value"], ["1", "NaN"]])
            with self.assertRaisesRegex(DataError, "finite"):
                profile(path, ["value"], [], [])
            table(path, [["id", "value"], ["1"]])
            with self.assertRaisesRegex(DataError, "fields"):
                profile(path, ["value"], [], [])
            table(path, [["id", "id"], ["1", "2"]])
            with self.assertRaisesRegex(DataError, "unique headers"):
                profile(path, [], [], [])

    def test_rejects_unknown_selection_and_empty_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.csv"
            table(path, [["id"], ["1"]])
            with self.assertRaisesRegex(DataError, "Unknown numeric"):
                profile(path, ["amount"], [], [])
            with self.assertRaisesRegex(DataError, "Duplicate column"):
                profile(path, ["id", "id"], [], [])
            table(path, [["id"]])
            with self.assertRaisesRegex(DataError, "no data rows"):
                profile(path, [], [], [])


class CompareTests(unittest.TestCase):
    def make_pair(self, root: Path):
        before, after = root / "before.csv", root / "after.csv"
        table(before, [["id", "group", "value"], ["1", "A", "10"], ["2", "B", "20"], ["3", "A", "30"], ["4", "B", "40"]])
        table(after, [["id", "group", "value"], ["5", "A", "10"], ["6", "B", "20"], ["7", "A", "30"], ["8", "B", "40"]])
        return before, after

    def test_stable_data_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            before, after = self.make_pair(Path(temporary))
            old, new = (profile(path, ["value"], ["group"], ["id"]) for path in (before, after))
            self.assertTrue(compare(old, new, 0.05, 3, 0.01)["passed"])

    def test_detects_missing_mean_category_and_duplicate_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before, after = self.make_pair(root)
            table(after, [["id", "group", "value"], ["5", "C", "100"], ["5", "C", ""], ["7", "A", "110"], ["", "A", "120"]])
            old, new = (profile(path, ["value"], ["group"], ["id"]) for path in (before, after))
            findings = compare(old, new, 0.05, 3, 0.01)["violations"]
            self.assertTrue(any("missing rate" in item for item in findings))
            self.assertTrue(any("mean shifted" in item for item in findings))
            self.assertTrue(any("new-category rate" in item for item in findings))
            self.assertTrue(any("Duplicate keys" in item for item in findings))
            self.assertTrue(any("Missing keys" in item for item in findings))

    def test_schema_drift_and_invalid_threshold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before, after = self.make_pair(root)
            table(after, [["id", "extra"], ["5", "yes"]])
            old = profile(before, ["value"], ["group"], ["id"])
            new = profile(after, [], [], ["id"])
            findings = compare(old, new, 0, 3, 0)["violations"]
            self.assertIn("Missing column: value", findings)
            self.assertIn("New column: extra", findings)
            with self.assertRaisesRegex(DataError, "Thresholds"):
                compare(old, new, float("nan"), 3, 0)


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "csvgate", *args], capture_output=True, text=True, check=False)

    def test_round_trip_and_safe_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before, after = CompareTests().make_pair(root)
            baseline, current, result = root / "baseline.json", root / "current.json", root / "result.json"
            options = ["--numeric", "value", "--category", "group", "--key", "id"]
            self.assertEqual(self.run_cli("profile", str(before), str(baseline), *options).returncode, 0)
            self.assertEqual(self.run_cli("profile", str(after), str(current), *options).returncode, 0)
            self.assertEqual(self.run_cli("compare", str(baseline), str(current), str(result)).returncode, 0)
            self.assertTrue(json.loads(result.read_text(encoding="utf-8"))["passed"])
            self.assertEqual(self.run_cli("profile", str(before), str(baseline), *options).returncode, 2)
            self.assertEqual(self.run_cli("profile", str(before), str(before), *options, "--force").returncode, 2)
            self.assertEqual(load_profile(baseline)["rows"], 4)

    def test_tampered_profile_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text('{"version":1,"rows":1,"columns":["x"],"missing":{"x":0},"numeric":{},"categories":{},"keys":{"columns":[],"missing_rows":0,"duplicate_rows":0}}', encoding="utf-8")
            with self.assertRaisesRegex(DataError, "Invalid"):
                load_profile(path)


if __name__ == "__main__":
    unittest.main()

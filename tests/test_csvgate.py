import csv
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from csvgate.core import DataError, compare, load_profile, profile, write_json


def table(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)


class ProfileTests(unittest.TestCase):
    def test_digest_matches_parsed_bytes_even_if_path_changes_after_eof(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.csv"
            original = b"value\r\n2\r\n4\r\n"
            path.write_bytes(original)
            original_reader = csv.reader

            def changing_reader(*args, **kwargs):
                yield from original_reader(*args, **kwargs)
                path.write_bytes(b"value\r\n100\r\n")

            with patch("csvgate.core.csv.reader", side_effect=changing_reader):
                report = profile(path, ["value"], [], [])
            self.assertEqual(report["rows"], 2)
            self.assertEqual(report["numeric"]["value"]["mean"], 3)
            self.assertEqual(report["source_sha256"], hashlib.sha256(original).hexdigest())

    def test_hash_covers_exact_encoding_and_newline_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.csv"
            for bom in (b"", b"\xef\xbb\xbf"):
                for newline in ("\n", "\r\n", "\r"):
                    with self.subTest(bom=bom, newline=repr(newline)):
                        content = bom + (f'name,value{newline}"中文{newline}label",4{newline}').encode("utf-8")
                        path.write_bytes(content)
                        report = profile(path, ["value"], [], [])
                        self.assertEqual(report["rows"], 1)
                        self.assertEqual(report["source_sha256"], hashlib.sha256(content).hexdigest())

    def test_large_input_is_opened_once_in_binary_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.csv"
            content = b"value\n" + b"12345\n" * 20000
            path.write_bytes(content)
            original_open = Path.open
            opens = []

            def tracking_open(selected, *args, **kwargs):
                opens.append(args[0] if args else kwargs.get("mode", "r"))
                return original_open(selected, *args, **kwargs)

            with patch.object(Path, "open", new=tracking_open):
                report = profile(path, ["value"], [], [])
            self.assertEqual(opens, ["rb"])
            self.assertEqual(report["rows"], 20000)
            self.assertEqual(report["source_sha256"], hashlib.sha256(content).hexdigest())

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
    def test_invalid_profile_invariants_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "data.csv", root / "profile.json"
            table(source, [["id", "value", "group"], ["1", "2", "A"], ["2", "4", "B"]])
            valid = profile(source, ["value"], ["group"], ["id"])
            changes = [
                lambda p: p.update(version=True),
                lambda p: p.update(columns=[]),
                lambda p: p["numeric"]["value"].update(count=1),
                lambda p: p["numeric"]["value"].update(stddev=-1),
                lambda p: p["numeric"]["value"].update(mean=99),
                lambda p: p["numeric"]["value"].update(min=99),
                lambda p: p["categories"]["group"].update(A=100),
                lambda p: p["categories"]["group"].update(A=0),
                lambda p: p["keys"].update(duplicate_rows=3),
                lambda p: p["keys"].update(columns=["id", "id"]),
                lambda p: p["keys"].update(columns=[], missing_rows=1),
            ]
            for change in changes:
                candidate = copy.deepcopy(valid)
                change(candidate)
                with self.subTest(candidate=candidate):
                    output.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaises(DataError):
                        load_profile(output)

    def test_duplicate_json_keys_and_nonfinite_literals_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "data.csv", root / "profile.json"
            table(source, [["value"], ["1"]])
            content = json.dumps(profile(source, ["value"], [], []))
            for bad in (content.replace('"version": 1', '"version": 0, "version": 1'),
                        content[:-1] + ', "unused": NaN}'):
                output.write_text(bad, encoding="utf-8")
                with self.assertRaises(DataError):
                    load_profile(output)

    def test_no_force_output_cannot_overwrite_competing_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output.json"
            import os
            original_link = os.link

            def competing_writer(source, target):
                output.write_text("competing writer", encoding="utf-8")
                return original_link(source, target)

            with patch("csvgate.core.os.link", side_effect=competing_writer):
                with self.assertRaises(DataError):
                    write_json(output, {"version": 1}, (), False)
            self.assertEqual(output.read_text(encoding="utf-8"), "competing writer")
            self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_finite_numeric_inputs_that_overflow_stats_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.csv"
            table(path, [["value"], ["1e308"], ["-1e308"]])
            with self.assertRaisesRegex(DataError, "statistics"):
                profile(path, ["value"], [], [])

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

"""Profile CSV files without retaining their rows, then compare profiles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


class DataError(Exception):
    """An invalid input, policy or unsafe output path."""


def _columns(header: list[str], requested: list[str], option: str) -> list[str]:
    if len(set(requested)) != len(requested):
        raise DataError(f"Duplicate column in {option}")
    unknown = set(requested) - set(header)
    if unknown:
        raise DataError(f"Unknown {option} columns: {', '.join(sorted(unknown))}")
    return requested


class _HashingReader(io.RawIOBase):
    """Hash exactly the raw bytes passed to the CSV text decoder.

    The caller owns the underlying file and closes it separately.
    """

    def __init__(self, source, digest):
        super().__init__()
        self.source = source
        self.digest = digest

    def readable(self):
        return True

    def readinto(self, buffer):
        count = self.source.readinto(buffer)
        if count:
            self.digest.update(memoryview(buffer)[:count])
        return count


def profile(path: Path, numeric: list[str], category: list[str], keys: list[str]) -> dict[str, Any]:
    """Produce deterministic aggregate statistics from a CSV.

    Only selected categorical values and key tuples are retained in memory.
    The source digest covers the same byte stream consumed by the parser.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as raw, io.TextIOWrapper(
            io.BufferedReader(_HashingReader(raw, digest)),
            encoding="utf-8-sig", newline="",
        ) as source:
            reader = csv.reader(source, strict=True)
            header = next(reader, None)
            if header is None or not header or any(not name.strip() for name in header) or len(set(header)) != len(header):
                raise DataError("CSV needs nonempty, unique headers")
            numeric = _columns(header, numeric, "numeric")
            category = _columns(header, category, "category")
            keys = _columns(header, keys, "key")
            if set(numeric) & set(category):
                raise DataError("A column cannot be both numeric and categorical")
            positions = {name: index for index, name in enumerate(header)}
            missing = dict.fromkeys(header, 0)
            stats = {name: {"count": 0, "mean": 0.0, "m2": 0.0, "min": None, "max": None} for name in numeric}
            categories = {name: Counter() for name in category}
            seen_keys: set[tuple[str, ...]] = set()
            duplicate_keys = missing_keys = rows = 0
            for number, values in enumerate(reader, start=2):
                if len(values) != len(header):
                    raise DataError(f"Row {number} has {len(values)} fields; expected {len(header)}")
                rows += 1
                for name, index in positions.items():
                    if not values[index].strip():
                        missing[name] += 1
                for name in numeric:
                    raw = values[positions[name]].strip()
                    if not raw:
                        continue
                    try:
                        value = float(raw)
                    except ValueError as exc:
                        raise DataError(f"Row {number}, {name}: expected a number") from exc
                    if not math.isfinite(value):
                        raise DataError(f"Row {number}, {name}: number must be finite")
                    stat = stats[name]
                    stat["count"] += 1
                    delta = value - stat["mean"]
                    stat["mean"] += delta / stat["count"]
                    stat["m2"] += delta * (value - stat["mean"])
                    stat["min"] = value if stat["min"] is None else min(stat["min"], value)
                    stat["max"] = value if stat["max"] is None else max(stat["max"], value)
                for name in category:
                    value = values[positions[name]].strip()
                    if value:
                        counts = categories[name]
                        counts[value] += 1
                        if len(counts) > 1000:
                            raise DataError(f"{name}: more than 1000 categories; choose a low-cardinality column")
                if keys:
                    key = tuple(values[positions[name]].strip() for name in keys)
                    if any(not part for part in key):
                        missing_keys += 1
                    elif key in seen_keys:
                        duplicate_keys += 1
                    else:
                        seen_keys.add(key)
            if rows == 0:
                raise DataError("CSV has no data rows")
        numeric_report = {}
        for name, stat in stats.items():
            count = stat["count"]
            numeric_report[name] = {
                "count": count,
                "mean": stat["mean"] if count else None,
                "stddev": math.sqrt(max(0.0, stat["m2"] / count)) if count else None,
                "min": stat["min"],
                "max": stat["max"],
            }
        return {
            "version": 1,
            "source_sha256": digest.hexdigest(),
            "rows": rows,
            "columns": header,
            "missing": missing,
            "numeric": numeric_report,
            "categories": {name: dict(sorted(counts.items())) for name, counts in categories.items()},
            "keys": {"columns": keys, "missing_rows": missing_keys, "duplicate_rows": duplicate_keys},
        }
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataError(f"Cannot read CSV {path}: {exc}") from exc


def _valid_profile(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    digest = value.get("source_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return False
    rows, columns = value.get("rows"), value.get("columns")
    if type(rows) is not int or rows < 1 or not isinstance(columns, list) or any(not isinstance(item, str) for item in columns) or len(set(columns)) != len(columns):
        return False
    if not isinstance(value.get("missing"), dict) or set(value["missing"]) != set(columns):
        return False
    if any(type(count) is not int or not 0 <= count <= rows for count in value["missing"].values()):
        return False
    if not isinstance(value.get("numeric"), dict) or not isinstance(value.get("categories"), dict) or not isinstance(value.get("keys"), dict):
        return False
    if set(value["numeric"]) - set(columns) or set(value["categories"]) - set(columns):
        return False
    keys = value["keys"]
    if not isinstance(keys.get("columns"), list) or any(name not in columns for name in keys["columns"]):
        return False
    if any(type(keys.get(name)) is not int or keys[name] < 0 for name in ("missing_rows", "duplicate_rows")):
        return False
    for stat in value["numeric"].values():
        if not isinstance(stat, dict) or type(stat.get("count")) is not int or not 0 <= stat["count"] <= rows:
            return False
        if stat["count"] and any(type(stat.get(name)) not in (int, float) or not math.isfinite(stat[name]) for name in ("mean", "stddev", "min", "max")):
            return False
        if not stat["count"] and any(stat.get(name) is not None for name in ("mean", "stddev", "min", "max")):
            return False
    for counts in value["categories"].values():
        if not isinstance(counts, dict) or any(not isinstance(name, str) or type(count) is not int or count < 0 for name, count in counts.items()):
            return False
    return True


def load_profile(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataError(f"Cannot read profile {path}: {exc}") from exc
    if not _valid_profile(value):
        raise DataError(f"Invalid or unsupported profile: {path}")
    return value


def compare(baseline: dict[str, Any], current: dict[str, Any], max_missing_increase: float, max_mean_shift_sd: float, max_new_category_rate: float) -> dict[str, Any]:
    limits = (max_missing_increase, max_mean_shift_sd, max_new_category_rate)
    if any(not math.isfinite(limit) or limit < 0 for limit in limits) or max_missing_increase > 1 or max_new_category_rate > 1:
        raise DataError("Thresholds must be finite and nonnegative; rates must not exceed 1")
    violations: list[str] = []
    before, after = set(baseline["columns"]), set(current["columns"])
    for name in sorted(before - after):
        violations.append(f"Missing column: {name}")
    for name in sorted(after - before):
        violations.append(f"New column: {name}")
    for field in ("numeric", "categories"):
        if set(baseline[field]) != set(current[field]):
            violations.append(f"{field} selection changed")
    if baseline["keys"]["columns"] != current["keys"]["columns"]:
        violations.append("Key columns changed")
    for name in sorted(before & after):
        old_rate = baseline["missing"][name] / baseline["rows"]
        new_rate = current["missing"][name] / current["rows"]
        if new_rate - old_rate > max_missing_increase:
            violations.append(f"{name}: missing rate increased from {old_rate:.3f} to {new_rate:.3f}")
    for name in sorted(set(baseline["numeric"]) & set(current["numeric"])):
        old, new = baseline["numeric"][name], current["numeric"][name]
        if old["count"] and new["count"]:
            difference = abs(new["mean"] - old["mean"])
            deviation = old["stddev"]
            if (deviation == 0 and difference > 0) or (deviation > 0 and difference / deviation > max_mean_shift_sd):
                violations.append(f"{name}: mean shifted beyond {max_mean_shift_sd:g} baseline standard deviations")
    for name in sorted(set(baseline["categories"]) & set(current["categories"])):
        old_values = set(baseline["categories"][name])
        current_counts = current["categories"][name]
        total = sum(current_counts.values())
        novel = sum(count for value, count in current_counts.items() if value not in old_values)
        if total and novel / total > max_new_category_rate:
            violations.append(f"{name}: new-category rate {novel / total:.3f} exceeds {max_new_category_rate:g}")
    if current["keys"]["missing_rows"]:
        violations.append(f"Missing keys: {current['keys']['missing_rows']} rows")
    if current["keys"]["duplicate_rows"]:
        violations.append(f"Duplicate keys: {current['keys']['duplicate_rows']} rows")
    return {"version": 1, "passed": not violations, "violations": violations, "baseline_sha256": baseline["source_sha256"], "current_sha256": current["source_sha256"]}


def write_json(path: Path, value: dict[str, Any], inputs: tuple[Path, ...], force: bool) -> None:
    target = path.resolve(strict=False)
    if any(target == source.resolve(strict=False) for source in inputs):
        raise DataError("Output path must differ from input paths")
    if target.exists() and not force:
        raise DataError(f"Output already exists: {path}; use --force to replace it")
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    except (OSError, ValueError) as exc:
        raise DataError(f"Cannot write {path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

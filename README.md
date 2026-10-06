# CSV Quality Gate

A dependency-free Python CLI for checking whether a new CSV extract still looks like a trusted baseline. It profiles row count, column names, missing values, selected numeric columns, selected low-cardinality categories, and optional composite keys. A comparison produces a machine-readable report and a CI-friendly exit code.

This catches common pipeline regressions: silently missing fields, rising null rates, large mean shifts, new categories, duplicate IDs, blank keys, and (when configured) extract-volume drift. It complements time-series leakage checks such as Chronoguard; it does not prove statistical equivalence or data correctness.

## Quick start

Requires Python 3.12+. No packages to install. From the repository root:

```sh
python -m csvgate profile examples/baseline.csv baseline.json --numeric revenue --category region --key record_id
python -m csvgate profile examples/current.csv current.json --numeric revenue --category region --key record_id
python -m csvgate compare baseline.json current.json gate.json
python -m unittest discover -s tests -v
```

`gate.json` contains `passed`, a list of `violations`, and both source SHA-256 digests. The `profile` command returns 0 on success or 2 for invalid input/I/O. `compare` returns 0 for pass, 1 for quality violations, or 2 for invalid input/I/O. Existing output files are protected unless `--force` is supplied; input files cannot be overwritten.

Optional comparison thresholds are `--max-missing-increase` (default 0.05, absolute rate increase), `--max-mean-shift-sd` (default 3, measured against the baseline population standard deviation), and `--max-new-category-rate` (default 0.01, fraction of nonblank current values absent from the baseline). A zero-variance baseline flags any mean change. Current missing or duplicate composite keys always fail. Column additions/removals and changes to selected numeric/category/key columns also fail.

## v0.2.0: extract volume and empty-data policy

```sh
python -m csvgate compare baseline.json current.json gate.json --max-row-count-change 0.5
python -m csvgate profile header-only.csv empty.json --allow-empty
python -m csvgate compare baseline.json empty.json empty-gate.json --empty-policy allow --max-row-count-change 1
python tools/verify_checkout.py
```

`--max-row-count-change` is optional (disabled by default to preserve nonempty-data behavior). It tests `abs(current_rows - baseline_rows) / baseline_rows > limit`; equality passes. The limit is finite and nonnegative, and may exceed 1 to permit growth beyond doubling. A limit of 0 requires identical counts; 0.5 permits a 50% decrease or increase. Comparison uses exact integer/binary-float rational arithmetic, so huge counts and one-row differences are not rounded away. CLI decimal thresholds retain the binary-float interpretation described below: a mathematical 1/3 change exceeds float `0.3333333333333333`.

Profiling still rejects a header-only extract by default. `--allow-empty` explicitly creates a valid zero-row profile, retaining selected columns, null numeric summaries, empty category maps and the hash of the header bytes. It never accepts absent/invalid headers or unknown column selections. API equivalents are keyword-only `profile(..., allow_empty=True)` and `compare(..., max_row_count_change=0.5, empty_policy="allow")`.

Comparisons default to `--empty-policy fail`: an empty **current** extract is a quality violation (exit 1 and a saved report), not malformed input. `allow` removes only that violation; it does not disable schema/selection/key/row-count checks. Zero-to-zero count change is 0. Zero baseline to positive current cannot define a relative change and fails if the row-count limit is enabled, regardless of its size; leave it disabled only when deliberately accepting this reference. Positive-to-zero is a 100% decline and can pass only with `allow` plus a disabled or at-least-1 count limit.

If either profile has zero rows, missing-rate and category-distribution comparisons are skipped because no distribution was observed; numeric comparison still requires nonzero numeric counts. An allowed empty result therefore does **not** validate those distributions. Even with an empty baseline, current missing/duplicate keys still fail. Schema and selection checks always run.

Report version 1 adds `row_count` with baseline/current counts, configured `max_relative_change` (null when disabled) and `empty_policy`, making the chosen volume policy inspectable. Profile version remains 1 with zero counts now valid: older releases reject these profiles; consumers requiring an exact report key set must accept the added field. Reports are not signed evidence of authenticity.

40 tests include 14 new methods, 300 seeded integer cross-product oracle cases, equality/adjacent float thresholds, huge count fixtures, canonical empty summaries, schema/key safety and real CLI exits 0/1/2. The export verifier copies only package/tests into a temporary directory and reruns all tests, ignoring PYTHONPATH/user-site; it confirms the imported package is the export, without Git metadata or installing packages. This verifies a source export, not a wheel, native installer or browser UI.

## Profiling, validation and output limits

CSV rows are processed incrementally. Numeric mean and population variance use Welford's algorithm. Only distinct key tuples and selected category counts remain in memory; categories are limited to 1000 distinct values per selected column. Profile JSON records category labels, so keep reports private if labels are sensitive. Numeric values must be finite; blank cells count as missing. A baseline is a reference snapshot, not a guarantee that its distributions are correct. Review and version it like a data contract.

Profiling and SHA-256 hashing now share one bounded-memory binary read stream. The digest identifies the exact bytes consumed by the parser, including a UTF-8 BOM and original line endings; the input path is not reopened for hashing. A change after parsing cannot silently substitute a different file's digest. This is not an atomic filesystem snapshot: concurrent in-place writes may still produce a mixed byte stream. Use immutable exports or filesystem snapshots for reproducible inputs. Regression tests cover a path modified after EOF, BOM/newline combinations, quoted multiline UTF-8 fields, and inputs larger than the read buffer.

Profile loading rejects duplicate JSON keys, nonfinite literals, boolean versions, impossible numeric/category counts, negative deviation, out-of-range means, and inconsistent key counts. Finite input numbers that overflow accumulated statistics fail explicitly rather than producing an unusable profile. These checks establish internal consistency, not authenticity: malicious but self-consistent statistics require independent recomputation.

Without `--force`, publication uses a same-directory atomic hard link and refuses a concurrent writer instead of overwriting it. Filesystems without hard-link support fail safely; with `--force`, replacement uses `os.replace`. These operations protect against ordinary competing outputs, not adversarial concurrent directory/symlink manipulation.

## v0.1.1: overflow-safe mean gates

Mean drift now tests `abs(current_mean - baseline_mean) > baseline_stddev * limit` using exact rational representations of the finite stored integers/binary floats. Intermediate float overflow cannot falsely reject a moderate relative change, and division rounding cannot hide a strict threshold violation. Equality passes; a zero deviation permits only equal means. Empty numeric columns retain the existing skip behavior; missing-rate checks still apply.

This is exact comparison of the **stored values**, not exact arithmetic for original CSV decimal text or an authenticity guarantee. CLI decimals are parsed as binary floats: for example the float `0.3333333333333333` is slightly below mathematical 1/3. A genuine 1/3-SD shift therefore fails that strict limit; the next larger representable limit passes. Comparisons immediately at a boundary can change from earlier division-rounded results. Profile/report JSON version stays 1.

The streaming Welford profiler still rejects overflowed accumulated statistics; this patch does not allow it to profile arbitrary huge variances. Extreme imported profiles exercise comparison independently. Tests cover large floats/integers, subnormals, signed zero, equality/adjacent limits, real CSV-generated profiles, CLI exit0/1/2 and 240 seeded cases checked against a separate high-precision Decimal oracle. Missing/category-rate arithmetic is unchanged.

```sh
python -m unittest discover -s tests -v
python benchmarks/compare.py --columns 512 --rounds 30
```

The benchmark uses synthetic in-memory profiles, checks every expected violation, excludes one warmup, and measures Python allocation separately from elapsed time. It is not CSV throughput, native RSS, or a before/after speedup claim. Run from a Python 3.12+ checkout; no package installer/runtime dependencies or browser UI are needed.

## Further quality milestones

- Completed in v0.2.0: configurable row-count drift, explicit empty-extract policy and boundary tests; distribution/seasonality-aware volume contracts remain future work.
- Versioned schema/type contracts and intentional schema-migration approval.
- Bounded profile/CSV resource limits with clear rejection rather than silent truncation.
- Stronger numerical-statistic consistency checks with disclosed tolerance, not authentication claims.
- Optional bounded quantile/histogram summaries for changes a mean cannot detect.
- Output write/sync interruption tests and explicit durability limits.

This is a new public portfolio project, not an eligible pre-existing repository under the Feishu collection criteria associated with this work.

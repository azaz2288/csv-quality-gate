# CSV Quality Gate

A dependency-free Python CLI for checking whether a new CSV extract still looks like a trusted baseline. It profiles row count, column names, missing values, selected numeric columns, selected low-cardinality categories, and optional composite keys. A comparison produces a machine-readable report and a CI-friendly exit code.

This catches common pipeline regressions: silently missing fields, rising null rates, large mean shifts, new categories, duplicate IDs, and blank keys. It complements time-series leakage checks such as Chronoguard; it does not prove statistical equivalence or data correctness.

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

## Design and limits

CSV rows are processed incrementally. Numeric mean and population variance use Welford's algorithm. Only distinct key tuples and selected category counts remain in memory; categories are limited to 1000 distinct values per selected column. Profile JSON records category labels, so keep reports private if labels are sensitive. Numeric values must be finite; blank cells count as missing. A baseline is a reference snapshot, not a guarantee that its distributions are correct. Review and version it like a data contract. The SHA-256 digest identifies source bytes, but profiling and hashing read the file separately; use immutable source files for a reproducible run.

This is a new public portfolio project, not an eligible pre-existing repository under the Feishu collection criteria associated with this work.

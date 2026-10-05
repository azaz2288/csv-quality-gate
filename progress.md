# Verified maintenance progress

## 2026-10-06 v0.1.1 exact stored-value mean gates

Before the change, new numeric tests had four failures: API/JSON/CLI falsely rejected finite opposite-sign large means after intermediate subtraction overflow, and subnormal division rounding concealed a strict threshold violation. The previous 15 tests were retained. Fraction-based cross-product comparison now avoids subtraction/division overflow or rounding and preserves strict greater-than, zero deviation, empty columns and profile schema version1.

Final 26 tests passed locally on Python3.12.10/Windows, with 240 seeded independent 2500-digit Decimal-oracle cases and real generated CSV boundary coverage. CLI pass/reject/invalid exit0/1/2, compileall and Git diff checks passed. No third-party runtime dependencies or GUI; this project is exercised directly from its checkout, not claimed to have a validated wheel or browser surface.

Synthetic benchmark: 512 numeric columns, 30 rounds after excluded warmup; median0.00711335s, min0.00600250s, max0.00898070s. Separate tracemalloc pass peak173152bytes, every run's512 expected violations verified. Not CSV parsing/native RSS or causal speedup. No private CSV used.

Exactness applies to already stored finite binary floats/integers, not original decimal intent, profiler accuracy, statistical equivalence or profile authenticity. Welford overflow rejection remains; missing/category rate arithmetic unchanged. Near equality results can differ from older division-rounded gates; README gives1/3 example. Next: row-count policy, schema contracts, input resource limits, stronger statistic checks, bounded distribution summaries and output fault/durability audit. Exact commit, remote SHA, current-SHA CI and final clean-state evidence are recorded outside this repo in portfolio maintenance reports to avoid recursive documentation-only commits.

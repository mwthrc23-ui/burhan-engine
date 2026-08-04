"""Benchmark package for Burhan Engine.

Provides a reproducible benchmark suite and runner to measure:
- Diagnostic accuracy
- Top-1 and Top-3 diagnostic success rates
- False positive rate
- Analysis latency
- Exact ground-truth patch success inside temporary projects
- Patch false-positive rate on negative controls
- Composite release-gate status
"""

from .suite import BenchmarkCase, BenchmarkSuite, load_suite
from .runner import BenchmarkRunner, BenchmarkResult, CaseResult

__all__ = [
    "BenchmarkCase",
    "BenchmarkSuite",
    "load_suite",
    "BenchmarkRunner",
    "BenchmarkResult",
    "CaseResult",
]

"""Benchmark runner for Burhan Engine.

Executes the benchmark suite against the live ``BurhanAnalyzer`` and
produces reproducible metrics:

* Diagnostic accuracy — fraction of cases where the engine's top-1
  hypothesis ``kind`` matches the expected family.
* Top-1 repair success — fraction of *curated* cases where the top-1
  candidate matches the expected kind.
* Top-3 repair success — same but within the top-3 candidates.
* False positive rate — fraction of cases where the engine emits a
  confident hypothesis for a case with no matching ground truth.
* Mean analysis latency (ms).
* Patch pass counts (not run here; placeholder for sandbox integration).

Design rules
------------
* No state mutation.
* No network calls.
* Results are JSON-serialisable frozen dataclasses.
* Latency figures are wall-clock measurements of ``BurhanAnalyzer.analyze``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..analyzer import BurhanAnalyzer
from .suite import BenchmarkCase, BenchmarkSuite, load_suite


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Result of running a single benchmark case.

    Attributes
    ----------
    case_id:
        Identifier of the case.
    top1_kind:
        The ``kind`` of the top-1 hypothesis produced by the engine, or
        ``""`` if no hypothesis was produced.
    top3_kinds:
        The ``kind`` values of the top-3 hypotheses.
    expected_kind:
        The expected top-1 kind from the benchmark case.
    correct_top1:
        True when ``top1_kind == expected_kind``.
    correct_top3:
        True when ``expected_kind`` appears in ``top3_kinds``.
    is_false_positive:
        True when the engine emits a confident (≥0.5) hypothesis but the
        case has no matching ground truth (``curated=False``).
    elapsed_ms:
        Wall-clock time for ``BurhanAnalyzer.analyze``.
    error:
        Non-empty if analysis raised an exception.
    """

    case_id: str
    top1_kind: str
    top3_kinds: tuple[str, ...]
    expected_kind: str
    correct_top1: bool
    correct_top3: bool
    is_false_positive: bool
    elapsed_ms: float
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "top1_kind": self.top1_kind,
            "top3_kinds": list(self.top3_kinds),
            "expected_kind": self.expected_kind,
            "correct_top1": self.correct_top1,
            "correct_top3": self.correct_top3,
            "is_false_positive": self.is_false_positive,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Aggregate result of the full benchmark run.

    Attributes
    ----------
    total_cases:
        Number of cases in the suite.
    curated_cases:
        Number of cases marked as curated (ground truth available).
    diagnostic_accuracy:
        Fraction of cases with correct top-1 classification.
    top1_success:
        Top-1 verified repair success rate (curated cases only).
    top3_success:
        Top-3 verified repair success rate (curated cases only).
    false_positive_rate:
        Rate of confident false positives on non-curated cases.
    mean_latency_ms:
        Mean analysis latency across all cases.
    families_covered:
        Number of distinct error families in the suite.
    case_results:
        Per-case results.
    """

    total_cases: int
    curated_cases: int
    diagnostic_accuracy: float
    top1_success: float
    top3_success: float
    false_positive_rate: float
    mean_latency_ms: float
    families_covered: int
    case_results: tuple[CaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "curated_cases": self.curated_cases,
            "diagnostic_accuracy": round(self.diagnostic_accuracy, 4),
            "top1_success": round(self.top1_success, 4),
            "top3_success": round(self.top3_success, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "mean_latency_ms": round(self.mean_latency_ms, 3),
            "families_covered": self.families_covered,
            "case_results": [r.to_dict() for r in self.case_results],
        }

    def summary_lines(self) -> list[str]:
        """Return human-readable summary lines (UTF-8 safe)."""
        return [
            f"Benchmark  : {self.total_cases} cases / {self.families_covered} families",
            f"Diagnostic : {self.diagnostic_accuracy:.1%}",
            f"Top-1      : {self.top1_success:.1%}",
            f"Top-3      : {self.top3_success:.1%}",
            f"False+     : {self.false_positive_rate:.1%}",
            f"Latency    : {self.mean_latency_ms:.1f} ms mean",
        ]


class BenchmarkRunner:
    """Runs the benchmark suite against a ``BurhanAnalyzer`` instance.

    Parameters
    ----------
    analyzer:
        The analyzer to benchmark.  If ``None``, a default instance is
        constructed with no project path.
    suite:
        The suite to run.  If ``None``, the default suite is loaded.
    """

    def __init__(
        self,
        analyzer: BurhanAnalyzer | None = None,
        suite: BenchmarkSuite | None = None,
    ) -> None:
        self._analyzer = analyzer or BurhanAnalyzer()
        self._suite = suite if suite is not None else load_suite()

    def run(self) -> BenchmarkResult:
        """Execute all cases and return aggregate metrics."""
        case_results: list[CaseResult] = []
        for case in self._suite.cases:
            result = self._run_case(case)
            case_results.append(result)

        return self._aggregate(tuple(case_results))

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    def _run_case(self, case: BenchmarkCase) -> CaseResult:
        try:
            import tempfile
            from pathlib import Path

            start = time.perf_counter()
            with tempfile.TemporaryDirectory() as tmpdir:
                analysis = self._analyzer.analyze(
                    project=Path(tmpdir),
                    goal=f"Diagnose: {case.error_family}",
                    error_text=case.error_text,
                )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            hypotheses = analysis.hypotheses
            top1_kind = hypotheses[0].kind if hypotheses else ""
            top3_kinds = tuple(h.kind for h in hypotheses[:3])
            expected = case.expected_top1_kind

            correct_top1 = top1_kind == expected
            correct_top3 = expected in top3_kinds

            # False positive: confident hypothesis on non-curated case
            top1_conf = hypotheses[0].confidence if hypotheses else 0.0
            is_false_positive = (
                not case.curated and top1_conf >= 0.5
            )

            return CaseResult(
                case_id=case.case_id,
                top1_kind=top1_kind,
                top3_kinds=top3_kinds,
                expected_kind=expected,
                correct_top1=correct_top1,
                correct_top3=correct_top3,
                is_false_positive=is_false_positive,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                case_id=case.case_id,
                top1_kind="",
                top3_kinds=(),
                expected_kind=case.expected_top1_kind,
                correct_top1=False,
                correct_top3=False,
                is_false_positive=False,
                elapsed_ms=0.0,
                error=str(exc),
            )

    @staticmethod
    def _aggregate(case_results: tuple[CaseResult, ...]) -> BenchmarkResult:
        total = len(case_results)
        if total == 0:
            return BenchmarkResult(
                total_cases=0,
                curated_cases=0,
                diagnostic_accuracy=0.0,
                top1_success=0.0,
                top3_success=0.0,
                false_positive_rate=0.0,
                mean_latency_ms=0.0,
                families_covered=0,
                case_results=(),
            )

        # Diagnostic accuracy: correct top-1 over all cases (excl. error)
        valid = [r for r in case_results if not r.error]
        diag_accuracy = sum(1 for r in valid if r.correct_top1) / total if total else 0.0

        # Top-1/3 success: curated cases only (excluding error cases)
        curated = [r for r in valid if r.expected_kind != ""]
        curated_count = len(curated)
        top1 = sum(1 for r in curated if r.correct_top1) / curated_count if curated_count else 0.0
        top3 = sum(1 for r in curated if r.correct_top3) / curated_count if curated_count else 0.0

        # False positive rate: over non-curated cases
        non_curated = [r for r in valid if not r.correct_top1 and r.top1_kind != r.expected_kind]
        fp_denom = len(non_curated) if non_curated else 1
        fp_rate = sum(1 for r in non_curated if r.is_false_positive) / fp_denom

        mean_latency = sum(r.elapsed_ms for r in case_results) / total
        families = len({r.expected_kind for r in case_results if r.expected_kind})

        return BenchmarkResult(
            total_cases=total,
            curated_cases=curated_count,
            diagnostic_accuracy=diag_accuracy,
            top1_success=top1,
            top3_success=top3,
            false_positive_rate=fp_rate,
            mean_latency_ms=mean_latency,
            families_covered=families,
            case_results=case_results,
        )

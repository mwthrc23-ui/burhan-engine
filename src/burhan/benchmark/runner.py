"""Benchmark runner for Burhan Engine.

Executes the benchmark suite against the live ``BurhanAnalyzer`` and
produces reproducible metrics:

* Diagnostic accuracy — fraction of cases where the engine's top-1
  hypothesis ``kind`` matches the expected family.
* Top-1 diagnostic success — fraction of *curated* cases where the top-1
  hypothesis belongs to the expected error family.
* Top-3 diagnostic success — same but within the top-3 hypotheses.
* False positive rate — fraction of negative-control cases where the engine
  emits a confident diagnostic family despite no expected family.
* Mean analysis latency (ms).
* Patch success on explicit ground truth after applying in a temporary project.
* Patch false-positive rate on negative controls, kept separate from the
  diagnostic false-positive rate.

Design rules
------------
* No mutation of the source benchmark suite; PatchEngine applies only inside
  a temporary project.
* No network calls.
* Results are JSON-serialisable frozen dataclasses.
* Latency figures are wall-clock measurements of ``BurhanAnalyzer.analyze``.
"""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..analyzer import BurhanAnalyzer
from ..patcher import PatchEngine
from .suite import BenchmarkCase, BenchmarkSuite, load_suite


NEUTRAL_BENCHMARK_GOAL = "Assess the project evidence without assuming an error."
MIN_DIAGNOSTIC_ACCURACY = 0.90
MIN_DIAGNOSTIC_TOP1_SUCCESS = 0.90
MAX_FALSE_POSITIVE_RATE = 0.10
MIN_NEGATIVE_CONTROL_CASES = 8
MIN_PATCH_ATTEMPTED_CASES = 4
MIN_PATCH_SUCCESS_RATE = 0.90

_ABSTENTION_KINDS = frozenset({"", "insufficient_evidence"})


_PYTHON_FAMILY_BY_KIND = {
    "undefined_name": "name_error",
    "unbound_local_variable": "unbound_local_error",
    "attribute_error": "attribute_error",
    "missing_attribute": "attribute_error",
    "missing_import_name": "import_error",
    "missing_module": "import_error",
    "wrong_argument_count": "type_error",
    "not_callable": "type_error",
    "unsupported_operand": "type_error",
    "type_error": "type_error",
    "missing_key": "key_error",
    "key_name_typo": "key_error",
    "wrong_dictionary": "key_error",
    "key_type_mismatch": "key_error",
    "index_out_of_range": "index_error",
    "async_error": "async_error",
}

_TYPESCRIPT_FAMILY_BY_KIND = {
    "undefined_name": "typescript_missing_symbol",
    "missing_property": "typescript_missing_symbol",
    "argument_type_mismatch": "typescript_type_mismatch",
    "wrong_argument_count": "typescript_type_mismatch",
    "type_mismatch": "typescript_type_mismatch",
}


def _diagnostic_family(kind: str, language: str) -> str:
    mapping = (
        _TYPESCRIPT_FAMILY_BY_KIND
        if language == "typescript"
        else _PYTHON_FAMILY_BY_KIND
    )
    return mapping.get(kind, kind)


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
        True when a negative-control case receives a confident (≥0.5)
        diagnostic family despite having no expected family.
    patch_attempted:
        True for an explicit ground-truth patch case.
    patch_succeeded:
        True only when the applied source exactly matches ground truth.
    patch_error:
        Reason an expected patch failed, without treating a safe rejection as
        a benchmark execution error.
    is_patch_false_positive:
        True when PatchEngine generated a patch for a negative control.
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
    is_curated: bool = True
    patch_attempted: bool = False
    patch_succeeded: bool = False
    patch_error: str = ""
    is_patch_false_positive: bool = False
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
            "is_curated": self.is_curated,
            "patch_attempted": self.patch_attempted,
            "patch_succeeded": self.patch_succeeded,
            "patch_error": self.patch_error,
            "is_patch_false_positive": self.is_patch_false_positive,
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
        Top-1 diagnostic success rate (curated cases only).
    top3_success:
        Top-3 diagnostic success rate (curated cases only).
    false_positive_rate:
        Rate of confident false positives on explicit negative controls.
        It is 0 when the suite contains no negative controls; inspect
        ``negative_control_cases`` before interpreting it.
    negative_control_cases:
        Number of cases without an expected diagnostic family.
    patch_attempted_cases:
        Number of ground-truth cases submitted to PatchEngine application.
    patch_success_cases:
        Number of applications matching the exact expected source.
    patch_success_rate:
        Exact ground-truth matches divided by attempted applications.
    patch_negative_control_cases:
        Number of negative controls probed for patch generation.
    patch_false_positive_cases:
        Number of controls for which PatchEngine generated a patch.
    patch_false_positive_rate:
        Patch false positives divided by patch negative controls.
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
    negative_control_cases: int
    patch_attempted_cases: int
    patch_success_cases: int
    patch_success_rate: float
    patch_negative_control_cases: int
    patch_false_positive_cases: int
    patch_false_positive_rate: float
    mean_latency_ms: float
    families_covered: int
    case_results: tuple[CaseResult, ...]

    @property
    def diagnostic_top1_success(self) -> float:
        """Explicit name for the backward-compatible ``top1_success`` field."""
        return self.top1_success

    @property
    def diagnostic_top3_success(self) -> float:
        """Explicit name for the backward-compatible ``top3_success`` field."""
        return self.top3_success

    @property
    def passes_release_gate(self) -> bool:
        """Whether all diagnostic, false-positive, and patch gates pass."""
        return (
            not any(result.error for result in self.case_results)
            and self.diagnostic_accuracy >= MIN_DIAGNOSTIC_ACCURACY
            and self.top1_success >= MIN_DIAGNOSTIC_TOP1_SUCCESS
            and self.negative_control_cases >= MIN_NEGATIVE_CONTROL_CASES
            and self.false_positive_rate <= MAX_FALSE_POSITIVE_RATE
            and self.patch_attempted_cases >= MIN_PATCH_ATTEMPTED_CASES
            and self.patch_success_rate >= MIN_PATCH_SUCCESS_RATE
            and self.patch_negative_control_cases >= MIN_NEGATIVE_CONTROL_CASES
            and self.patch_false_positive_cases == 0
            and self.patch_false_positive_rate == 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "curated_cases": self.curated_cases,
            "diagnostic_accuracy": round(self.diagnostic_accuracy, 4),
            "top1_success": round(self.top1_success, 4),
            "top3_success": round(self.top3_success, 4),
            "diagnostic_top1_success": round(self.diagnostic_top1_success, 4),
            "diagnostic_top3_success": round(self.diagnostic_top3_success, 4),
            "false_positive_rate": round(self.false_positive_rate, 4),
            "negative_control_cases": self.negative_control_cases,
            "patch_attempted_cases": self.patch_attempted_cases,
            "patch_success_cases": self.patch_success_cases,
            "patch_success_rate": round(self.patch_success_rate, 4),
            "patch_negative_control_cases": self.patch_negative_control_cases,
            "patch_false_positive_cases": self.patch_false_positive_cases,
            "patch_false_positive_rate": round(self.patch_false_positive_rate, 4),
            "passes_release_gate": self.passes_release_gate,
            "mean_latency_ms": round(self.mean_latency_ms, 3),
            "families_covered": self.families_covered,
            "case_results": [r.to_dict() for r in self.case_results],
        }

    def summary_lines(self) -> list[str]:
        """Return human-readable summary lines (UTF-8 safe)."""
        return [
            f"Benchmark  : {self.total_cases} cases / {self.families_covered} families",
            f"Diagnostic : {self.diagnostic_accuracy:.1%}",
            f"Diag Top-1 : {self.top1_success:.1%}",
            f"Diag Top-3 : {self.top3_success:.1%}",
            (
                f"False+     : {self.false_positive_rate:.1%}"
                if self.negative_control_cases
                else "False+     : n/a (no negative controls)"
            ),
            (
                f"Patch      : {self.patch_success_cases}/"
                f"{self.patch_attempted_cases} ({self.patch_success_rate:.1%})"
                if self.patch_attempted_cases
                else "Patch      : n/a (no patchable cases)"
            ),
            (
                f"Patch False+: {self.patch_false_positive_cases}/"
                f"{self.patch_negative_control_cases} "
                f"({self.patch_false_positive_rate:.1%})"
                if self.patch_negative_control_cases
                else "Patch False+: n/a (no negative controls)"
            ),
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
        patch_engine: PatchEngine | None = None,
    ) -> None:
        self._analyzer = analyzer or BurhanAnalyzer()
        self._suite = suite if suite is not None else load_suite()
        self._patch_engine = patch_engine or PatchEngine()

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
            with tempfile.TemporaryDirectory() as tmpdir:
                project = Path(tmpdir)
                source_path = self._source_path(project, case)
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text(
                    case.source_snippet,
                    encoding="utf-8",
                )
                goal = (
                    NEUTRAL_BENCHMARK_GOAL
                    if not case.expected_error_family
                    else f"Diagnose: {case.error_family}"
                )
                start = time.perf_counter()
                analysis = self._analyzer.analyze(
                    project=project,
                    goal=goal,
                    error_text=case.error_text,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                patch_attempted = case.patchable
                patch_succeeded = False
                patch_error = ""
                is_patch_false_positive = False
                if patch_attempted:
                    try:
                        if not analysis.hypotheses:
                            raise ValueError("analysis produced no patch hypothesis")
                        patch = self._patch_engine.repair(
                            project,
                            analysis.hypotheses[0],
                            apply=True,
                        )
                        patched_source = source_path.read_text(encoding="utf-8")
                        patch_succeeded = bool(
                            patch.diff
                            and tuple(patch.changed_files)
                            == (Path(case.source_path).as_posix(),)
                            and patch.applied
                            and patched_source == case.expected_patched_source
                        )
                        if not patch_succeeded:
                            patch_error = (
                                "applied patch did not match exact ground truth"
                            )
                    except (OSError, ValueError) as exc:
                        patch_error = str(exc)
                elif not case.expected_error_family and analysis.hypotheses:
                    try:
                        patch = self._patch_engine.repair(
                            project,
                            analysis.hypotheses[0],
                            apply=True,
                        )
                        is_patch_false_positive = bool(
                            patch.diff and patch.changed_files
                        )
                    except Exception:  # noqa: BLE001 - rejection means no patch FP
                        pass
            hypotheses = analysis.hypotheses
            top1_kind = hypotheses[0].kind if hypotheses else ""
            top3_kinds = tuple(h.kind for h in hypotheses[:3])
            expected = case.expected_error_family
            top1_family = _diagnostic_family(top1_kind, case.language)
            top3_families = tuple(
                _diagnostic_family(kind, case.language) for kind in top3_kinds
            )

            # False positive: confident diagnosis on an explicit negative
            # control (a case with no expected diagnostic family).
            top1_conf = hypotheses[0].confidence if hypotheses else 0.0
            is_false_positive = (
                not expected
                and top1_kind not in _ABSTENTION_KINDS
                and top1_conf >= 0.5
            )
            correct_top1 = (
                not is_false_positive if not expected else top1_family == expected
            )
            correct_top3 = (
                not is_false_positive if not expected else expected in top3_families
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
                is_curated=case.curated,
                patch_attempted=patch_attempted,
                patch_succeeded=patch_succeeded,
                patch_error=patch_error,
                is_patch_false_positive=is_patch_false_positive,
            )
        except Exception as exc:  # noqa: BLE001
            return CaseResult(
                case_id=case.case_id,
                top1_kind="",
                top3_kinds=(),
                expected_kind=case.expected_error_family,
                correct_top1=False,
                correct_top3=False,
                is_false_positive=False,
                elapsed_ms=0.0,
                is_curated=case.curated,
                patch_attempted=case.patchable,
                patch_error="analysis failed before patch preview" if case.patchable else "",
                error=str(exc),
            )

    @staticmethod
    def _source_path(project: Path, case: BenchmarkCase) -> Path:
        suffix = ".ts" if case.language == "typescript" else ".py"
        relative = Path(case.source_path or f"benchmark_case{suffix}")
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"case {case.case_id}: source_path must stay in project")
        return project / relative

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
                negative_control_cases=0,
                patch_attempted_cases=0,
                patch_success_cases=0,
                patch_success_rate=0.0,
                patch_negative_control_cases=0,
                patch_false_positive_cases=0,
                patch_false_positive_rate=0.0,
                mean_latency_ms=0.0,
                families_covered=0,
                case_results=(),
            )

        # Diagnostic accuracy is measured only on positive diagnostic cases;
        # negative controls have their own false-positive metric.
        valid = [r for r in case_results if not r.error]
        diagnostic = [r for r in valid if r.expected_kind]
        diag_accuracy = (
            sum(1 for r in diagnostic if r.correct_top1) / len(diagnostic)
            if diagnostic
            else 0.0
        )

        # Top-1/3 success: curated cases only (excluding error cases)
        curated = [r for r in valid if r.is_curated]
        curated_count = len(curated)
        top1 = sum(1 for r in curated if r.correct_top1) / curated_count if curated_count else 0.0
        top3 = sum(1 for r in curated if r.correct_top3) / curated_count if curated_count else 0.0

        # False positive rate: only explicit negative controls count. A case
        # without a known repair may still have a valid diagnostic family.
        negative_controls = [r for r in valid if not r.expected_kind]
        fp_rate = (
            sum(1 for r in negative_controls if r.is_false_positive)
            / len(negative_controls)
            if negative_controls
            else 0.0
        )

        patch_attempted = [r for r in case_results if r.patch_attempted]
        patch_success_cases = sum(1 for r in patch_attempted if r.patch_succeeded)
        patch_success_rate = (
            patch_success_cases / len(patch_attempted) if patch_attempted else 0.0
        )
        patch_negative_controls = [r for r in valid if not r.expected_kind]
        patch_false_positive_cases = sum(
            1 for r in patch_negative_controls if r.is_patch_false_positive
        )
        patch_false_positive_rate = (
            patch_false_positive_cases / len(patch_negative_controls)
            if patch_negative_controls
            else 0.0
        )

        mean_latency = sum(r.elapsed_ms for r in case_results) / total
        families = len({r.expected_kind for r in case_results if r.expected_kind})

        return BenchmarkResult(
            total_cases=total,
            curated_cases=curated_count,
            diagnostic_accuracy=diag_accuracy,
            top1_success=top1,
            top3_success=top3,
            false_positive_rate=fp_rate,
            negative_control_cases=len(negative_controls),
            patch_attempted_cases=len(patch_attempted),
            patch_success_cases=patch_success_cases,
            patch_success_rate=patch_success_rate,
            patch_negative_control_cases=len(patch_negative_controls),
            patch_false_positive_cases=patch_false_positive_cases,
            patch_false_positive_rate=patch_false_positive_rate,
            mean_latency_ms=mean_latency,
            families_covered=families,
            case_results=case_results,
        )

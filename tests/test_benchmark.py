"""Tests for the benchmark suite and runner (Phase 1).

TDD: these tests define the expected behaviour; implementation must
satisfy all assertions.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from burhan.benchmark.suite import (
    BenchmarkCase,
    BenchmarkSuite,
    VALID_FAMILIES,
    load_suite,
)
from burhan.benchmark.runner import BenchmarkRunner, BenchmarkResult


# ---------------------------------------------------------------------------
# Suite structure
# ---------------------------------------------------------------------------

class TestBenchmarkSuite:
    def test_suite_has_at_least_50_cases(self) -> None:
        suite = load_suite()
        assert len(suite) >= 50, f"Expected ≥50 cases, got {len(suite)}"

    def test_suite_covers_at_least_8_error_families(self) -> None:
        suite = load_suite()
        assert len(suite.families()) >= 8, (
            f"Expected ≥8 error families, got {suite.families()}"
        )

    def test_all_case_ids_are_unique(self) -> None:
        suite = load_suite()
        ids = [c.case_id for c in suite.cases]
        assert len(ids) == len(set(ids)), "Duplicate case IDs found"

    def test_all_error_families_are_valid(self) -> None:
        suite = load_suite()
        for case in suite.cases:
            assert case.error_family in VALID_FAMILIES, (
                f"Case {case.case_id} has unknown family {case.error_family!r}"
            )

    def test_curated_cases_have_ground_truth_repair(self) -> None:
        suite = load_suite()
        for case in suite.cases:
            if case.curated:
                assert case.ground_truth_repair, (
                    f"Case {case.case_id} is curated but has no ground_truth_repair"
                )

    def test_all_cases_have_non_empty_error_text(self) -> None:
        suite = load_suite()
        for case in suite.cases:
            assert case.error_text.strip(), f"Case {case.case_id} has empty error_text"

    def test_suite_contains_python_and_typescript(self) -> None:
        suite = load_suite()
        languages = {c.language for c in suite.cases}
        assert "python" in languages
        assert "typescript" in languages

    def test_by_family_returns_correct_subset(self) -> None:
        suite = load_suite()
        name_cases = suite.by_family("name_error")
        assert all(c.error_family == "name_error" for c in name_cases)
        assert len(name_cases) >= 1

    def test_curated_only_returns_curated(self) -> None:
        suite = load_suite()
        curated = suite.curated_only()
        assert all(c.curated for c in curated)
        assert len(curated) >= 1

    def test_invalid_family_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown error_family"):
            BenchmarkCase(
                case_id="bad-001",
                language="python",
                error_family="nonexistent_family",
                error_text="some error",
                source_snippet="",
                expected_error_family="name_error",
                expected_top1_kind="name_error",
                curated=False,
            )

    def test_suite_to_dict_structure(self) -> None:
        suite = load_suite()
        d = suite.to_dict()
        assert "total" in d
        assert "cases" in d
        assert d["total"] == len(suite)
        assert isinstance(d["cases"], list)

    def test_case_to_dict_structure(self) -> None:
        suite = load_suite()
        case = suite.cases[0]
        d = case.to_dict()
        expected_keys = {
            "case_id", "language", "error_family", "error_text",
            "source_snippet", "expected_error_family", "expected_top1_kind",
            "curated", "ground_truth_repair", "notes",
        }
        assert set(d.keys()) >= expected_keys


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TestBenchmarkRunner:
    def test_live_suite_meets_release_accuracy_gate(self) -> None:
        """The shipped benchmark must exercise the live analyzer successfully."""
        result = BenchmarkRunner().run()
        assert not [case for case in result.case_results if case.error]
        assert result.diagnostic_accuracy >= 0.90
        assert result.top1_success >= 0.90

    def test_runner_produces_benchmark_result(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        assert isinstance(result, BenchmarkResult)

    def test_result_total_matches_suite(self) -> None:
        suite = load_suite()
        runner = BenchmarkRunner(suite=suite)
        result = runner.run()
        assert result.total_cases == len(suite)

    def test_curated_count_matches_suite_metadata(self) -> None:
        suite = load_suite()
        result = BenchmarkRunner(suite=suite).run()
        assert result.curated_cases == len(suite.curated_only())

    def test_result_families_covered_at_least_8(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        assert result.families_covered >= 8

    def test_case_results_count_matches_total(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        assert len(result.case_results) == result.total_cases

    def test_all_metrics_in_valid_range(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        for attr in (
            "diagnostic_accuracy",
            "top1_success",
            "top3_success",
            "false_positive_rate",
        ):
            value = getattr(result, attr)
            assert 0.0 <= value <= 1.0, f"{attr} = {value} out of [0, 1]"

    def test_default_suite_does_not_claim_false_positive_measurement(self) -> None:
        result = BenchmarkRunner().run()
        assert result.negative_control_cases == 0
        assert "n/a" in "\n".join(result.summary_lines())

    def test_mean_latency_non_negative(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        assert result.mean_latency_ms >= 0.0

    def test_result_to_dict_structure(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        d = result.to_dict()
        expected_keys = {
            "total_cases", "curated_cases", "diagnostic_accuracy",
            "top1_success", "top3_success", "false_positive_rate",
            "mean_latency_ms", "families_covered", "case_results",
        }
        assert set(d.keys()) >= expected_keys

    def test_case_result_to_dict_has_required_keys(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        assert result.case_results
        d = result.case_results[0].to_dict()
        expected_keys = {
            "case_id", "top1_kind", "top3_kinds", "expected_kind",
            "correct_top1", "correct_top3", "is_false_positive",
            "elapsed_ms", "error",
        }
        assert set(d.keys()) >= expected_keys

    def test_summary_lines_returns_list_of_strings(self) -> None:
        runner = BenchmarkRunner()
        result = runner.run()
        lines = result.summary_lines()
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)
        assert len(lines) >= 4

    def test_runner_handles_analysis_error_gracefully(self) -> None:
        """Runner must not crash if the analyzer raises for one case."""
        from unittest.mock import MagicMock
        from burhan.benchmark.suite import BenchmarkSuite, BenchmarkCase

        bad_case = BenchmarkCase(
            case_id="err-case",
            language="python",
            error_family="name_error",
            error_text="NameError: name 'x' is not defined",
            source_snippet="",
            expected_error_family="name_error",
            expected_top1_kind="name_error",
            curated=False,
        )
        suite = BenchmarkSuite(cases=(bad_case,))

        mock_analyzer = MagicMock()
        mock_analyzer.analyze.side_effect = RuntimeError("simulated failure")

        runner = BenchmarkRunner(analyzer=mock_analyzer, suite=suite)
        result = runner.run()
        assert result.total_cases == 1
        assert result.case_results[0].error != ""
        assert result.case_results[0].correct_top1 is False

    def test_runner_uses_passed_suite_even_if_empty(self) -> None:
        from burhan.benchmark.suite import BenchmarkSuite
        empty = BenchmarkSuite(cases=())
        runner = BenchmarkRunner(suite=empty)
        result = runner.run()
        assert result.total_cases == 0
        assert result.diagnostic_accuracy == 0.0

    def test_typescript_family_is_compared_as_typescript(self) -> None:
        """A TS undefined-name diagnosis must not be scored as Python NameError."""
        case = BenchmarkCase(
            case_id="ts-family",
            language="typescript",
            error_family="typescript_missing_symbol",
            error_text="error TS2304: Cannot find name 'Widget'.",
            source_snippet="const x = Widget;",
            expected_error_family="typescript_missing_symbol",
            expected_top1_kind="typescript_missing_symbol",
            curated=True,
            ground_truth_repair="Import Widget",
        )
        result = BenchmarkRunner(suite=BenchmarkSuite(cases=(case,))).run()
        assert result.case_results[0].correct_top1 is True

    def test_false_positive_rate_counts_confident_negative_control_case(self) -> None:
        """Only cases with no expected family are negative controls."""
        from unittest.mock import MagicMock

        case = BenchmarkCase(
            case_id="fp-family",
            language="python",
            error_family="import_error",
            error_text="benign log line with no diagnostic target",
            source_snippet="print('ok')",
            expected_error_family="",
            expected_top1_kind="",
            curated=False,
        )
        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = SimpleNamespace(
            hypotheses=(SimpleNamespace(kind="undefined_name", confidence=0.95),)
        )

        result = BenchmarkRunner(
            analyzer=mock_analyzer,
            suite=BenchmarkSuite(cases=(case,)),
        ).run()

        assert result.false_positive_rate == 1.0
        assert result.negative_control_cases == 1
        assert result.case_results[0].is_false_positive is True

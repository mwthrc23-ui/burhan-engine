"""Tests for Phase 3: HypothesisEngine and RepairCandidates.

Covers mandatory test cases:
1. Python NameError in a single file
2. Python error caused by definition in another file
3. AttributeError appearing inside pytest
4. TypeScript property/type error
12. Smallest candidate chosen when multiple succeed
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

from burhan.diagnosis.hypothesis_engine import HypothesisEngine
from burhan.candidates.repair_candidates import (
    RepairCandidate,
    generate_candidates,
    select_smallest_successful,
)
from burhan.model import Evidence, Hypothesis


# ---------------------------------------------------------------------------
# HypothesisEngine – basic functionality
# ---------------------------------------------------------------------------

class HypothesisEngineBasicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HypothesisEngine()

    # -----------------------------------------------------------------------
    # Mandatory test 1: NameError in single file
    # -----------------------------------------------------------------------
    def test_name_error_single_file(self) -> None:
        error = (
            'File "app.py", line 3, in <module>\n'
            "NameError: name 'greet' is not defined"
        )
        # "greet" matches "greeter" at ratio ~0.83, above 0.72 cutoff
        hyps, _ = self.engine.generate(error, ("greeter",))
        self.assertGreater(len(hyps), 0)
        self.assertEqual(hyps[0].kind, "undefined_name")
        self.assertEqual(hyps[0].target, "greet")
        self.assertIsNotNone(hyps[0].location)
        # Replacement found (greeter is close enough)
        self.assertEqual(hyps[0].suggested_replacement, "greeter")

    def test_name_error_no_replacement_has_opposing_evidence(self) -> None:
        error = "NameError: name 'xyz123' is not defined"
        hyps, _ = self.engine.generate(error, ("alpha", "beta"))
        self.assertEqual(hyps[0].kind, "undefined_name")
        # No close match → opposing note present
        opposing = [ev for ev in hyps[0].evidence if ev.source.startswith("opposing:")]
        self.assertTrue(len(opposing) > 0)

    # -----------------------------------------------------------------------
    # Mandatory test 2: NameError caused by definition in another file
    # -----------------------------------------------------------------------
    def test_name_error_cross_file(self) -> None:
        """Simulates an error where `helper` is defined in utils.py but not imported."""
        error = (
            'File "main.py", line 5, in run\n'
            "NameError: name 'helper' is not defined"
        )
        # Project symbols include helper from utils.py – exact match so replacement = helper
        symbols = ("helper", "run", "main")
        hyps, _ = self.engine.generate(error, symbols)
        self.assertEqual(hyps[0].kind, "undefined_name")
        # exact match means replacement equals the target itself
        self.assertEqual(hyps[0].target, "helper")
        # The location should point to main.py
        self.assertIn("main.py", hyps[0].location or "")

    # -----------------------------------------------------------------------
    # Mandatory test 3: AttributeError inside pytest
    # -----------------------------------------------------------------------
    def test_attribute_error_in_pytest(self) -> None:
        error = (
            "FAILED tests/test_foo.py::test_something\n"
            'File "src/foo.py", line 12, in process\n'
            "AttributeError: 'NoneType' object has no attribute 'process'"
        )
        hyps, _ = self.engine.generate(error, ("process", "run"))
        self.assertGreater(len(hyps), 0)
        self.assertEqual(hyps[0].kind, "missing_attribute")
        self.assertIn("process", hyps[0].target)

    # -----------------------------------------------------------------------
    # Mandatory test 4: TypeScript error
    # -----------------------------------------------------------------------
    def test_typescript_property_error(self) -> None:
        error = (
            "src/user.ts(10,5): error TS2339: "
            "Property 'userName' does not exist on type 'User'"
        )
        hyps, _ = self.engine.generate(error, ("username", "User", "getName"))
        self.assertGreater(len(hyps), 0)
        self.assertEqual(hyps[0].kind, "missing_property")
        self.assertIn("userName", hyps[0].explanation)

    def test_typescript_argument_type(self) -> None:
        error = (
            "index.ts(3,10): error TS2345: "
            "Argument of type 'string' is not assignable to parameter of type 'number'"
        )
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "argument_type_mismatch")

    def test_typescript_wrong_arg_count(self) -> None:
        error = "app.ts(5,1): error TS2554: Expected 2 arguments, but got 1"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "wrong_argument_count")

    # -----------------------------------------------------------------------
    # Insufficient evidence path
    # -----------------------------------------------------------------------
    def test_insufficient_evidence_on_unknown_error(self) -> None:
        error = "some random unknown error without a pattern"
        hyps, qs = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "insufficient_evidence")
        self.assertLessEqual(hyps[0].confidence, 0.35)
        self.assertTrue(len(qs) > 0)

    # -----------------------------------------------------------------------
    # Multi-candidate output
    # -----------------------------------------------------------------------
    def test_sorted_by_confidence_desc(self) -> None:
        # Two clear patterns in one error text – verify ordering
        error = (
            'File "app.py", line 1\n'
            "NameError: name 'foo' is not defined"
        )
        hyps, _ = self.engine.generate(error, ("foo_bar",))
        for i in range(len(hyps) - 1):
            self.assertGreaterEqual(hyps[i].confidence, hyps[i + 1].confidence)

    def test_attribute_error_hypotheses(self) -> None:
        error = "AttributeError: 'list' object has no attribute 'append_item'"
        hyps, _ = self.engine.generate(error, ("append", "append_item"))
        self.assertEqual(hyps[0].kind, "missing_attribute")

    # -----------------------------------------------------------------------
    # Various error kinds
    # -----------------------------------------------------------------------
    def test_syntax_error(self) -> None:
        error = "SyntaxError: invalid syntax"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "syntax_error")

    def test_value_error(self) -> None:
        error = "ValueError: invalid literal for int() with base 10: 'abc'"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "value_error")

    def test_index_error(self) -> None:
        error = "IndexError: list index out of range"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "index_out_of_range")

    def test_key_error(self) -> None:
        error = "KeyError: 'missing_key'"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "missing_key")

    def test_zero_division(self) -> None:
        error = "ZeroDivisionError: division by zero"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "zero_division")

    def test_recursion_error(self) -> None:
        error = "RecursionError: maximum recursion depth exceeded"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "infinite_recursion")

    def test_file_not_found(self) -> None:
        error = "FileNotFoundError: 'data.csv'"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "file_not_found")

    def test_unbound_local(self) -> None:
        error = "UnboundLocalError: local variable 'counter' referenced before assignment"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "unbound_local_variable")

    def test_import_error(self) -> None:
        error = "ImportError: cannot import name 'parse' from 'json'"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "missing_import_name")

    def test_type_error_arg_count(self) -> None:
        error = "TypeError: foo() takes 2 positional arguments but 3 were given"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "wrong_argument_count")

    def test_not_callable(self) -> None:
        error = "TypeError: 'int' object is not callable"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "not_callable")

    def test_bad_operand(self) -> None:
        error = "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
        hyps, _ = self.engine.generate(error, ())
        self.assertEqual(hyps[0].kind, "unsupported_operand")

    def test_questions_deduplicated(self) -> None:
        # Single error, questions should not repeat
        error = "NameError: name 'foo' is not defined"
        _, qs = self.engine.generate(error, ())
        self.assertEqual(len(qs), len(set(qs)))


# ---------------------------------------------------------------------------
# RepairCandidates
# ---------------------------------------------------------------------------

class RepairCandidatesTests(unittest.TestCase):
    def _make_hyp(
        self,
        kind: str = "undefined_name",
        confidence: float = 0.85,
        target: str = "foo",
        location: str | None = "app.py:10",
        replacement: str | None = "foo_bar",
    ) -> Hypothesis:
        from burhan.energy import confidence_from_energy, hypothesis_energy
        ev = (Evidence("runtime", "NameError detected", 2.5),)
        energy = hypothesis_energy(ev, uncertainty=0.1, unresolved_constraints=0, estimated_change_size=1)
        conf = confidence_from_energy(energy, len(ev))
        return Hypothesis(
            kind=kind,
            target=target,
            explanation=f"test hypothesis {kind}",
            location=location,
            energy=energy,
            confidence=confidence,
            suggested_replacement=replacement,
            evidence=ev,
        )

    def test_generate_returns_ranked_list(self) -> None:
        hyps = (self._make_hyp("undefined_name", 0.9), self._make_hyp("missing_attribute", 0.7))
        candidates = generate_candidates(hyps)
        self.assertGreater(len(candidates), 0)
        self.assertEqual(candidates[0].rank, 1)
        for i in range(len(candidates) - 1):
            self.assertLessEqual(candidates[i].rank, candidates[i + 1].rank)

    def test_insufficient_evidence_excluded(self) -> None:
        hyps = (self._make_hyp("insufficient_evidence", 0.3, replacement=None),)
        candidates = generate_candidates(hyps)
        self.assertEqual(len(candidates), 0)

    def test_max_candidates_respected(self) -> None:
        hyps = tuple(self._make_hyp(confidence=0.9 - i * 0.05) for i in range(8))
        candidates = generate_candidates(hyps, max_candidates=3)
        self.assertLessEqual(len(candidates), 3)

    def test_max_candidates_validation(self) -> None:
        hyps = (self._make_hyp(),)
        with self.assertRaises(ValueError):
            generate_candidates(hyps, max_candidates=0)
        with self.assertRaises(ValueError):
            generate_candidates(hyps, max_candidates=11)

    def test_to_dict_serialisable(self) -> None:
        import json
        hyps = (self._make_hyp(),)
        candidates = generate_candidates(hyps)
        for c in candidates:
            json.dumps(c.to_dict())  # must not raise

    def test_description_includes_replacement(self) -> None:
        hyps = (self._make_hyp(replacement="foo_bar"),)
        candidates = generate_candidates(hyps)
        self.assertIn("foo_bar", candidates[0].description)

    def test_sorted_confidence_desc(self) -> None:
        hyps = (
            self._make_hyp("undefined_name", 0.6),
            self._make_hyp("missing_attribute", 0.9),
        )
        candidates = generate_candidates(hyps)
        self.assertGreaterEqual(candidates[0].confidence, candidates[-1].confidence)

    # -----------------------------------------------------------------------
    # Mandatory test 12: Select smallest successful candidate
    # -----------------------------------------------------------------------
    def test_select_smallest_successful(self) -> None:
        """When two candidates succeed, the one with fewer changed lines wins."""
        hyp = self._make_hyp()
        c_large = RepairCandidate(
            rank=1,
            hypothesis=hyp,
            description="large change",
            target_file="app.py",
            target_line=10,
            change_size=10,
            affected_files=("app.py",),
            confidence=0.9,
            risk="medium",
            supporting_evidence=("ev1",),
            opposing_evidence=(),
            rejection_reason="",
        )
        c_small = RepairCandidate(
            rank=2,
            hypothesis=hyp,
            description="small change",
            target_file="app.py",
            target_line=10,
            change_size=1,
            affected_files=("app.py",),
            confidence=0.8,
            risk="low",
            supporting_evidence=("ev1",),
            opposing_evidence=(),
            rejection_reason="",
        )
        result = select_smallest_successful((c_large, c_small))
        self.assertIsNotNone(result)
        self.assertEqual(result.change_size, 1)  # type: ignore[union-attr]

    def test_select_smallest_skips_rejected(self) -> None:
        hyp = self._make_hyp()
        c_rejected = RepairCandidate(
            rank=1, hypothesis=hyp, description="rejected", target_file="", target_line=0,
            change_size=1, affected_files=(), confidence=0.9, risk="low",
            supporting_evidence=(), opposing_evidence=(), rejection_reason="test failed",
        )
        c_ok = RepairCandidate(
            rank=2, hypothesis=hyp, description="ok", target_file="", target_line=0,
            change_size=3, affected_files=(), confidence=0.7, risk="low",
            supporting_evidence=(), opposing_evidence=(), rejection_reason="",
        )
        result = select_smallest_successful((c_rejected, c_ok))
        self.assertIsNotNone(result)
        self.assertEqual(result.description, "ok")  # type: ignore[union-attr]

    def test_select_returns_none_if_all_rejected(self) -> None:
        hyp = self._make_hyp()
        c = RepairCandidate(
            rank=1, hypothesis=hyp, description="r", target_file="", target_line=0,
            change_size=1, affected_files=(), confidence=0.9, risk="low",
            supporting_evidence=(), opposing_evidence=(), rejection_reason="failed",
        )
        result = select_smallest_successful((c,))
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Integration: HypothesisEngine → generate_candidates pipeline
# ---------------------------------------------------------------------------

class HypothesisToCandiatesIntegrationTests(unittest.TestCase):
    def test_name_error_produces_candidate(self) -> None:
        engine = HypothesisEngine()
        error = "NameError: name 'greet' is not defined"
        hyps, _ = engine.generate(error, ("greet_user",))
        candidates = generate_candidates(hyps)
        self.assertGreater(len(candidates), 0)
        self.assertEqual(candidates[0].rank, 1)

    def test_insufficient_evidence_produces_no_candidates(self) -> None:
        engine = HypothesisEngine()
        error = "an unrecognised message format"
        hyps, _ = engine.generate(error, ())
        candidates = generate_candidates(hyps)
        self.assertEqual(len(candidates), 0)


if __name__ == "__main__":
    unittest.main()

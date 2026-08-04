"""Tests for error family handlers (Phase 3)."""
from __future__ import annotations

import pytest

from burhan.diagnosis.error_families.key_error import KeyErrorHandler
from burhan.diagnosis.error_families.async_error import AsyncErrorHandler
from burhan.diagnosis.error_families.typescript import TypeScriptErrorHandler


# ---------------------------------------------------------------------------
# KeyError
# ---------------------------------------------------------------------------

class TestKeyErrorHandler:
    def setup_method(self) -> None:
        self.handler = KeyErrorHandler()

    def test_returns_hypotheses_for_key_error(self) -> None:
        result = self.handler.diagnose("KeyError: 'timeout'")
        assert len(result) >= 3

    def test_top_hypothesis_is_missing_key(self) -> None:
        result = self.handler.diagnose("KeyError: 'timeout'")
        assert result[0].kind == "key_error"
        assert result[0].confidence >= 0.5

    def test_candidates_present_for_top_hypothesis(self) -> None:
        result = self.handler.diagnose("KeyError: 'timeout'")
        assert len(result[0].candidates) >= 3

    def test_first_candidate_uses_get(self) -> None:
        result = self.handler.diagnose("KeyError: 'mykey'")
        top = result[0]
        assert any("get" in c.code_template for c in top.candidates)

    def test_in_guard_candidate_present(self) -> None:
        result = self.handler.diagnose("KeyError: 'mykey'")
        top = result[0]
        templates = " ".join(c.code_template for c in top.candidates)
        assert "in" in templates

    def test_hypotheses_sorted_by_confidence_desc(self) -> None:
        result = self.handler.diagnose("KeyError: 'x'")
        confs = [h.confidence for h in result]
        assert confs == sorted(confs, reverse=True)

    def test_no_hypotheses_without_key_error(self) -> None:
        result = self.handler.diagnose("NameError: name 'x' is not defined")
        assert result == ()

    def test_supporting_evidence_non_empty(self) -> None:
        result = self.handler.diagnose("KeyError: 'user_id'")
        assert result[0].supporting

    def test_to_dict_structure(self) -> None:
        result = self.handler.diagnose("KeyError: 'host'")
        d = result[0].to_dict()
        assert "kind" in d
        assert "confidence" in d
        assert "candidates" in d

    def test_candidate_to_dict_structure(self) -> None:
        result = self.handler.diagnose("KeyError: 'host'")
        c = result[0].candidates[0].to_dict()
        assert "rank" in c
        assert "description" in c
        assert "code_template" in c

    def test_integer_key(self) -> None:
        result = self.handler.diagnose("KeyError: 0")
        assert len(result) >= 1
        assert result[0].key_name in ("0", "")


# ---------------------------------------------------------------------------
# AsyncError
# ---------------------------------------------------------------------------

class TestAsyncErrorHandler:
    def setup_method(self) -> None:
        self.handler = AsyncErrorHandler()

    def test_coroutine_never_awaited(self) -> None:
        error = "RuntimeWarning: coroutine 'fetch' was never awaited"
        result = self.handler.diagnose(error)
        assert len(result) >= 2
        assert result[0].kind == "async_error"
        assert result[0].confidence >= 0.8

    def test_await_outside_async(self) -> None:
        error = "SyntaxError: 'await' outside async function"
        result = self.handler.diagnose(error)
        assert result[0].sub_kind == "await_outside_async"
        assert result[0].confidence >= 0.8

    def test_loop_already_running(self) -> None:
        error = "RuntimeError: This event loop is already running"
        result = self.handler.diagnose(error)
        assert any(h.sub_kind == "event_loop_already_running" for h in result)

    def test_coroutine_never_awaited_candidates(self) -> None:
        error = "RuntimeWarning: coroutine 'save' was never awaited"
        result = self.handler.diagnose(error)
        top = result[0]
        assert len(top.candidates) >= 3

    def test_await_outside_async_candidates(self) -> None:
        error = "SyntaxError: 'await' outside async function"
        result = self.handler.diagnose(error)
        top = result[0]
        templates = [c.code_template for c in top.candidates]
        assert any("async def" in t for t in templates)

    def test_no_match_returns_empty(self) -> None:
        result = self.handler.diagnose("ImportError: No module named 'x'")
        assert result == ()

    def test_sorted_by_confidence(self) -> None:
        error = "RuntimeWarning: coroutine 'fn' was never awaited"
        result = self.handler.diagnose(error)
        confs = [h.confidence for h in result]
        assert confs == sorted(confs, reverse=True)

    def test_hypotheses_have_supporting_evidence(self) -> None:
        error = "RuntimeWarning: coroutine 'fn' was never awaited"
        result = self.handler.diagnose(error)
        assert result[0].supporting

    def test_to_dict_has_required_keys(self) -> None:
        error = "RuntimeWarning: coroutine 'fn' was never awaited"
        result = self.handler.diagnose(error)
        d = result[0].to_dict()
        assert set(d.keys()) >= {"kind", "sub_kind", "explanation", "confidence", "candidates"}


# ---------------------------------------------------------------------------
# TypeScript errors
# ---------------------------------------------------------------------------

class TestTypeScriptErrorHandler:
    def setup_method(self) -> None:
        self.handler = TypeScriptErrorHandler()

    def test_ts2304_missing_name(self) -> None:
        error = "error TS2304: Cannot find name 'fetchData'."
        result = self.handler.diagnose(error)
        assert result
        assert result[0].kind == "typescript_missing_symbol"
        assert result[0].confidence >= 0.7

    def test_ts2339_missing_property(self) -> None:
        error = "error TS2339: Property 'toUppercase' does not exist on type 'string'."
        result = self.handler.diagnose(error)
        assert result[0].kind == "typescript_missing_symbol"
        assert result[0].confidence >= 0.7

    def test_ts2345_type_mismatch(self) -> None:
        error = (
            "error TS2345: Argument of type 'string' is not assignable "
            "to parameter of type 'number'."
        )
        result = self.handler.diagnose(error)
        assert result[0].kind == "typescript_type_mismatch"

    def test_ts2554_param_count(self) -> None:
        error = "error TS2554: Expected 2 arguments, but got 3."
        result = self.handler.diagnose(error)
        assert result[0].kind == "typescript_type_mismatch"
        assert result[0].sub_kind == "wrong_param_count"

    def test_missing_name_has_3_candidates(self) -> None:
        error = "error TS2304: Cannot find name 'useState'."
        result = self.handler.diagnose(error)
        assert len(result[0].candidates) >= 3

    def test_missing_property_has_3_candidates(self) -> None:
        error = "error TS2339: Property 'lenght' does not exist on type 'string[]'."
        result = self.handler.diagnose(error)
        assert len(result[0].candidates) >= 3

    def test_param_count_candidates(self) -> None:
        error = "error TS2554: Expected 1 arguments, but got 0."
        result = self.handler.diagnose(error)
        assert result[0].candidates

    def test_unknown_error_returns_empty(self) -> None:
        result = self.handler.diagnose("error TS9999: Unknown error.")
        assert result == ()

    def test_sorted_by_confidence(self) -> None:
        error = "error TS2304: Cannot find name 'X'."
        result = self.handler.diagnose(error)
        confs = [h.confidence for h in result]
        assert confs == sorted(confs, reverse=True)

    def test_to_dict_structure(self) -> None:
        error = "error TS2304: Cannot find name 'X'."
        result = self.handler.diagnose(error)
        d = result[0].to_dict()
        assert "kind" in d
        assert "sub_kind" in d
        assert "candidates" in d

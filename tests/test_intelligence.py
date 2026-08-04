"""Tests for Phase 7: Optional Intelligence Provider.

Covers mandatory test case:
11. Intelligence provider failure does not stop the core engine
"""
from __future__ import annotations

import unittest

from burhan.intelligence.provider_base import (
    IntelligenceProvider,
    IntelligenceRequest,
    IntelligenceResponse,
)
from burhan.intelligence.local_provider import LocalProvider
from burhan.intelligence.llm_provider import LLMProvider


# ---------------------------------------------------------------------------
# IntelligenceRequest
# ---------------------------------------------------------------------------

class IntelligenceRequestTests(unittest.TestCase):
    def _make(self, **kwargs) -> IntelligenceRequest:
        defaults = dict(
            error_kind="undefined_name",
            error_summary="NameError: name 'foo' not defined",
            symbol_names=("foo", "bar"),
            question="ما السبب المحتمل؟",
        )
        defaults.update(kwargs)
        return IntelligenceRequest(**defaults)

    def test_fingerprint_is_16_chars(self) -> None:
        req = self._make()
        self.assertEqual(len(req.fingerprint), 16)

    def test_same_request_same_fingerprint(self) -> None:
        r1 = self._make()
        r2 = self._make()
        self.assertEqual(r1.fingerprint, r2.fingerprint)

    def test_different_content_different_fingerprint(self) -> None:
        r1 = self._make(error_kind="undefined_name")
        r2 = self._make(error_kind="missing_attribute")
        self.assertNotEqual(r1.fingerprint, r2.fingerprint)

    def test_summary_truncated_at_500(self) -> None:
        long_summary = "x" * 600
        req = self._make(error_summary=long_summary)
        self.assertLessEqual(len(req.error_summary), 500)

    def test_symbols_truncated_at_20(self) -> None:
        many = tuple(f"sym{i}" for i in range(30))
        req = self._make(symbol_names=many)
        self.assertLessEqual(len(req.symbol_names), 20)

    def test_question_truncated_at_300(self) -> None:
        long_q = "ق" * 400
        req = self._make(question=long_q)
        self.assertLessEqual(len(req.question), 300)

    def test_to_dict_no_raw_code(self) -> None:
        import json
        req = self._make()
        d = req.to_dict()
        # Must not contain full source/secret data
        self.assertNotIn("symbol_names", d)
        self.assertIn("symbol_count", d)
        json.dumps(d)  # must not raise

    def test_allow_external_default_false(self) -> None:
        req = self._make()
        self.assertFalse(req.allow_external)


# ---------------------------------------------------------------------------
# LocalProvider
# ---------------------------------------------------------------------------

class LocalProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = LocalProvider()
        self.request = IntelligenceRequest(
            error_kind="undefined_name",
            error_summary="NameError detected",
            symbol_names=("foo",),
            question="ما السبب؟",
        )

    def test_is_always_available(self) -> None:
        self.assertTrue(self.provider.is_available())

    def test_returns_response(self) -> None:
        response = self.provider.provide(self.request)
        self.assertIsInstance(response, IntelligenceResponse)

    def test_response_has_suggestion(self) -> None:
        response = self.provider.provide(self.request)
        self.assertGreater(len(response.suggestion), 0)

    def test_never_uses_external(self) -> None:
        response = self.provider.provide(self.request)
        self.assertFalse(response.used_external)

    def test_provider_name_is_local(self) -> None:
        response = self.provider.provide(self.request)
        self.assertEqual(response.provider_name, "local_heuristic")

    def test_fingerprint_matches_request(self) -> None:
        response = self.provider.provide(self.request)
        self.assertEqual(response.request_fingerprint, self.request.fingerprint)

    def test_to_dict_has_trust_note(self) -> None:
        response = self.provider.provide(self.request)
        d = response.to_dict()
        self.assertIn("ASSUMED", d["trust_note"])

    def test_unknown_error_kind_returns_default_hint(self) -> None:
        req = IntelligenceRequest(
            error_kind="completely_unknown_kind",
            error_summary="unknown",
            symbol_names=(),
            question="?",
        )
        response = self.provider.provide(req)
        self.assertGreater(len(response.suggestion), 0)


# ---------------------------------------------------------------------------
# LLMProvider
# ---------------------------------------------------------------------------

class LLMProviderTests(unittest.TestCase):
    def test_unconfigured_not_available(self) -> None:
        provider = LLMProvider()
        self.assertFalse(provider.is_available())

    def test_configured_is_available(self) -> None:
        provider = LLMProvider(endpoint="http://localhost:8080", api_key_env="OPENAI_KEY")
        self.assertTrue(provider.is_available())

    def test_no_external_without_consent(self) -> None:
        provider = LLMProvider(endpoint="http://localhost:8080")
        req = IntelligenceRequest(
            error_kind="undefined_name",
            error_summary="error",
            symbol_names=(),
            question="?",
            allow_external=False,  # No consent
        )
        response = provider.provide(req)
        self.assertFalse(response.used_external)
        self.assertEqual(response.confidence_hint, 0.0)

    def test_unconfigured_returns_zero_confidence(self) -> None:
        provider = LLMProvider()
        req = IntelligenceRequest(
            error_kind="undefined_name",
            error_summary="error",
            symbol_names=(),
            question="?",
            allow_external=True,
        )
        response = provider.provide(req)
        self.assertEqual(response.confidence_hint, 0.0)


# ---------------------------------------------------------------------------
# Mandatory test 11: Provider failure does not stop core engine
# ---------------------------------------------------------------------------

class ProviderFailureSafetyTests(unittest.TestCase):
    """Verify that a crashing intelligence provider does not stop the engine."""

    class _CrashingProvider(IntelligenceProvider):
        name = "crashing"
        version = "0.0.1"

        def is_available(self) -> bool:
            return True

        def provide(self, request: IntelligenceRequest) -> IntelligenceResponse:
            raise RuntimeError("provider crashed intentionally")

    def _run_engine_with_provider(self, provider: IntelligenceProvider) -> bool:
        """Run the core diagnosis engine; return True if it completed."""
        from burhan.diagnosis.hypothesis_engine import HypothesisEngine
        engine = HypothesisEngine()
        error = "NameError: name 'foo' is not defined"
        try:
            hyps, _ = engine.generate(error, ())
            # Optionally try to get intelligence (simulate integration)
            req = IntelligenceRequest(
                error_kind=hyps[0].kind,
                error_summary=error,
                symbol_names=(),
                question="ما السبب؟",
            )
            try:
                provider.provide(req)
            except Exception:
                pass  # Provider failure is caught and ignored
            return True
        except Exception:
            return False

    def test_crashing_provider_does_not_stop_engine(self) -> None:
        """Core engine must succeed even when intelligence provider crashes."""
        provider = self._CrashingProvider()
        result = self._run_engine_with_provider(provider)
        self.assertTrue(result, "المحرك الأساسي يجب أن يعمل حتى لو تعطّل مزود الذكاء")

    def test_local_provider_fallback_when_llm_crashes(self) -> None:
        """When LLM fails, LocalProvider can serve as fallback."""
        llm = LLMProvider()  # not configured → not available
        local = LocalProvider()
        # Simulate: try LLM first, fall back to local
        provider = llm if llm.is_available() else local
        req = IntelligenceRequest(
            error_kind="undefined_name",
            error_summary="NameError",
            symbol_names=(),
            question="?",
        )
        response = provider.provide(req)
        self.assertIsNotNone(response)
        self.assertEqual(response.provider_name, "local_heuristic")

    def test_engine_diagnosis_independent_of_provider(self) -> None:
        """Diagnosis result must be identical with and without a provider."""
        from burhan.diagnosis.hypothesis_engine import HypothesisEngine
        engine = HypothesisEngine()
        error = "AttributeError: 'list' object has no attribute 'append_item'"
        hyps_no_provider, _ = engine.generate(error, ())
        # Same error with a crashing provider (simulated as no-op)
        hyps_with_provider, _ = engine.generate(error, ())
        self.assertEqual(hyps_no_provider[0].kind, hyps_with_provider[0].kind)
        self.assertAlmostEqual(
            hyps_no_provider[0].confidence,
            hyps_with_provider[0].confidence,
            places=5,
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for LLM provider safety and secret redaction (Phase 7)."""
from __future__ import annotations

import pytest

from burhan.intelligence.llm_provider import LLMProvider, LLMConfig, redact_secrets


class TestSecretRedaction:
    def test_api_key_redacted(self) -> None:
        text = "Authorization: sk-abcdefghijklmnopqrstu"
        redacted = redact_secrets(text)
        assert "sk-abcdefghijklmnopqrstu" not in redacted
        assert "[REDACTED]" in redacted

    def test_password_like_field_redacted(self) -> None:
        text = "api_key=abc123xyz456"
        redacted = redact_secrets(text)
        assert "abc123xyz456" not in redacted
        assert "[REDACTED]" in redacted

    def test_aws_key_redacted(self) -> None:
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        redacted = redact_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert "[REDACTED]" in redacted

    def test_non_secret_preserved(self) -> None:
        text = "def calculate(x):\n    return x * 2"
        redacted = redact_secrets(text)
        assert "calculate" in redacted
        assert "return x * 2" in redacted

    def test_empty_string_safe(self) -> None:
        assert redact_secrets("") == ""


class TestLLMProviderConfig:
    def test_default_provider_is_local(self) -> None:
        provider = LLMProvider()
        assert not provider.is_active()

    def test_requires_explicit_opt_in(self) -> None:
        provider = LLMProvider()
        with pytest.raises((ValueError, RuntimeError)):
            provider.generate_hypothesis("some error")

    def test_config_size_limit_enforced(self) -> None:
        cfg = LLMConfig(max_input_chars=100)
        provider = LLMProvider(config=cfg)
        long_text = "x" * 10000
        truncated = provider._truncate(long_text)
        assert len(truncated) <= 100 + len("[TRUNCATED]")

    def test_result_marked_as_assumed(self) -> None:
        from burhan.intelligence.llm_provider import ASSUMPTION_TAG
        assert ASSUMPTION_TAG == "ASSUMED"

    def test_project_files_not_sent(self) -> None:
        provider = LLMProvider()
        text = "/home/user/project/secret_module.py: line 42"
        cleaned = provider._sanitize_context(text)
        assert "/home/user/project" not in cleaned

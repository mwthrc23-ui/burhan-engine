"""Tests for encoding safety (Phase security)."""
from __future__ import annotations

import pytest

from burhan.intelligence.llm_provider import redact_secrets


class TestEncodingSafety:
    def test_arabic_text_preserved(self) -> None:
        text = "خطأ في البرنامج: NameError"
        result = redact_secrets(text)
        assert "خطأ في البرنامج" in result

    def test_chinese_text_preserved(self) -> None:
        text = "错误: 名称未定义"
        result = redact_secrets(text)
        assert "名称未定义" in result

    def test_null_bytes_handled(self) -> None:
        text = "error\x00text"
        # Should not crash
        result = redact_secrets(text)
        assert isinstance(result, str)

    def test_high_unicode_preserved(self) -> None:
        text = "error \U0001F4A5 boom"
        result = redact_secrets(text)
        assert "\U0001F4A5" in result

    def test_mixed_script_with_secret(self) -> None:
        text = "خطأ sk-abcdefghijklmnopqrstu البرنامج"
        result = redact_secrets(text)
        assert "sk-abcdefghijklmnopqrstu" not in result
        assert "خطأ" in result
        assert "البرنامج" in result

    def test_cp1256_compatible_chars(self) -> None:
        # cp1256 is Arabic Windows encoding; these chars must round-trip
        text = "نتيجة: صحيح"
        encoded = text.encode("cp1256")
        decoded = encoded.decode("cp1256")
        result = redact_secrets(decoded)
        assert "صحيح" in result

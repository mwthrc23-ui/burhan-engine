"""LLM intelligence provider – optional, requires explicit user consent.

This provider wraps external language model APIs.  It is disabled by
default and must be explicitly enabled with ``allow_external=True`` in the
``IntelligenceRequest``.

Security guarantees
-------------------
* If ``request.allow_external`` is False, the provider returns a
  zero-confidence response without making any external call.
* Raw source code is NEVER sent; only the redacted ``IntelligenceRequest``
  fields are transmitted.
* The provider cannot override sandbox policies, scope, or application rules.
* Provider output is always classified as ASSUMED trust.
* Secret patterns (API keys, passwords, AWS keys) are redacted before
  any text is processed or transmitted.
"""
from __future__ import annotations

import dataclasses
import re

from .provider_base import IntelligenceProvider, IntelligenceRequest, IntelligenceResponse

_VERSION = "1.0.0"

# Tag that must appear on every LLM-originated result.
ASSUMPTION_TAG = "ASSUMED"

# Patterns for common secret types to redact.
_SECRET_PATTERNS = [
    # OpenAI / generic ******
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.ASCII),
    # Generic password patterns: ******
    re.compile(r"(?i)password\s*[=:]\s*\S+"),
    # AWS Access Key ID
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # Generic API key assignment
    re.compile(r"(?i)api[_-]?key\s*[=:]\s*\S+"),
    # Authorization header value
    re.compile(r"(?i)Authorization\s*:\s*\S+"),
]

# Pattern to strip absolute file paths from context.
_ABS_PATH_PATTERN = re.compile(r"/(?:[a-zA-Z0-9_.\-]+/){2,}[a-zA-Z0-9_./-]*")


def redact_secrets(text: str) -> str:
    """Return *text* with known secret patterns replaced by ``[REDACTED]``."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


@dataclasses.dataclass(frozen=True)
class LLMConfig:
    """Configuration for the LLM provider.

    Parameters
    ----------
    max_input_chars:
        Maximum number of characters in the input sent to the model.
        Longer inputs are truncated.
    timeout_seconds:
        Maximum time to wait for a response (not enforced in stub).
    """

    max_input_chars: int = 4096
    timeout_seconds: float = 30.0


class LLMProvider(IntelligenceProvider):
    """Stub LLM provider – requires external service configuration.

    Set ``endpoint`` and ``api_key_env`` in the constructor to activate.
    Without configuration this provider is unavailable and returns a
    graceful zero-confidence response.

    Parameters
    ----------
    endpoint:
        HTTP(S) URL of the LLM API endpoint.  If empty the provider is
        disabled.
    api_key_env:
        Name of the environment variable that holds the API key.
        The key is NEVER stored in this object or logged.
    model_id:
        Model identifier string (for audit logging only).
    config:
        Optional ``LLMConfig``.
    """

    name = "llm"
    version = _VERSION

    def __init__(
        self,
        endpoint: str = "",
        api_key_env: str = "",
        model_id: str = "unspecified",
        config: LLMConfig | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._api_key_env = api_key_env
        self._model_id = model_id
        self._config = config or LLMConfig()

    def is_available(self) -> bool:
        """Return True only when an endpoint is configured."""
        return bool(self._endpoint)

    def is_active(self) -> bool:
        """Return True only when the provider is available (configured)."""
        return self.is_available()

    def generate_hypothesis(self, error_text: str) -> str:
        """Generate a hypothesis suggestion (ASSUMED, not verified).

        Raises ``RuntimeError`` if the provider is not active.
        All suggestions are tagged as ASSUMED.
        """
        if not self.is_active():
            raise RuntimeError(
                "LLMProvider is not active: set endpoint and api_key_env to enable. "
                "Result would be ASSUMED until proven by the sandbox."
            )
        # Real implementation calls self._endpoint.
        # Stub: return tagged ASSUMED suggestion.
        sanitized = self._sanitize_context(error_text)
        truncated = self._truncate(sanitized)
        return f"[{ASSUMPTION_TAG}] suggestion for: {truncated[:80]}"

    def _truncate(self, text: str) -> str:
        """Truncate *text* to the configured max_input_chars."""
        limit = self._config.max_input_chars
        if len(text) <= limit:
            return text
        return text[:limit] + "[TRUNCATED]"

    def _sanitize_context(self, text: str) -> str:
        """Remove secrets and absolute file paths from *text*."""
        text = redact_secrets(text)
        text = _ABS_PATH_PATTERN.sub("[PATH]", text)
        return text

    def provide(self, request: IntelligenceRequest) -> IntelligenceResponse:
        """Return a response, never sending data externally without consent.

        If ``request.allow_external`` is False or the provider is not
        configured, returns an empty-suggestion response.
        """
        if not request.allow_external or not self.is_available():
            return IntelligenceResponse(
                provider_name=self.name,
                provider_version=self.version,
                suggestion="",
                confidence_hint=0.0,
                request_fingerprint=request.fingerprint,
                used_external=False,
            )

        # Real implementation would call self._endpoint here.
        # Stub: always returns ASSUMED response (no real HTTP call).
        return IntelligenceResponse(
            provider_name=self.name,
            provider_version=self.version,
            suggestion=f"[{ASSUMPTION_TAG}] stub – real LLM call not implemented",
            confidence_hint=0.0,
            request_fingerprint=request.fingerprint,
            used_external=False,  # stub never calls external
        )

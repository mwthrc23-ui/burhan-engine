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
"""
from __future__ import annotations

from .provider_base import IntelligenceProvider, IntelligenceRequest, IntelligenceResponse

_VERSION = "1.0.0"


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
    """

    name = "llm"
    version = _VERSION

    def __init__(
        self,
        endpoint: str = "",
        api_key_env: str = "",
        model_id: str = "unspecified",
    ) -> None:
        self._endpoint = endpoint
        self._api_key_env = api_key_env
        self._model_id = model_id

    def is_available(self) -> bool:
        """Return True only when an endpoint is configured."""
        return bool(self._endpoint)

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
        # Stub: always returns empty response (no real HTTP call).
        return IntelligenceResponse(
            provider_name=self.name,
            provider_version=self.version,
            suggestion="[stub – real LLM call not implemented]",
            confidence_hint=0.0,
            request_fingerprint=request.fingerprint,
            used_external=False,  # stub never calls external
        )

"""Abstract base for optional intelligence providers.

Contracts
---------
* The engine MUST continue to function if ``provide`` raises any exception.
* Providers MUST NOT send raw source code or secrets externally without
  explicit user consent (``allow_external=True``).
* Provider output is ALWAYS classified as ``ConfidenceLevel.ASSUMED`` until
  validated by the engine's own tools and tests.
* No provider may override sandbox, scope, or application policies.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class IntelligenceRequest:
    """A redacted, size-limited context passed to an intelligence provider.

    The request contains ONLY information that has already been sanitised:
    no absolute paths, no secrets, no raw file contents.

    Attributes
    ----------
    error_kind:
        Machine-readable error category (e.g. ``"undefined_name"``).
    error_summary:
        Short sanitised summary of the error (max 500 chars).
    symbol_names:
        Relevant symbol names from the project index (max 20).
    question:
        The specific question to answer (max 300 chars).
    allow_external:
        If True the provider may send data to an external service.
        Defaults to False – providers MUST respect this flag.
    """

    error_kind: str
    error_summary: str
    symbol_names: tuple[str, ...]
    question: str
    allow_external: bool = False

    def __post_init__(self) -> None:
        if len(self.error_summary) > 500:
            object.__setattr__(self, "error_summary", self.error_summary[:500])
        if len(self.symbol_names) > 20:
            object.__setattr__(self, "symbol_names", self.symbol_names[:20])
        if len(self.question) > 300:
            object.__setattr__(self, "question", self.question[:300])

    @property
    def fingerprint(self) -> str:
        """SHA-256 fingerprint of the redacted request (for audit logging)."""
        raw = (
            f"{self.error_kind}\0{self.error_summary}\0"
            f"{','.join(self.symbol_names)}\0{self.question}"
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_kind": self.error_kind,
            # Truncate summary to avoid leaking full error in logs
            "error_summary": self.error_summary[:200],
            "symbol_count": len(self.symbol_names),
            "question_length": len(self.question),
            "allow_external": self.allow_external,
            "request_fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class IntelligenceResponse:
    """Unverified output from an intelligence provider.

    IMPORTANT: All hypotheses produced from this response must be
    classified as ``ConfidenceLevel.ASSUMED`` and validated by the engine's
    own tools before being promoted.

    Attributes
    ----------
    provider_name:
        Identifier of the provider that produced this response.
    provider_version:
        Version string of the provider.
    suggestion:
        Suggested explanation or next step (max 500 chars).
    confidence_hint:
        Provider's self-reported confidence [0, 1].  Treated as advisory.
    request_fingerprint:
        Fingerprint of the request that triggered this response.
    used_external:
        Whether the provider contacted an external service.
    """

    provider_name: str
    provider_version: str
    suggestion: str
    confidence_hint: float
    request_fingerprint: str
    used_external: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "provider_version": self.provider_version,
            # Truncate suggestion to avoid log pollution
            "suggestion_preview": self.suggestion[:200],
            "confidence_hint": self.confidence_hint,
            "request_fingerprint": self.request_fingerprint,
            "used_external": self.used_external,
            "trust_note": "ASSUMED – must be validated by engine tools before use",
        }


class IntelligenceProvider:
    """Abstract base class for intelligence providers.

    Subclasses MUST implement ``provide`` and MUST NOT violate the
    contracts described in the module docstring.
    """

    name: str = "abstract"
    version: str = "0.0.0"

    def provide(self, request: IntelligenceRequest) -> IntelligenceResponse:
        """Return a response for *request*.

        Implementations MUST:
        * Respect ``request.allow_external`` – never call external services
          if it is ``False``.
        * Truncate or redact any sensitive content before external calls.
        * Return gracefully even when the underlying service is unavailable;
          callers catch ``Exception`` but a graceful ``IntelligenceResponse``
          with ``confidence_hint=0.0`` is preferred.
        """
        raise NotImplementedError

    def is_available(self) -> bool:
        """Return True if the provider is ready to serve requests."""
        return False

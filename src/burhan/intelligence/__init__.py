"""Optional intelligence provider interface for Burhan.

The core engine works entirely without a language model.  This sub-package
provides an optional, pluggable intelligence layer that:

* Accepts only redacted, limited context (no raw source, no secrets).
* Treats LLM output as *unverified hypotheses* – never as ground truth.
* Does not allow overriding sandbox policies or scope restrictions.
* Records provider type, version, and a fingerprint of the redacted request.
"""
from .provider_base import IntelligenceProvider, IntelligenceRequest, IntelligenceResponse
from .local_provider import LocalProvider

__all__ = [
    "IntelligenceProvider",
    "IntelligenceRequest",
    "IntelligenceResponse",
    "LocalProvider",
]

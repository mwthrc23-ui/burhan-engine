"""Error family handlers for Burhan diagnosis engine.

Each sub-module implements a ``FamilyHandler`` that produces parallel
hypotheses and ranked repair candidates for one error family.

Available families
------------------
* key_error        — KeyError
* async_error      — RuntimeWarning / SyntaxError for async/await
* typescript       — TypeScript TS2304 / TS2339 / TS2345 / TS2554

Design rules (applies to all handlers)
---------------------------------------
* No state mutation — every method returns new objects.
* No network calls.
* Each handler produces ≥3 hypotheses when evidence supports it.
* Each hypothesis records supporting and opposing evidence.
* Confidence stays ASSUMED until proven by the sandbox.
"""

from .key_error import KeyErrorHandler
from .async_error import AsyncErrorHandler
from .typescript import TypeScriptErrorHandler

__all__ = [
    "KeyErrorHandler",
    "AsyncErrorHandler",
    "TypeScriptErrorHandler",
]

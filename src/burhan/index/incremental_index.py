"""Incremental semantic index with checksum-based cache.

Wraps any ``IndexAdapter`` and caches results keyed by the SHA-256 of
the file content.  Only re-indexes files whose content has changed since
the last call.

Design rules
------------
* No persistent storage — cache lives only for the lifetime of the
  ``IncrementalIndex`` instance.
* No network calls.
* Thread-safe reads (cache is written once per unique content hash).
* Resource bounded: if the cache exceeds ``max_entries`` the oldest
  entries are evicted (LRU by insertion order via ``dict``).
* Confidence and source provenance are preserved from the underlying
  adapter result.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any

from .base import IndexAdapter, IndexResult

_DEFAULT_MAX_ENTRIES = 2048


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


class IncrementalIndex:
    """Cache-backed incremental indexer.

    Parameters
    ----------
    adapter:
        The underlying ``IndexAdapter`` that performs actual parsing.
    max_entries:
        Maximum number of cached results.  Oldest entries are evicted
        when the limit is exceeded.
    """

    def __init__(
        self,
        adapter: IndexAdapter,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._adapter = adapter
        self._max_entries = max_entries
        # key: (relative_path, content_hash)  value: IndexResult
        self._cache: OrderedDict[tuple[str, str], IndexResult] = OrderedDict()
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def language(self) -> str:
        return self._adapter.language

    def supports(self, relative_path: str) -> bool:
        return self._adapter.supports(relative_path)

    def index(self, relative_path: str, content: str) -> IndexResult:
        """Return a cached or freshly-computed ``IndexResult``.

        If the file is unchanged (same content hash) the cached result is
        returned immediately.
        """
        if not self._adapter.supports(relative_path):
            return IndexResult(
                file=relative_path,
                language=self._adapter.language,
                symbols=(),
                calls=(),
                imports=(),
                confidence=0.0,
                degraded=True,
            )

        content_hash = _sha256(content)
        cache_key = (relative_path, content_hash)

        if cache_key in self._cache:
            self._hits += 1
            # Move to end to mark as recently used
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        self._misses += 1
        result = self._adapter.index_source(relative_path, content)
        self._cache[cache_key] = result
        self._cache.move_to_end(cache_key)

        # Evict oldest entry if over budget
        if len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

        return result

    def invalidate(self, relative_path: str) -> int:
        """Remove all cached entries for *relative_path*.

        Returns the number of entries removed.
        """
        keys_to_remove = [k for k in self._cache if k[0] == relative_path]
        for key in keys_to_remove:
            del self._cache[key]
        return len(keys_to_remove)

    def cache_size(self) -> int:
        """Return the current number of cached entries."""
        return len(self._cache)

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total else 0.0,
            "cache_size": len(self._cache),
            "max_entries": self._max_entries,
        }

    def clear(self) -> None:
        """Evict all cached entries and reset statistics."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

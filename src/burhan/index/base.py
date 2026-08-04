"""Base interface for semantic index adapters.

Each language adapter implements ``IndexAdapter`` and returns an
``IndexResult`` that is language-agnostic and safe to serialise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SymbolDef:
    """A symbol (function, class, variable) defined in a source file."""

    name: str
    kind: str  # "function" | "class" | "variable" | "method" | "interface" | "type"
    file: str
    line: int
    column: int = 0
    parent: str | None = None  # enclosing class/function name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "parent": self.parent,
        }


@dataclass(frozen=True, slots=True)
class CallRef:
    """A call-site reference: *caller* calls *callee* at *file*:*line*."""

    caller: str
    callee: str
    file: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "caller": self.caller,
            "callee": self.callee,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class ImportRef:
    """An import statement: *file* imports *name* from *module*."""

    file: str
    module: str
    name: str  # the imported symbol; "*" for star imports

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "module": self.module, "name": self.name}


@dataclass(frozen=True, slots=True)
class IndexResult:
    """Language-agnostic result of indexing one file."""

    file: str
    language: str
    symbols: tuple[SymbolDef, ...]
    calls: tuple[CallRef, ...]
    imports: tuple[ImportRef, ...]
    confidence: float = 1.0  # reduced when using degraded (non-AST) mode
    degraded: bool = False  # True when external tooling was unavailable

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "language": self.language,
            "symbols": [s.to_dict() for s in self.symbols],
            "calls": [c.to_dict() for c in self.calls],
            "imports": [i.to_dict() for i in self.imports],
            "confidence": self.confidence,
            "degraded": self.degraded,
        }


class IndexAdapter:
    """Abstract base: override ``index_source`` in language subclasses."""

    language: str = "unknown"

    def index_source(self, relative_path: str, content: str) -> IndexResult:  # noqa: ARG002
        """Return an ``IndexResult`` for the given file content.

        Implementations MUST NOT send content to external services.
        """
        raise NotImplementedError

    def supports(self, relative_path: str) -> bool:
        """Return True if this adapter can handle the given path."""
        raise NotImplementedError

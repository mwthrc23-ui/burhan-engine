"""Python semantic indexer using the standard ``ast`` module.

No external dependencies required.  Falls back to an empty result (with
``degraded=True``) on ``SyntaxError`` rather than crashing.

Enhanced facts collected
------------------------
* Symbol definitions (functions, classes, methods, variables, async fns)
* Call graph edges (caller → callee)
* Import statements
* Class inheritance (base class names)
* Symbol use-sites (Name loads inside function/method bodies)
* Test-linked symbols (functions whose names start with ``test_``)

Each fact carries a ``confidence`` and a ``source`` tag so downstream
consumers can weight evidence appropriately.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from .base import CallRef, ImportRef, IndexAdapter, IndexResult, SymbolDef

_SUPPORTED = frozenset({".py", ".pyi"})


def _suffix(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot >= 0 else ""


# ---------------------------------------------------------------------------
# Extra semantic facts (additive — do not break existing callers)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class InheritanceRef:
    """Records that *child_class* in *file* inherits from *base_class*."""

    child_class: str
    base_class: str
    file: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_class": self.child_class,
            "base_class": self.base_class,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class UseRef:
    """A name *used* (loaded) inside *scope* at *file*:*line*."""

    name: str
    scope: str
    file: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope": self.scope,
            "file": self.file,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class EnhancedIndexResult:
    """Extended result that includes inheritance and use-sites.

    Fully backward-compatible: callers that only need ``IndexResult`` can
    use the ``to_index_result()`` method.
    """

    base: IndexResult
    inheritance: tuple[InheritanceRef, ...]
    uses: tuple[UseRef, ...]
    test_symbols: tuple[str, ...]  # names of test functions/methods

    def to_index_result(self) -> IndexResult:
        return self.base

    def to_dict(self) -> dict[str, Any]:
        d = self.base.to_dict()
        d["inheritance"] = [i.to_dict() for i in self.inheritance]
        d["uses"] = [u.to_dict() for u in self.uses]
        d["test_symbols"] = list(self.test_symbols)
        return d


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------

class _SymbolVisitor(ast.NodeVisitor):
    """Collect definitions, calls, imports, inheritance and uses."""

    def __init__(self, file: str) -> None:
        self._file = file
        self.symbols: list[SymbolDef] = []
        self.calls: list[CallRef] = []
        self.imports: list[ImportRef] = []
        self.inheritance: list[InheritanceRef] = []
        self.uses: list[UseRef] = []
        self._scope_stack: list[str] = []

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _current_scope(self) -> str | None:
        return self._scope_stack[-1] if self._scope_stack else None

    def _push(self, name: str) -> None:
        self._scope_stack.append(name)

    def _pop(self) -> None:
        if self._scope_stack:
            self._scope_stack.pop()

    def _caller_name(self) -> str:
        return ".".join(self._scope_stack) if self._scope_stack else "<module>"

    # ------------------------------------------------------------------
    # symbol visitors
    # ------------------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        parent = self._current_scope()
        kind = "method" if parent else "function"
        self.symbols.append(
            SymbolDef(
                name=node.name,
                kind=kind,
                file=self._file,
                line=node.lineno,
                column=node.col_offset,
                parent=parent,
            )
        )
        self._push(node.name)
        self.generic_visit(node)
        self._pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        parent = self._current_scope()
        self.symbols.append(
            SymbolDef(
                name=node.name,
                kind="class",
                file=self._file,
                line=node.lineno,
                column=node.col_offset,
                parent=parent,
            )
        )
        # Collect inheritance
        for base in node.bases:
            base_name = _resolve_call_name(base)
            if base_name:
                self.inheritance.append(
                    InheritanceRef(
                        child_class=node.name,
                        base_class=base_name,
                        file=self._file,
                        line=node.lineno,
                    )
                )
        self._push(node.name)
        self.generic_visit(node)
        self._pop()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        if not self._scope_stack:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.symbols.append(
                        SymbolDef(
                            name=target.id,
                            kind="variable",
                            file=self._file,
                            line=node.lineno,
                            column=node.col_offset,
                        )
                    )
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # call visitors
    # ------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        callee = _resolve_call_name(node.func)
        if callee:
            self.calls.append(
                CallRef(
                    caller=self._caller_name(),
                    callee=callee,
                    file=self._file,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # use-site visitors (Name loads only, inside scopes)
    # ------------------------------------------------------------------

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load) and self._scope_stack:
            self.uses.append(
                UseRef(
                    name=node.id,
                    scope=self._caller_name(),
                    file=self._file,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # import visitors
    # ------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.imports.append(
                ImportRef(file=self._file, module=alias.name, name=alias.name)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            self.imports.append(
                ImportRef(file=self._file, module=module, name=alias.name)
            )


def _resolve_call_name(node: ast.expr) -> str | None:
    """Return a dotted name for a call target, or None if too complex."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _resolve_call_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return node.attr
    return None


class PythonIndexer(IndexAdapter):
    """Index Python source files using ``ast``.

    Returns an ``IndexResult`` for backward compatibility.  Call
    ``index_enhanced`` to obtain the full ``EnhancedIndexResult``.
    """

    language = "python"

    def supports(self, relative_path: str) -> bool:
        return _suffix(relative_path) in _SUPPORTED

    def index_source(self, relative_path: str, content: str) -> IndexResult:
        return self.index_enhanced(relative_path, content).to_index_result()

    def index_enhanced(self, relative_path: str, content: str) -> EnhancedIndexResult:
        """Return the full enhanced index including inheritance and uses."""
        try:
            tree = ast.parse(content, filename=relative_path)
        except SyntaxError:
            base = IndexResult(
                file=relative_path,
                language=self.language,
                symbols=(),
                calls=(),
                imports=(),
                confidence=0.0,
                degraded=True,
            )
            return EnhancedIndexResult(
                base=base,
                inheritance=(),
                uses=(),
                test_symbols=(),
            )

        visitor = _SymbolVisitor(relative_path)
        visitor.visit(tree)

        test_symbols = tuple(
            sym.name
            for sym in visitor.symbols
            if sym.name.startswith("test_")
        )

        base = IndexResult(
            file=relative_path,
            language=self.language,
            symbols=tuple(visitor.symbols),
            calls=tuple(visitor.calls),
            imports=tuple(visitor.imports),
            confidence=1.0,
            degraded=False,
        )
        return EnhancedIndexResult(
            base=base,
            inheritance=tuple(visitor.inheritance),
            uses=tuple(visitor.uses),
            test_symbols=test_symbols,
        )


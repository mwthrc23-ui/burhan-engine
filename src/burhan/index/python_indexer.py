"""Python semantic indexer using the standard ``ast`` module.

No external dependencies required.  Falls back to an empty result (with
``degraded=True``) on ``SyntaxError`` rather than crashing.
"""
from __future__ import annotations

import ast
from typing import Any

from .base import CallRef, ImportRef, IndexAdapter, IndexResult, SymbolDef

_SUPPORTED = frozenset({".py", ".pyi"})


def _suffix(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot >= 0 else ""


class _SymbolVisitor(ast.NodeVisitor):
    """Collect definitions, calls and imports from a Python AST."""

    def __init__(self, file: str) -> None:
        self._file = file
        self.symbols: list[SymbolDef] = []
        self.calls: list[CallRef] = []
        self.imports: list[ImportRef] = []
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
        self._push(node.name)
        self.generic_visit(node)
        self._pop()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        # Only module-level simple assignments become variable symbols.
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
    """Index Python source files using ``ast``."""

    language = "python"

    def supports(self, relative_path: str) -> bool:
        return _suffix(relative_path) in _SUPPORTED

    def index_source(self, relative_path: str, content: str) -> IndexResult:
        try:
            tree = ast.parse(content, filename=relative_path)
        except SyntaxError:
            return IndexResult(
                file=relative_path,
                language=self.language,
                symbols=(),
                calls=(),
                imports=(),
                confidence=0.0,
                degraded=True,
            )

        visitor = _SymbolVisitor(relative_path)
        visitor.visit(tree)
        return IndexResult(
            file=relative_path,
            language=self.language,
            symbols=tuple(visitor.symbols),
            calls=tuple(visitor.calls),
            imports=tuple(visitor.imports),
            confidence=1.0,
            degraded=False,
        )

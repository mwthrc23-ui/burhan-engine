"""TypeScript/JavaScript semantic indexer.

Uses regex-based analysis (degraded mode) because Tree-sitter and the
TypeScript Compiler API are not required dependencies.  Confidence is
reduced to 0.6 to reflect this limitation.

When tree-sitter-python is available in the environment this module will
be extended to use it.  External services are never contacted.
"""
from __future__ import annotations

import re

from .base import CallRef, ImportRef, IndexAdapter, IndexResult, SymbolDef

_SUPPORTED = frozenset({".ts", ".tsx", ".js", ".jsx"})

# Regex patterns for common TS/JS constructs (best-effort).
_FUNC_DECL = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("
)
_ARROW_FUNC = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\("
)
_CLASS_DECL = re.compile(r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)\b")
_INTERFACE_DECL = re.compile(r"(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)\b")
_TYPE_ALIAS = re.compile(r"(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=")
_METHOD_DECL = re.compile(
    r"^\s+(?:async\s+|static\s+|public\s+|private\s+|protected\s+)*"
    r"([A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)
_CALL_SITE = re.compile(r"\b([A-Za-z_$][\w$.]*)\s*\(")
_IMPORT_FROM = re.compile(
    r"(import\s+(?:\*\s+as\s+\w+|\{[^}]*\}|[A-Za-z_$][\w$]*))\s+from\s+['\"]([^'\"]+)['\"]"
)
_IMPORT_NAMES = re.compile(r"[A-Za-z_$][\w$]*")


def _suffix(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot >= 0 else ""


class TypeScriptIndexer(IndexAdapter):
    """Index TypeScript/JavaScript files using regex (degraded mode)."""

    language = "typescript"

    def supports(self, relative_path: str) -> bool:
        return _suffix(relative_path) in _SUPPORTED

    def index_source(self, relative_path: str, content: str) -> IndexResult:
        symbols: list[SymbolDef] = []
        calls: list[CallRef] = []
        imports: list[ImportRef] = []

        lines = content.splitlines()

        for lineno, line in enumerate(lines, start=1):
            # Functions
            for match in _FUNC_DECL.finditer(line):
                symbols.append(
                    SymbolDef(
                        name=match.group(1),
                        kind="function",
                        file=relative_path,
                        line=lineno,
                        column=match.start(1),
                    )
                )
            # Arrow functions / const fn
            for match in _ARROW_FUNC.finditer(line):
                symbols.append(
                    SymbolDef(
                        name=match.group(1),
                        kind="function",
                        file=relative_path,
                        line=lineno,
                        column=match.start(1),
                    )
                )
            # Classes
            for match in _CLASS_DECL.finditer(line):
                symbols.append(
                    SymbolDef(
                        name=match.group(1),
                        kind="class",
                        file=relative_path,
                        line=lineno,
                        column=match.start(1),
                    )
                )
            # Interfaces
            for match in _INTERFACE_DECL.finditer(line):
                symbols.append(
                    SymbolDef(
                        name=match.group(1),
                        kind="interface",
                        file=relative_path,
                        line=lineno,
                        column=match.start(1),
                    )
                )
            # Type aliases
            for match in _TYPE_ALIAS.finditer(line):
                symbols.append(
                    SymbolDef(
                        name=match.group(1),
                        kind="type",
                        file=relative_path,
                        line=lineno,
                        column=match.start(1),
                    )
                )
            # Call sites (best-effort; filter out keywords)
            _KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "return"})
            for match in _CALL_SITE.finditer(line):
                callee = match.group(1)
                if callee not in _KEYWORDS:
                    calls.append(
                        CallRef(
                            caller="<module>",
                            callee=callee,
                            file=relative_path,
                            line=lineno,
                        )
                    )

        # Imports
        for match in _IMPORT_FROM.finditer(content):
            module = match.group(2)
            raw = match.group(0)
            # Extract individual names from {A, B, C}
            brace_match = re.search(r"\{([^}]*)\}", raw)
            if brace_match:
                for name_match in _IMPORT_NAMES.finditer(brace_match.group(1)):
                    imports.append(
                        ImportRef(file=relative_path, module=module, name=name_match.group())
                    )
            else:
                star_match = re.search(r"\*\s+as\s+(\w+)", raw)
                if star_match:
                    imports.append(
                        ImportRef(file=relative_path, module=module, name="*")
                    )
                else:
                    default_match = re.search(r"import\s+([A-Za-z_$][\w$]*)", raw)
                    if default_match:
                        imports.append(
                            ImportRef(
                                file=relative_path,
                                module=module,
                                name=default_match.group(1),
                            )
                        )

        return IndexResult(
            file=relative_path,
            language=self.language,
            symbols=tuple(symbols),
            calls=tuple(calls),
            imports=tuple(imports),
            confidence=0.6,
            degraded=True,  # regex mode
        )

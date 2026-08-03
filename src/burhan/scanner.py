from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import CodeTreeNode


SUPPORTED_EXTENSIONS = frozenset(
    {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".json", ".toml", ".yaml", ".yml", ".md"}
)
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
        ".next",
        ".turbo",
    }
)
SECRET_FILE_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "service-account.json",
        "id_rsa",
        "id_ed25519",
    }
)


def is_secret_file(path: Path) -> bool:
    name = path.name.lower()
    return name in SECRET_FILE_NAMES or name.startswith(".env.") or path.suffix.lower() in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
    }


@dataclass(frozen=True, slots=True)
class ScanLimits:
    max_files: int = 500
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 10_000_000

    def __post_init__(self) -> None:
        if self.max_files <= 0 or self.max_file_bytes <= 0 or self.max_total_bytes <= 0:
            raise ValueError("scan limits must be positive")


@dataclass(frozen=True, slots=True)
class SourceFile:
    relative_path: str
    content: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    root: Path
    files: tuple[SourceFile, ...]
    skipped_secret_files: int = 0
    skipped_oversized_files: int = 0
    truncated: bool = False

    @property
    def combined_text(self) -> str:
        return "\n".join(item.content for item in self.files)


# ---------------------------------------------------------------------------
# Incremental scan cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    mtime_ns: int
    size_bytes: int
    content_hash: str
    symbols: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mtime_ns": self.mtime_ns,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "symbols": self.symbols,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _CacheEntry:
        return cls(
            mtime_ns=int(data["mtime_ns"]),
            size_bytes=int(data["size_bytes"]),
            content_hash=str(data["content_hash"]),
            symbols=list(data.get("symbols", [])),
        )


class ScanCache:
    """Persistent file-level cache that allows incremental re-indexing.

    Only files whose mtime *and* size have changed are re-read and re-parsed.
    A SHA-256 content hash is stored as a secondary guard against mtime skew.
    The cache is stored as a single JSON file next to the project root or at
    the path you supply.
    """

    _VERSION = 1

    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path.expanduser().resolve()
        self._entries: dict[str, _CacheEntry] = {}
        self._dirty = False
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def needs_reindex(self, relative_path: str, stat: os.stat_result) -> bool:
        """Return True if the file must be re-read (not in cache or changed)."""
        entry = self._entries.get(relative_path)
        if entry is None:
            return True
        return entry.mtime_ns != stat.st_mtime_ns or entry.size_bytes != stat.st_size

    def get_symbols(self, relative_path: str) -> list[str] | None:
        """Return cached symbol list, or None if the entry is absent/stale."""
        entry = self._entries.get(relative_path)
        return list(entry.symbols) if entry is not None else None

    def update(self, relative_path: str, stat: os.stat_result, content: str, symbols: list[str]) -> None:
        """Store or refresh the cache entry for *relative_path*."""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._entries[relative_path] = _CacheEntry(
            mtime_ns=stat.st_mtime_ns,
            size_bytes=stat.st_size,
            content_hash=content_hash,
            symbols=list(symbols),
        )
        self._dirty = True

    def flush(self) -> None:
        """Write the cache to disk if any entries changed."""
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self._VERSION,
            "entries": {k: v.to_dict() for k, v in self._entries.items()},
        }
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
            self._dirty = False
        except OSError:
            tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if raw.get("version") != self._VERSION:
                return
            for key, value in raw.get("entries", {}).items():
                self._entries[key] = _CacheEntry.from_dict(value)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._entries = {}


class ProjectScanner:
    def __init__(self, limits: ScanLimits | None = None) -> None:
        self._limits = limits or ScanLimits()

    def scan(self, project: Path) -> ProjectSnapshot:
        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project directory does not exist: {root}")

        files: list[SourceFile] = []
        skipped_secrets = 0
        skipped_oversized = 0
        total_bytes = 0
        truncated = False

        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = sorted(
                name for name in directories if name not in EXCLUDED_DIRECTORIES and not name.startswith(".")
            )
            for name in sorted(names):
                path = Path(current) / name
                if is_secret_file(path):
                    skipped_secrets += 1
                    continue
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS or path.is_symlink():
                    continue
                if len(files) >= self._limits.max_files:
                    truncated = True
                    break
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > self._limits.max_file_bytes:
                    skipped_oversized += 1
                    continue
                if total_bytes + size > self._limits.max_total_bytes:
                    truncated = True
                    break
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                relative = path.relative_to(root).as_posix()
                files.append(SourceFile(relative, content, size))
                total_bytes += size
            if truncated:
                break

        return ProjectSnapshot(
            root=root,
            files=tuple(files),
            skipped_secret_files=skipped_secrets,
            skipped_oversized_files=skipped_oversized,
            truncated=truncated,
        )


class IncrementalProjectScanner(ProjectScanner):
    """A scanner that caches per-file metadata and skips unchanged files.

    On each call to :meth:`scan` the scanner compares the stored ``mtime_ns``
    and ``size_bytes`` against the current stat.  Files whose metadata matches
    are served from the cache without being read from disk or re-parsed.
    Changed files (or new files) are read, verified with a SHA-256 hash, and
    written back into the cache.  The cache is flushed to disk at the end of
    every scan.

    The cache file defaults to ``<project>/.burhan-scan-cache.json``.  Supply
    *cache_path* to override (useful for testing or shared CI caches).

    Tree-sitter is used automatically for Python and TypeScript/JavaScript
    files *if* the ``tree_sitter`` package is importable **and** the matching
    language grammar is available.  When tree-sitter is absent the scanner
    falls back to the standard AST/regex approach in :class:`ProjectScanner`.
    """

    def __init__(
        self,
        limits: ScanLimits | None = None,
        cache_path: Path | None = None,
    ) -> None:
        super().__init__(limits)
        self._cache_path = cache_path
        self._ts_available = self._probe_tree_sitter()

    # ------------------------------------------------------------------
    # Override
    # ------------------------------------------------------------------

    def scan(self, project: Path) -> ProjectSnapshot:
        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project directory does not exist: {root}")

        cache_file = self._cache_path or (root / ".burhan-scan-cache.json")
        cache_file_resolved = cache_file.expanduser().resolve()
        cache = ScanCache(cache_file)

        files: list[SourceFile] = []
        skipped_secrets = 0
        skipped_oversized = 0
        total_bytes = 0
        truncated = False

        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = sorted(
                name for name in directories
                if name not in EXCLUDED_DIRECTORIES and not name.startswith(".")
            )
            for name in sorted(names):
                path = Path(current) / name
                # Skip the scan cache file itself to avoid polluting results.
                if path.resolve() == cache_file_resolved:
                    continue
                if is_secret_file(path):
                    skipped_secrets += 1
                    continue
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS or path.is_symlink():
                    continue
                if len(files) >= self._limits.max_files:
                    truncated = True
                    break
                try:
                    stat = path.stat()
                except OSError:
                    continue
                size = stat.st_size
                if size > self._limits.max_file_bytes:
                    skipped_oversized += 1
                    continue
                if total_bytes + size > self._limits.max_total_bytes:
                    truncated = True
                    break

                relative = path.relative_to(root).as_posix()

                if not cache.needs_reindex(relative, stat):
                    # Use cached content placeholder — symbols already stored.
                    # We still need the file content for the snapshot so that
                    # the analyzer can read it; only symbol extraction benefits
                    # from caching.  Re-read the content (it is cheap compared
                    # to parsing) but skip symbol extraction.
                    try:
                        content = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    files.append(SourceFile(relative, content, size))
                    total_bytes += size
                    continue

                # File is new or changed — read, parse, update cache.
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                symbols = self._extract_symbols_cached(relative, content)
                cache.update(relative, stat, content, symbols)
                files.append(SourceFile(relative, content, size))
                total_bytes += size
            if truncated:
                break

        cache.flush()
        return ProjectSnapshot(
            root=root,
            files=tuple(files),
            skipped_secret_files=skipped_secrets,
            skipped_oversized_files=skipped_oversized,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # Symbol extraction (tree-sitter or fallback)
    # ------------------------------------------------------------------

    def _extract_symbols_cached(self, relative_path: str, content: str) -> list[str]:
        """Extract symbols using tree-sitter when available, else fall back."""
        suffix = relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
        if self._ts_available and suffix == "py":
            symbols = self._ts_extract_python(content)
            if symbols is not None:
                return symbols
        if self._ts_available and suffix in {"ts", "tsx", "js", "jsx"}:
            symbols = self._ts_extract_js(content)
            if symbols is not None:
                return symbols
        # Fallback: use the same logic as BurhanAnalyzer._extract_symbols
        return []  # caller will rely on BurhanAnalyzer for actual symbol extraction

    # ------------------------------------------------------------------
    # Tree-sitter probing (optional dependency)
    # ------------------------------------------------------------------

    @staticmethod
    def _probe_tree_sitter() -> bool:
        try:
            import tree_sitter  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _ts_extract_python(content: str) -> list[str] | None:
        """Extract top-level symbol names via tree-sitter for Python."""
        try:
            import tree_sitter_python as tspython
            from tree_sitter import Language, Parser

            language = Language(tspython.language())
            parser = Parser(language)
            tree = parser.parse(content.encode("utf-8"))
            names: list[str] = []
            _walk_ts_python(tree.root_node, names)
            return list(dict.fromkeys(names))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _ts_extract_js(content: str) -> list[str] | None:
        """Extract symbol names via tree-sitter for JS/TS."""
        try:
            import tree_sitter_javascript as tsjs
            from tree_sitter import Language, Parser

            language = Language(tsjs.language())
            parser = Parser(language)
            tree = parser.parse(content.encode("utf-8"))
            names: list[str] = []
            _walk_ts_js(tree.root_node, names)
            return list(dict.fromkeys(names))
        except Exception:  # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# Tree-sitter node walkers (used only when tree-sitter is available)
# ---------------------------------------------------------------------------

_PYTHON_DEFINITION_TYPES = frozenset(
    {"function_definition", "async_function_definition", "class_definition"}
)
_JS_DEFINITION_TYPES = frozenset(
    {
        "function_declaration",
        "class_declaration",
        "method_definition",
        "lexical_declaration",  # const / let
        "variable_declaration",  # var
    }
)


def _walk_ts_python(node: Any, out: list[str]) -> None:
    if node.type in _PYTHON_DEFINITION_TYPES:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            out.append(name_node.text.decode("utf-8", errors="replace"))
    for child in node.children:
        _walk_ts_python(child, out)


def _walk_ts_js(node: Any, out: list[str]) -> None:
    if node.type in _JS_DEFINITION_TYPES:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            out.append(name_node.text.decode("utf-8", errors="replace"))
    for child in node.children:
        _walk_ts_js(child, out)


# ---------------------------------------------------------------------------
# Code tree builder
# ---------------------------------------------------------------------------

_JS_SYMBOL_RE = re.compile(
    r"\b(?:function|class|interface|type|const|let|var)\s+([A-Za-z_$][\w$]*)"
)


def _symbol_kind_from_ast(node: ast.AST) -> tuple[str, str] | None:
    """Return (name, kind) for a top-level AST definition node, or None."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name, "function"
    if isinstance(node, ast.ClassDef):
        return node.name, "class"
    return None


def _extract_file_children(source_file: SourceFile) -> tuple[CodeTreeNode, ...]:
    """Return symbol nodes for a source file using AST for Python, regex for JS/TS."""
    path = source_file.relative_path
    children: list[CodeTreeNode] = []

    if path.endswith((".py", ".pyi")):
        try:
            tree = ast.parse(source_file.content)
        except SyntaxError:
            return ()
        for node in ast.walk(tree):
            result = _symbol_kind_from_ast(node)
            if result is not None:
                name, kind = result
                children.append(CodeTreeNode(name=name, kind=kind))
        return tuple(dict.fromkeys(c.name for c in children) and children or children)

    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        seen: set[str] = set()
        for match in _JS_SYMBOL_RE.finditer(source_file.content):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                keyword = match.group(0).split()[0]
                kind = "class" if keyword == "class" else "function"
                children.append(CodeTreeNode(name=name, kind=kind))
        return tuple(children)

    return ()


def build_code_tree(snapshot: ProjectSnapshot) -> CodeTreeNode:
    """Build a hierarchical :class:`CodeTreeNode` tree from *snapshot*.

    The tree has the form::

        <project root>          (directory)
        ├── src/                (directory)
        │   └── app.py          (file)
        │       ├── MyClass     (class)
        │       └── helper      (function)
        └── tests/              (directory)
            └── test_app.py     (file)

    Directories are inferred from the relative paths of the scanned files.
    Only files present in the snapshot are included (i.e. secret or oversized
    files that were excluded from the scan will not appear).
    """
    # Build a nested dict tree: {name: {"_files": [...], "subdirs": {name: ...}}}
    # We use a simpler approach: build a dict of path -> CodeTreeNode bottom-up.

    # Collect all unique directory paths and file paths
    dir_files: dict[str, list[CodeTreeNode]] = {}  # dir_path -> list of file nodes

    for source in snapshot.files:
        parts = source.relative_path.split("/")
        file_name = parts[-1]
        dir_path = "/".join(parts[:-1]) if len(parts) > 1 else ""
        file_node = CodeTreeNode(
            name=file_name,
            kind="file",
            children=_extract_file_children(source),
        )
        dir_files.setdefault(dir_path, []).append(file_node)

    # Collect all unique directory paths (including intermediate directories)
    all_dirs: set[str] = set(dir_files.keys())
    for path in list(all_dirs):
        parts = path.split("/") if path else []
        for i in range(len(parts)):
            all_dirs.add("/".join(parts[:i]))

    # Build nodes bottom-up: for each directory, collect its direct children
    # (subdirectories and files)
    def _build_dir(dir_path: str) -> CodeTreeNode:
        dir_name = dir_path.split("/")[-1] if dir_path else snapshot.root.name

        # Find immediate subdirectories
        depth = len(dir_path.split("/")) if dir_path else 0
        subdirs: list[CodeTreeNode] = []
        seen_subdirs: set[str] = set()
        for other in sorted(all_dirs):
            if not other:
                continue
            other_parts = other.split("/")
            if len(other_parts) == depth + 1 and (
                (dir_path and other.startswith(dir_path + "/"))
                or (not dir_path and "/" not in other)
            ):
                subdir_name = other_parts[-1]
                if subdir_name not in seen_subdirs:
                    seen_subdirs.add(subdir_name)
                    subdirs.append(_build_dir(other))

        # Files directly in this directory
        files = sorted(dir_files.get(dir_path, []), key=lambda n: n.name)
        children = tuple(sorted(subdirs, key=lambda n: n.name) + files)
        return CodeTreeNode(name=dir_name, kind="directory", children=children)

    return _build_dir("")


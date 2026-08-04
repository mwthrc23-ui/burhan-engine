"""Project fingerprint utility for Burhan Engine.

Computes a stable SHA-256 fingerprint of all non-excluded source files
in a project directory.  Used by the repair loop to detect if the
project changes between analysis and proof phases.

Design rules
------------
* Read-only — never writes to the project directory.
* Symlink-safe — resolves symlinks and rejects those pointing outside
  the project root (path traversal guard).
* No network calls.
* Returns a deterministic fingerprint for the same directory contents.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


# Directories that are always excluded from fingerprinting.
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        "dist",
        "build",
        ".serena",
    }
)

# Maximum file size included in fingerprint (16 MB).
_MAX_FILE_BYTES = 16 * 1024 * 1024


class FingerprintError(OSError):
    """Raised when fingerprinting cannot complete safely."""


def fingerprint_project(root: Path, max_files: int = 10_000) -> str:
    """Return a SHA-256 fingerprint of the project under *root*.

    Parameters
    ----------
    root:
        Absolute path to the project root directory.
    max_files:
        Safety limit on the number of files processed.  Raises
        ``FingerprintError`` if exceeded.

    Returns
    -------
    str
        64-character lowercase hex digest.

    Raises
    ------
    FingerprintError
        If *root* does not exist, is not a directory, a symlink escapes
        the root, or more than *max_files* files are found.
    """
    root = root.resolve()
    if not root.exists():
        raise FingerprintError(f"project root does not exist: {root}")
    if not root.is_dir():
        raise FingerprintError(f"project root is not a directory: {root}")

    h = hashlib.sha256()
    file_count = 0

    for rel_path in _sorted_source_paths(root):
        abs_path = root / rel_path

        # Symlink guard
        try:
            resolved = abs_path.resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root)
        except ValueError:
            raise FingerprintError(
                f"symlink escapes project root: {rel_path} → {resolved}"
            )

        file_count += 1
        if file_count > max_files:
            raise FingerprintError(
                f"project contains more than {max_files} files; fingerprinting aborted"
            )

        try:
            stat = abs_path.stat()
            if stat.st_size > _MAX_FILE_BYTES:
                h.update(f"LARGE:{rel_path}:{stat.st_size}\n".encode())
                continue
            content = abs_path.read_bytes()
        except OSError:
            continue

        # Include relative path and content in the digest (path is UTF-8 encoded)
        h.update(rel_path.encode("utf-8", errors="replace"))
        h.update(b"\x00")
        h.update(content)
        h.update(b"\x00")

    return h.hexdigest()


def fingerprint_changed(
    root: Path,
    baseline: str,
    max_files: int = 10_000,
) -> bool:
    """Return True if the project fingerprint has changed from *baseline*."""
    current = fingerprint_project(root, max_files=max_files)
    return current != baseline


def _sorted_source_paths(root: Path) -> list[str]:
    """Return sorted relative paths of all non-excluded files under *root*.

    Symlinked directories are followed (``followlinks=True``) so that
    path-traversal via symlinked dirs is caught by the caller's symlink guard.
    """
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        # Prune excluded directories in-place
        dirnames[:] = sorted(
            d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")
        )
        dir_rel = Path(dirpath).relative_to(root)
        for filename in sorted(filenames):
            rel = str(dir_rel / filename) if str(dir_rel) != "." else filename
            paths.append(rel)
    return paths


def fingerprint_stats(root: Path) -> dict[str, Any]:
    """Return statistics about the files included in the fingerprint."""
    root = root.resolve()
    paths = _sorted_source_paths(root)
    total_size = 0
    for rel in paths:
        try:
            total_size += (root / rel).stat().st_size
        except OSError:
            pass
    return {
        "file_count": len(paths),
        "total_bytes": total_size,
        "root": str(root),
    }

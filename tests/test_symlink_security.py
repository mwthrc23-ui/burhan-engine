"""Security tests: symlink, path traversal, overwrite prevention (Phase security)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from burhan.verification.project_fingerprint import (
    FingerprintError,
    fingerprint_project,
)


class TestSymlinkSecurity:
    def test_symlink_within_root_is_safe(self, tmp_path: Path) -> None:
        real = tmp_path / "real.py"
        real.write_text("x = 1\n")
        link = tmp_path / "link.py"
        link.symlink_to(real)
        # Must not raise
        fp = fingerprint_project(tmp_path)
        assert len(fp) == 64

    def test_symlink_pointing_outside_raises(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "_outside_secret.py"
        outside.write_text("secret = 'hunter2'\n")
        link = tmp_path / "escape.py"
        link.symlink_to(outside)
        with pytest.raises(FingerprintError, match="symlink escapes"):
            fingerprint_project(tmp_path)
        outside.unlink(missing_ok=True)

    def test_deeply_nested_symlink_escape_raises(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        outside = tmp_path.parent / "_outside_nested.py"
        outside.write_text("secret = True\n")
        link = sub / "escape.py"
        link.symlink_to(outside)
        with pytest.raises(FingerprintError, match="symlink escapes"):
            fingerprint_project(tmp_path)
        outside.unlink(missing_ok=True)

    def test_symlink_to_dir_outside_raises(self, tmp_path: Path) -> None:
        outside_dir = tmp_path.parent / "_outside_dir"
        outside_dir.mkdir(exist_ok=True)
        (outside_dir / "secret.py").write_text("secret = 1\n")
        link = tmp_path / "escaped_dir"
        link.symlink_to(outside_dir)
        # fingerprint_project walks files; a symlinked dir pointing outside
        # should cause the escaped file to fail the relative_to check
        with pytest.raises(FingerprintError, match="symlink escapes"):
            fingerprint_project(tmp_path)
        import shutil
        shutil.rmtree(str(outside_dir), ignore_errors=True)


class TestPathTraversal:
    def test_nonexistent_path_raises(self) -> None:
        with pytest.raises(FingerprintError, match="does not exist"):
            fingerprint_project(Path("/nonexistent/burhan_test_12345"))

    def test_file_as_root_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        with pytest.raises(FingerprintError, match="not a directory"):
            fingerprint_project(f)

    def test_max_files_limit_prevents_dos(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"f{i}.py").write_text(f"x={i}\n")
        with pytest.raises(FingerprintError, match="more than 5 files"):
            fingerprint_project(tmp_path, max_files=5)

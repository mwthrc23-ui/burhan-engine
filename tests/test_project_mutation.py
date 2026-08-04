"""Tests for project mutation detection between analysis and proof (Phase security)."""
from __future__ import annotations

from pathlib import Path
import pytest

from burhan.verification.project_fingerprint import (
    fingerprint_project,
    fingerprint_changed,
)


class TestProjectMutation:
    def test_no_mutation_fingerprint_stable(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        assert not fingerprint_changed(tmp_path, baseline)

    def test_file_addition_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        (tmp_path / "new.py").write_text("y = 2\n")
        assert fingerprint_changed(tmp_path, baseline)

    def test_file_deletion_detected(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        baseline = fingerprint_project(tmp_path)
        (tmp_path / "b.py").unlink()
        assert fingerprint_changed(tmp_path, baseline)

    def test_file_content_change_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "app.py"
        f.write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        f.write_text("x = 99\n")
        assert fingerprint_changed(tmp_path, baseline)

    def test_file_rename_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "old.py"
        f.write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        f.rename(tmp_path / "new.py")
        assert fingerprint_changed(tmp_path, baseline)

    def test_git_change_ignored(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: main\n")
        (tmp_path / "app.py").write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        (tmp_path / ".git" / "HEAD").write_text("ref: other\n")
        assert not fingerprint_changed(tmp_path, baseline)

    def test_pycache_change_ignored(self, tmp_path: Path) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-311.pyc").write_bytes(b"\x00")
        (tmp_path / "app.py").write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        (cache / "mod.cpython-311.pyc").write_bytes(b"\xff")
        assert not fingerprint_changed(tmp_path, baseline)

"""Tests for advanced verification loop features (Phase 4)."""
from __future__ import annotations

from pathlib import Path

import pytest

from burhan.verification.project_fingerprint import (
    FingerprintError,
    fingerprint_project,
    fingerprint_changed,
    fingerprint_stats,
)


class TestProjectFingerprint:
    def test_fingerprint_non_empty(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        fp = fingerprint_project(tmp_path)
        assert len(fp) == 64
        assert fp.isalnum()

    def test_fingerprint_deterministic(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        fp1 = fingerprint_project(tmp_path)
        fp2 = fingerprint_project(tmp_path)
        assert fp1 == fp2

    def test_fingerprint_changes_on_file_edit(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        fp1 = fingerprint_project(tmp_path)
        f.write_text("x = 2\n")
        fp2 = fingerprint_project(tmp_path)
        assert fp1 != fp2

    def test_fingerprint_changes_on_new_file(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        fp1 = fingerprint_project(tmp_path)
        (tmp_path / "b.py").write_text("y = 2\n")
        fp2 = fingerprint_project(tmp_path)
        assert fp1 != fp2

    def test_empty_directory_fingerprints(self, tmp_path: Path) -> None:
        fp = fingerprint_project(tmp_path)
        assert len(fp) == 64

    def test_git_dir_excluded(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (tmp_path / "a.py").write_text("x = 1\n")
        fp1 = fingerprint_project(tmp_path)
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/other\n")
        fp2 = fingerprint_project(tmp_path)
        assert fp1 == fp2  # .git changes are ignored

    def test_missing_root_raises(self) -> None:
        with pytest.raises(FingerprintError, match="does not exist"):
            fingerprint_project(Path("/nonexistent/path/12345"))

    def test_file_as_root_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "file.py"
        f.write_text("x = 1\n")
        with pytest.raises(FingerprintError, match="not a directory"):
            fingerprint_project(f)

    def test_symlink_within_root_allowed(self, tmp_path: Path) -> None:
        target = tmp_path / "real.py"
        target.write_text("x = 1\n")
        link = tmp_path / "link.py"
        link.symlink_to(target)
        fp = fingerprint_project(tmp_path)
        assert len(fp) == 64

    def test_symlink_escaping_root_raises(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.py"
        outside.write_text("secret\n")
        link = tmp_path / "link.py"
        link.symlink_to(outside)
        with pytest.raises(FingerprintError, match="symlink escapes"):
            fingerprint_project(tmp_path)

    def test_fingerprint_changed_true_after_edit(self, tmp_path: Path) -> None:
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        f.write_text("x = 99\n")
        assert fingerprint_changed(tmp_path, baseline)

    def test_fingerprint_changed_false_when_same(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        assert not fingerprint_changed(tmp_path, baseline)

    def test_stats_returns_correct_file_count(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        stats = fingerprint_stats(tmp_path)
        assert stats["file_count"] == 2
        assert stats["total_bytes"] > 0

    def test_max_files_limit(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text(f"x = {i}\n")
        with pytest.raises(FingerprintError, match="more than 3 files"):
            fingerprint_project(tmp_path, max_files=3)

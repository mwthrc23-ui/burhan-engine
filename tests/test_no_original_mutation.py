"""Tests: original project must not be mutated in preview/proof modes (Phase security)."""
from __future__ import annotations

from pathlib import Path

from burhan.verification.project_fingerprint import (
    fingerprint_project,
    fingerprint_changed,
)


class TestNoOriginalMutation:
    def test_fingerprint_read_does_not_modify_files(self, tmp_path: Path) -> None:
        f = tmp_path / "app.py"
        f.write_text("x = 1\n")
        mtime_before = f.stat().st_mtime
        fingerprint_project(tmp_path)
        mtime_after = f.stat().st_mtime
        assert mtime_before == mtime_after

    def test_fingerprint_changed_read_does_not_modify_files(self, tmp_path: Path) -> None:
        f = tmp_path / "app.py"
        f.write_text("x = 1\n")
        baseline = fingerprint_project(tmp_path)
        mtime_before = f.stat().st_mtime
        fingerprint_changed(tmp_path, baseline)
        mtime_after = f.stat().st_mtime
        assert mtime_before == mtime_after

    def test_multiple_fingerprint_calls_do_not_create_files(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        files_before = set(tmp_path.rglob("*"))
        for _ in range(5):
            fingerprint_project(tmp_path)
        files_after = set(tmp_path.rglob("*"))
        assert files_before == files_after

    def test_incremental_index_does_not_write_to_project(self, tmp_path: Path) -> None:
        from burhan.index.python_indexer import PythonIndexer
        from burhan.index.incremental_index import IncrementalIndex

        f = tmp_path / "mod.py"
        f.write_text("def foo(): pass\n")
        files_before = set(tmp_path.rglob("*"))

        idx = IncrementalIndex(PythonIndexer())
        idx.index("mod.py", f.read_text())

        files_after = set(tmp_path.rglob("*"))
        assert files_before == files_after

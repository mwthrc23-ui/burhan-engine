from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from burhan.external_patch import ExternalPatchError, apply_external_patch


def _patch(path: str, old: str = "x = 1", new: str = "x = 2") -> str:
    return (
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def test_preview_returns_immutable_artifact_without_writing(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_bytes(b"x = 1\n")

    result = apply_external_patch(tmp_path, _patch("app.py"))

    assert source.read_text(encoding="utf-8") == "x = 1\n"
    assert result.diff == _patch("app.py")
    assert result.changed_files == ("app.py",)
    assert result.updated_files[0].relative_path == "app.py"
    assert result.updated_files[0].content == "x = 2\n"
    assert result.artifact_hash.startswith("sha256:")
    assert result.verification_checks == (
        "bounded_unified_diff",
        "existing_utf8_text_files",
        "paths_within_project",
        "exact_context_match",
        "atomic_write_on_apply",
    )
    assert result.applied is False
    with pytest.raises(AttributeError):
        result.applied = True  # type: ignore[misc]


def test_apply_updates_all_files_and_supports_multiple_hunks(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    second.write_text("alpha\nbeta\n", encoding="utf-8")
    diff = (
        "diff --git a/first.py b/first.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/first.py\n"
        "+++ b/first.py\n"
        "@@ -1,2 +1,2 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        "@@ -4 +4 @@\n"
        "-four\n"
        "+FOUR\n"
        "diff --git a/second.py b/second.py\n"
        "--- a/second.py\n"
        "+++ b/second.py\n"
        "@@ -1,2 +1,2 @@\n"
        " alpha\n"
        "-beta\n"
        "+BETA\n"
    )

    result = apply_external_patch(tmp_path, diff, apply=True)

    assert first.read_text(encoding="utf-8") == "one\nTWO\nthree\nFOUR\n"
    assert second.read_text(encoding="utf-8") == "alpha\nBETA\n"
    assert result.changed_files == ("first.py", "second.py")
    assert result.applied is True


def test_preserves_crlf_and_can_remove_final_newline(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_bytes(b"one\r\ntwo\r\n")
    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " one\n"
        "-two\n"
        "+TWO\n"
        "\\ No newline at end of file\n"
    )

    result = apply_external_patch(tmp_path, diff, apply=True)

    assert source.read_bytes() == b"one\r\nTWO"
    assert result.updated_files[0].content == "one\r\nTWO"


def test_supports_line_insertion_and_deletion_in_an_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_bytes(b"one\ntwo\nthree\n")
    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,0 +2 @@\n"
        "+inserted\n"
        "@@ -3 +3,0 @@\n"
        "-three\n"
    )

    result = apply_external_patch(tmp_path, diff, apply=True)

    assert source.read_bytes() == b"one\ninserted\ntwo\n"
    assert result.updated_files[0].content == "one\ninserted\ntwo\n"


@pytest.mark.parametrize(
    "old_path,new_path",
    [
        ("/tmp/app.py", "/tmp/app.py"),
        ("C:/tmp/app.py", "C:/tmp/app.py"),
        ("../app.py", "../app.py"),
        ("src/../../app.py", "src/../../app.py"),
        (r"src\..\app.py", r"src\..\app.py"),
        ("app.py:stream", "app.py:stream"),
        ("app.py. ", "app.py. "),
        ("app.py", "other.py"),
        ("/dev/null", "app.py"),
        ("app.py", "/dev/null"),
    ],
)
def test_rejects_unsafe_or_mismatched_file_headers(
    tmp_path: Path, old_path: str, new_path: str
) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    diff = f"--- {old_path}\n+++ {new_path}\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"

    with pytest.raises(ExternalPatchError):
        apply_external_patch(tmp_path, diff)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        "secrets.yaml",
        "private.pem",
        ".git/config",
        "node_modules/package.js",
        "dist/app.py",
        "ECC/plugin.py",
    ],
)
def test_rejects_secret_and_excluded_paths(tmp_path: Path, path: str) -> None:
    target = tmp_path.joinpath(*path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(ExternalPatchError):
        apply_external_patch(tmp_path, _patch(path))


@pytest.mark.parametrize(
    "metadata",
    [
        "new file mode 100644",
        "deleted file mode 100644",
        "old mode 100644\nnew mode 100755",
        "rename from app.py\nrename to renamed.py",
        "copy from app.py\ncopy to copied.py",
        "Binary files a/app.py and b/app.py differ",
        "GIT binary patch",
    ],
)
def test_rejects_non_content_patch_operations(tmp_path: Path, metadata: str) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    diff = f"diff --git a/app.py b/app.py\n{metadata}\n{_patch('app.py')}"

    with pytest.raises(ExternalPatchError):
        apply_external_patch(tmp_path, diff)


def test_rejects_missing_non_utf8_and_binary_targets(tmp_path: Path) -> None:
    with pytest.raises(ExternalPatchError, match="existing"):
        apply_external_patch(tmp_path, _patch("missing.py"))

    target = tmp_path / "app.py"
    target.write_bytes(b"\xff\xfe")
    with pytest.raises(ExternalPatchError, match="UTF-8"):
        apply_external_patch(tmp_path, _patch("app.py"))

    target.write_bytes(b"x = 1\x00\n")
    with pytest.raises(ExternalPatchError, match="text"):
        apply_external_patch(tmp_path, _patch("app.py"))


def test_rejects_invalid_utf8_oversized_or_too_many_file_patch(tmp_path: Path) -> None:
    with pytest.raises(ExternalPatchError, match="UTF-8"):
        apply_external_patch(tmp_path, b"\xff")

    with pytest.raises(ExternalPatchError, match="1 MiB"):
        apply_external_patch(tmp_path, "x" * (1024 * 1024 + 1))

    pieces = []
    for index in range(17):
        path = f"file_{index}.py"
        (tmp_path / path).write_text("x = 1\n", encoding="utf-8")
        pieces.append(_patch(path))
    with pytest.raises(ExternalPatchError, match="16 files"):
        apply_external_patch(tmp_path, "".join(pieces))


def test_rejects_context_mismatch_without_partial_write(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("x = 1\n", encoding="utf-8")
    second.write_text("unexpected\n", encoding="utf-8")
    diff = _patch("first.py") + _patch("second.py", "expected", "changed")

    with pytest.raises(ExternalPatchError, match="context"):
        apply_external_patch(tmp_path, diff, apply=True)

    assert first.read_text(encoding="utf-8") == "x = 1\n"
    assert second.read_text(encoding="utf-8") == "unexpected\n"


def test_rejects_two_paths_that_alias_the_same_file(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    alias = tmp_path / "alias.py"
    source.write_bytes(b"x = 1\n")
    os.link(source, alias)

    with pytest.raises(ExternalPatchError, match="hardlinked|same target"):
        apply_external_patch(tmp_path, _patch("app.py") + _patch("alias.py"))


def test_rejects_a_single_hardlink_to_data_outside_the_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    os.link(outside, project / "app.py")

    with pytest.raises(ExternalPatchError, match="hardlinked"):
        apply_external_patch(project, _patch("app.py"), apply=True)

    assert outside.read_text(encoding="utf-8") == "x = 1\n"
    assert (project / "app.py").read_text(encoding="utf-8") == "x = 1\n"


@pytest.mark.parametrize(
    "content,diff",
    [
        (
            b"",
            "--- a/app.py\n+++ b/app.py\n@@ -0,0 +1,2 @@\n"
            "+one\n\\ No newline at end of file\n+two\n",
        ),
        (
            b"one\ntwo\n",
            "--- a/app.py\n+++ b/app.py\n@@ -1,2 +0,0 @@\n"
            "-one\n\\ No newline at end of file\n-two\n",
        ),
    ],
)
def test_rejects_nonfinal_no_newline_marker(
    tmp_path: Path, content: bytes, diff: str
) -> None:
    (tmp_path / "app.py").write_bytes(content)

    with pytest.raises(ExternalPatchError, match="no-newline marker"):
        apply_external_patch(tmp_path, diff)


def test_rejects_no_newline_marker_when_hunk_is_not_at_end_of_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_bytes(b"one\ntwo\nthree\n")
    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-one\n"
        "+ONE\n"
        "\\ No newline at end of file\n"
    )

    with pytest.raises(ExternalPatchError, match="no-newline marker"):
        apply_external_patch(tmp_path, diff)


def test_rejects_symlink_and_reparse_targets(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text("x = 1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(ExternalPatchError, match="link or reparse"):
        apply_external_patch(tmp_path, _patch("link.py"))

    with patch("burhan.external_patch.is_reparse_path", return_value=True):
        with pytest.raises(ExternalPatchError, match="link or reparse"):
            apply_external_patch(tmp_path, _patch("real.py"))


def test_revalidates_parent_components_before_atomic_apply(tmp_path: Path) -> None:
    parent = tmp_path / "src"
    parent.mkdir()
    source = parent / "app.py"
    source.write_bytes(b"x = 1\n")
    parent_checks = 0

    def becomes_reparse(path: Path) -> bool:
        nonlocal parent_checks
        if path == parent:
            parent_checks += 1
            return parent_checks > 1
        return False

    with patch("burhan.external_patch.is_reparse_path", side_effect=becomes_reparse):
        with pytest.raises(ExternalPatchError, match="link or reparse"):
            apply_external_patch(tmp_path, _patch("src/app.py"), apply=True)

    assert source.read_bytes() == b"x = 1\n"


def test_rolls_back_if_atomic_replacement_fails(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("x = 1\n", encoding="utf-8")
    second.write_text("x = 1\n", encoding="utf-8")
    real_replace = os.replace
    replacement_count = 0

    def fail_second_replacement(source: str | Path, destination: str | Path) -> None:
        nonlocal replacement_count
        if Path(destination) in {first, second} and ".burhan-patch-" in Path(source).name:
            replacement_count += 1
            if replacement_count == 2:
                raise OSError("simulated replacement failure")
        real_replace(source, destination)

    with patch("burhan.external_patch.os.replace", side_effect=fail_second_replacement):
        with pytest.raises(ExternalPatchError, match="atomically"):
            apply_external_patch(
                tmp_path,
                _patch("first.py") + _patch("second.py"),
                apply=True,
            )

    assert first.read_text(encoding="utf-8") == "x = 1\n"
    assert second.read_text(encoding="utf-8") == "x = 1\n"
    assert not list(tmp_path.glob(".burhan-patch-*"))
    assert not list(tmp_path.glob(".burhan-backup-*"))


def test_rolls_back_if_late_safety_revalidation_fails(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_bytes(b"x = 1\n")
    second.write_bytes(b"x = 1\n")
    reparse_checks = 0

    def second_target_changes_late(_path: Path) -> bool:
        nonlocal reparse_checks
        reparse_checks += 1
        return reparse_checks == 8

    with patch(
        "burhan.external_patch.is_reparse_path",
        side_effect=second_target_changes_late,
    ):
        with pytest.raises(ExternalPatchError, match="atomically"):
            apply_external_patch(
                tmp_path,
                _patch("first.py") + _patch("second.py"),
                apply=True,
            )

    assert first.read_bytes() == b"x = 1\n"
    assert second.read_bytes() == b"x = 1\n"
    assert not list(tmp_path.glob(".burhan-patch-*"))
    assert not list(tmp_path.glob(".burhan-backup-*"))

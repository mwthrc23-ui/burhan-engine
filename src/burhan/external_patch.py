"""Bounded, fail-closed application of external unified diffs.

This module intentionally implements only ordinary text modifications.  It is
not a general replacement for ``git apply`` and rejects every patch operation
that would create, delete, rename, copy, or change metadata for a file.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from .scanner import is_excluded_directory, is_reparse_path, is_secret_file


_MAX_PATCH_BYTES = 1024 * 1024
_MAX_FILES = 16
_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)
_FORBIDDEN_METADATA = (
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "Binary files ",
    "GIT binary patch",
)
_VERIFICATION_CHECKS = (
    "bounded_unified_diff",
    "existing_utf8_text_files",
    "paths_within_project",
    "exact_context_match",
    "atomic_write_on_apply",
)


class ExternalPatchError(ValueError):
    """An external patch is malformed, unsafe, or does not match the project."""


@dataclass(frozen=True, slots=True)
class UpdatedFile:
    """The complete post-patch content for one project-relative file."""

    relative_path: str
    content: str


@dataclass(frozen=True, slots=True)
class ExternalPatchResult:
    """A validated patch artifact and its computed file updates."""

    diff: str
    changed_files: tuple[str, ...]
    updated_files: tuple[UpdatedFile, ...]
    artifact_hash: str
    verification_checks: tuple[str, ...]
    applied: bool


@dataclass(frozen=True, slots=True)
class _PatchLine:
    kind: str
    content: str
    old_has_newline: bool = True
    new_has_newline: bool = True


@dataclass(frozen=True, slots=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[_PatchLine, ...]


@dataclass(frozen=True, slots=True)
class _FilePatch:
    relative_path: str
    hunks: tuple[_Hunk, ...]


@dataclass(frozen=True, slots=True)
class _SourceLine:
    content: str
    ending: str


@dataclass(frozen=True, slots=True)
class _TargetState:
    root: Path
    relative_path: str
    path: Path
    original: bytes
    updated: str
    identity: tuple[int, int, int, int, int]


def apply_external_patch(
    project: Path, patch: str | bytes, *, apply: bool = False
) -> ExternalPatchResult:
    """Validate and preview or atomically apply a bounded unified text diff.

    ``apply=False`` is side-effect free.  With ``apply=True``, every target is
    validated and every replacement is prepared before any project file is
    replaced; a failed multi-file replacement restores already-replaced files.
    """

    patch_text, patch_bytes = _decode_patch(patch)
    file_patches = _parse_patch(patch_text)
    root = project.expanduser().resolve()
    if not root.is_dir():
        raise ExternalPatchError("project must be an existing directory")

    states = tuple(_build_target_state(root, item) for item in file_patches)
    _reject_duplicate_targets(states)
    if apply:
        _apply_atomically(states)

    return ExternalPatchResult(
        diff=patch_text,
        changed_files=tuple(state.relative_path for state in states),
        updated_files=tuple(
            UpdatedFile(relative_path=state.relative_path, content=state.updated)
            for state in states
        ),
        artifact_hash=f"sha256:{hashlib.sha256(patch_bytes).hexdigest()}",
        verification_checks=_VERIFICATION_CHECKS,
        applied=apply,
    )


def _decode_patch(patch: str | bytes) -> tuple[str, bytes]:
    if isinstance(patch, bytes):
        patch_bytes = patch
        try:
            patch_text = patch.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ExternalPatchError("patch must be valid UTF-8") from error
    elif isinstance(patch, str):
        patch_text = patch
        try:
            patch_bytes = patch.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise ExternalPatchError("patch must be valid UTF-8") from error
    else:
        raise ExternalPatchError("patch must be str or bytes")
    if len(patch_bytes) > _MAX_PATCH_BYTES:
        raise ExternalPatchError("patch exceeds the 1 MiB limit")
    if not patch_bytes:
        raise ExternalPatchError("patch must not be empty")
    if b"\0" in patch_bytes:
        raise ExternalPatchError("binary patches are not supported")
    return patch_text, patch_bytes


def _parse_patch(patch_text: str) -> tuple[_FilePatch, ...]:
    normalized = patch_text.replace("\r\n", "\n")
    if "\r" in normalized:
        raise ExternalPatchError("patch must use LF or CRLF line endings")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    parsed: list[_FilePatch] = []
    seen_paths: set[str] = set()
    index = 0
    while index < len(lines):
        git_path: str | None = None
        line = lines[index]
        _reject_forbidden_metadata(line)
        if line.startswith("diff --git "):
            fields = line.split(" ")
            if len(fields) != 4 or not fields[2] or not fields[3]:
                raise ExternalPatchError("unsupported diff --git header")
            old_git = _normalize_path(fields[2], expected_prefix="a/")
            new_git = _normalize_path(fields[3], expected_prefix="b/")
            if old_git != new_git:
                raise ExternalPatchError("mismatched old and new paths")
            git_path = old_git
            index += 1
            while index < len(lines) and not lines[index].startswith("--- "):
                metadata = lines[index]
                _reject_forbidden_metadata(metadata)
                if not metadata.startswith("index "):
                    raise ExternalPatchError("unsupported patch metadata")
                index += 1

        if index >= len(lines) or not lines[index].startswith("--- "):
            raise ExternalPatchError("missing old-file header")
        old_path = _header_path(lines[index][4:], expected_prefix="a/")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ExternalPatchError("missing new-file header")
        new_path = _header_path(lines[index][4:], expected_prefix="b/")
        index += 1
        if old_path != new_path or (git_path is not None and old_path != git_path):
            raise ExternalPatchError("mismatched old and new paths")
        if old_path in seen_paths:
            raise ExternalPatchError("a file may appear only once in a patch")
        seen_paths.add(old_path)
        if len(seen_paths) > _MAX_FILES:
            raise ExternalPatchError("patch may modify at most 16 files")

        hunks: list[_Hunk] = []
        while index < len(lines) and lines[index].startswith("@@ "):
            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)
        if not hunks:
            raise ExternalPatchError("each file patch must contain a hunk")
        parsed.append(_FilePatch(relative_path=old_path, hunks=tuple(hunks)))

        if index < len(lines) and not (
            lines[index].startswith("diff --git ") or lines[index].startswith("--- ")
        ):
            _reject_forbidden_metadata(lines[index])
            raise ExternalPatchError("unexpected content outside a hunk")

    if not parsed:
        raise ExternalPatchError("patch must modify at least one file")
    return tuple(parsed)


def _parse_hunk(lines: list[str], index: int) -> tuple[_Hunk, int]:
    match = _HUNK_HEADER.fullmatch(lines[index])
    if match is None:
        raise ExternalPatchError("invalid unified-diff hunk header")
    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    if (old_start == 0 and old_count != 0) or (new_start == 0 and new_count != 0):
        raise ExternalPatchError("invalid zero-length hunk range")

    index += 1
    old_seen = 0
    new_seen = 0
    patch_lines: list[_PatchLine] = []
    changed = False
    while old_seen < old_count or new_seen < new_count:
        if index >= len(lines):
            raise ExternalPatchError("truncated unified-diff hunk")
        raw = lines[index]
        if not raw or raw[0] not in {" ", "+", "-"}:
            raise ExternalPatchError("invalid unified-diff hunk line")
        kind = raw[0]
        old_seen += kind in {" ", "-"}
        new_seen += kind in {" ", "+"}
        if old_seen > old_count or new_seen > new_count:
            raise ExternalPatchError("hunk line counts do not match its header")
        changed = changed or kind in {"+", "-"}
        patch_lines.append(_PatchLine(kind=kind, content=raw[1:]))
        index += 1
        if index < len(lines) and lines[index] == r"\ No newline at end of file":
            current = patch_lines[-1]
            if kind == "-":
                if old_seen != old_count:
                    raise ExternalPatchError("no-newline marker must end the old file")
                current = replace(current, old_has_newline=False)
            elif kind == "+":
                if new_seen != new_count:
                    raise ExternalPatchError("no-newline marker must end the new file")
                current = replace(current, new_has_newline=False)
            else:
                if old_seen != old_count or new_seen != new_count:
                    raise ExternalPatchError("no-newline marker must end both files")
                current = replace(
                    current,
                    old_has_newline=False,
                    new_has_newline=False,
                )
            patch_lines[-1] = current
            index += 1

    if index < len(lines) and lines[index] == r"\ No newline at end of file":
        raise ExternalPatchError("orphaned no-newline marker")
    if not changed:
        raise ExternalPatchError("a hunk must contain a content change")
    return (
        _Hunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            lines=tuple(patch_lines),
        ),
        index,
    )


def _header_path(value: str, *, expected_prefix: str) -> str:
    path = value.split("\t", 1)[0]
    if path == "/dev/null":
        raise ExternalPatchError("file creation and deletion patches are not supported")
    return _normalize_path(path, expected_prefix=expected_prefix)


def _normalize_path(value: str, *, expected_prefix: str) -> str:
    if value.startswith(expected_prefix):
        value = value[len(expected_prefix) :]
    if not value or "\\" in value or "\0" in value:
        raise ExternalPatchError("unsafe patch path")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ExternalPatchError("absolute patch paths are not allowed")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ExternalPatchError("patch path traversal is not allowed")
    parts = pure.parts
    if any(
        ":" in part
        or part.endswith((" ", "."))
        or any(ord(character) < 32 for character in part)
        for part in parts
    ):
        raise ExternalPatchError("unsafe platform-specific patch path")
    if any(
        is_excluded_directory(part.lower()) or part.lower() == "ecc"
        for part in parts[:-1]
    ):
        raise ExternalPatchError("patch targets an excluded path")
    platform_path = Path(*parts)
    if is_secret_file(platform_path):
        raise ExternalPatchError("patch targets a secret file")
    return pure.as_posix()


def _reject_forbidden_metadata(line: str) -> None:
    if line.startswith(_FORBIDDEN_METADATA):
        raise ExternalPatchError("non-content patch operations are not supported")


def _build_target_state(root: Path, file_patch: _FilePatch) -> _TargetState:
    parts = PurePosixPath(file_patch.relative_path).parts
    target = root.joinpath(*parts)
    _ensure_safe_components(root, parts)
    if not target.is_file():
        raise ExternalPatchError("patch targets existing regular files only")
    try:
        if not target.resolve(strict=True).is_relative_to(root):
            raise ExternalPatchError("patch target escapes the project")
        original = target.read_bytes()
        target_stat = target.stat()
    except OSError as error:
        raise ExternalPatchError("patch target could not be read safely") from error
    if b"\0" in original:
        raise ExternalPatchError("patch target must be a text file")
    if target_stat.st_nlink > 1:
        raise ExternalPatchError("patch target must not be a hardlinked file")
    try:
        original_text = original.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ExternalPatchError("patch target must be valid UTF-8") from error
    updated = _apply_hunks(original_text, file_patch.hunks, file_patch.relative_path)
    return _TargetState(
        root=root,
        relative_path=file_patch.relative_path,
        path=target,
        original=original,
        updated=updated,
        identity=_stat_identity(target_stat),
    )


def _ensure_safe_components(root: Path, parts: tuple[str, ...]) -> None:
    current = root
    try:
        for position, part in enumerate(parts):
            current = current / part
            if current.is_symlink() or is_reparse_path(current):
                raise ExternalPatchError("patch target contains a link or reparse point")
            if position < len(parts) - 1 and not current.is_dir():
                raise ExternalPatchError("patch target parent must be an existing directory")
        if not current.resolve(strict=True).is_relative_to(root):
            raise ExternalPatchError("patch target escapes the project")
    except FileNotFoundError as error:
        raise ExternalPatchError("patch targets existing files only") from error
    except OSError as error:
        raise ExternalPatchError("patch target could not be inspected safely") from error


def _reject_duplicate_targets(states: tuple[_TargetState, ...]) -> None:
    path_keys: set[str] = set()
    file_keys: set[tuple[int, int]] = set()
    for state in states:
        path_key = os.path.normcase(str(state.path.resolve(strict=True)))
        file_key = state.identity[:2]
        if path_key in path_keys or (file_key[1] and file_key in file_keys):
            raise ExternalPatchError("multiple patch paths resolve to the same target")
        path_keys.add(path_key)
        file_keys.add(file_key)


def _apply_hunks(text: str, hunks: tuple[_Hunk, ...], relative_path: str) -> str:
    source = _split_source(text)
    newline = next((line.ending for line in source if line.ending), "\n")
    output: list[_SourceLine] = []
    cursor = 0
    for hunk in hunks:
        old_index = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
        new_index = hunk.new_start if hunk.new_count == 0 else hunk.new_start - 1
        if old_index < cursor or old_index > len(source) or new_index != len(output) + old_index - cursor:
            raise ExternalPatchError(f"hunk positions do not match context in {relative_path}")
        output.extend(source[cursor:old_index])
        position = old_index
        for patch_line in hunk.lines:
            if patch_line.kind in {" ", "-"}:
                if position >= len(source):
                    raise ExternalPatchError(f"patch context mismatch in {relative_path}")
                existing = source[position]
                if (
                    existing.content != patch_line.content
                    or bool(existing.ending) != patch_line.old_has_newline
                ):
                    raise ExternalPatchError(f"patch context mismatch in {relative_path}")
                position += 1
                if patch_line.kind == " ":
                    output.append(existing)
            else:
                output.append(
                    _SourceLine(
                        content=patch_line.content,
                        ending=newline if patch_line.new_has_newline else "",
                    )
                )
        cursor = position
    output.extend(source[cursor:])
    if any(not line.ending for line in output[:-1]):
        raise ExternalPatchError("no-newline marker must describe the end of a file")
    return "".join(line.content + line.ending for line in output)


def _split_source(text: str) -> tuple[_SourceLine, ...]:
    if not text:
        return ()
    pieces = text.split("\n")
    result: list[_SourceLine] = []
    for piece in pieces[:-1]:
        if piece.endswith("\r"):
            result.append(_SourceLine(content=piece[:-1], ending="\r\n"))
        else:
            result.append(_SourceLine(content=piece, ending="\n"))
    if pieces[-1]:
        result.append(_SourceLine(content=pieces[-1], ending=""))
    return tuple(result)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_mode)


def _apply_atomically(states: tuple[_TargetState, ...]) -> None:
    prepared: list[tuple[_TargetState, Path, Path]] = []
    try:
        for state in states:
            _ensure_unchanged(state)
            replacement = _write_temporary(
                state.path.parent,
                ".burhan-patch-",
                state.updated.encode("utf-8"),
                stat.S_IMODE(state.identity[-1]),
            )
            try:
                backup = _write_temporary(
                    state.path.parent,
                    ".burhan-backup-",
                    state.original,
                    stat.S_IMODE(state.identity[-1]),
                )
            except Exception:
                replacement.unlink(missing_ok=True)
                raise
            prepared.append((state, replacement, backup))
        for state, _replacement, _backup in prepared:
            _ensure_unchanged(state)
    except (OSError, ExternalPatchError) as error:
        _cleanup_prepared(prepared)
        if isinstance(error, ExternalPatchError):
            raise
        raise ExternalPatchError("could not prepare atomic patch application") from error

    replaced: list[tuple[_TargetState, Path]] = []
    try:
        for state, replacement, backup in prepared:
            _ensure_unchanged(state)
            os.replace(replacement, state.path)
            replaced.append((state, backup))
    except (OSError, ExternalPatchError) as error:
        rollback_failed = False
        for state, backup in reversed(replaced):
            try:
                os.replace(backup, state.path)
            except OSError:
                rollback_failed = True
        _cleanup_prepared(prepared)
        message = "could not apply patch atomically"
        if rollback_failed:
            message += "; rollback also failed"
        raise ExternalPatchError(message) from error
    _cleanup_prepared(prepared)


def _write_temporary(directory: Path, prefix: str, content: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=prefix, dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, mode)
        return path
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def _ensure_unchanged(state: _TargetState) -> None:
    try:
        _ensure_safe_components(
            state.root,
            PurePosixPath(state.relative_path).parts,
        )
        current = state.path.stat()
    except OSError as error:
        raise ExternalPatchError("patch target changed before application") from error
    if _stat_identity(current) != state.identity:
        raise ExternalPatchError("patch target changed before application")


def _cleanup_prepared(prepared: list[tuple[_TargetState, Path, Path]]) -> None:
    for _state, replacement, backup in prepared:
        replacement.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)

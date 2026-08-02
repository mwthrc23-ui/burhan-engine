from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import Hypothesis


@dataclass(frozen=True, slots=True)
class VerificationResult:
    grade: str
    checks: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "checks": list(self.checks),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class PatchResult:
    diff: str
    changed_files: tuple[str, ...]
    applied: bool
    artifact_hash: str
    verification: VerificationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "unified_diff",
            "diff": self.diff,
            "changed_files": list(self.changed_files),
            "applied": self.applied,
            "artifact_hash": self.artifact_hash,
            "verification": self.verification.to_dict(),
        }


class PatchEngine:
    def repair(self, project: Path, hypothesis: Hypothesis, *, apply: bool = False) -> PatchResult:
        if hypothesis.kind != "undefined_name" or not hypothesis.suggested_replacement:
            raise ValueError("this repair engine currently supports undefined names with a known replacement")
        if not hypothesis.location:
            raise ValueError("hypothesis has no source location")

        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project directory does not exist: {root}")
        relative_hint, line_number = self._parse_location(hypothesis.location)
        source_path = self._resolve_source(root, relative_hint)
        if source_path.suffix.lower() not in {".py", ".pyi"}:
            raise ValueError("V0 repair currently supports Python source files only")
        if source_path.stat().st_size > 1_000_000:
            raise ValueError("source file exceeds the 1 MB repair limit")

        original = source_path.read_text(encoding="utf-8")
        updated = self._replace_on_line(
            original,
            line_number,
            hypothesis.target,
            hypothesis.suggested_replacement,
        )
        ast.parse(updated, filename=source_path.name)

        relative_path = source_path.relative_to(root).as_posix()
        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
                lineterm="",
            )
        )
        artifact_hash = f"sha256:{hashlib.sha256(diff.encode('utf-8')).hexdigest()}"
        if apply:
            self._atomic_write(source_path, updated)

        return PatchResult(
            diff=diff,
            changed_files=(relative_path,),
            applied=apply,
            artifact_hash=artifact_hash,
            verification=VerificationResult(
                grade="V0",
                checks=("path_in_project", "single_identifier_replacement", "python_ast_parse"),
                limitations=("لم تُشغّل اختبارات المشروع", "لم يُفحص السلوك وقت التشغيل"),
            ),
        )

    @staticmethod
    def _parse_location(location: str) -> tuple[str, int]:
        parts = location.rsplit(":", 2)
        if len(parts) == 3 and parts[-1].isdigit() and parts[-2].isdigit():
            path_text = parts[0]
            line_text = parts[-2]
        else:
            try:
                path_text, line_text = location.rsplit(":", 1)
            except ValueError as error:
                raise ValueError("invalid source location") from error
        if not line_text.isdigit() or int(line_text) <= 0:
            raise ValueError("invalid source line number")
        return path_text, int(line_text)

    @staticmethod
    def _resolve_source(root: Path, path_text: str) -> Path:
        candidate = Path(path_text)
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("source path is outside project")
        if not resolved.is_file():
            raise ValueError(f"source file does not exist: {path_text}")
        return resolved

    @staticmethod
    def _replace_on_line(content: str, line_number: int, target: str, replacement: str) -> str:
        lines = content.splitlines(keepends=True)
        if line_number > len(lines):
            raise ValueError("reported line is outside the source file")
        pattern = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)")
        line = lines[line_number - 1]
        occurrences = len(pattern.findall(line))
        if occurrences != 1:
            raise ValueError("target must appear exactly once on the reported line")
        updated_line = pattern.sub(replacement, line, count=1)
        updated_lines = tuple(lines[: line_number - 1]) + (updated_line,) + tuple(lines[line_number:])
        return "".join(updated_lines)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".burhan-", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, path.stat().st_mode)
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

from __future__ import annotations

import ast
import difflib
import hashlib
import importlib.util
import keyword
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from .external_patch import ExternalPatchResult, apply_external_patch
from .model import AnalysisResult, BirEdge, BirNode, Evidence, Hypothesis, NodeKind
from .scanner import (
    TraversalLimitError,
    bounded_walk,
    is_excluded_directory,
    is_reparse_path,
    is_secret_file,
)


DEFAULT_DOCKER_IMAGE = (
    "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)

# Pinned pytest image (python:3.12-slim + pytest 9.1.1).
# Rebuild with: docker build -f docker/Dockerfile.pytest -t burhan-pytest:local .
# Then pin: docker inspect --format='{{index .RepoDigests 0}}' burhan-pytest:local
PYTEST_DOCKER_IMAGE = (
    "ghcr.io/mwthrc23-ui/burhan-pytest@sha256:"
    "45181883b866f80ef1f3d1dc00661148eaf6b2e3715d0b7592d8905fd5221280"
)
PINNED_DOCKER_IMAGE_PATTERN = re.compile(
    r"(?=.{1,255}@sha256:)"
    r"(?:[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})/)?"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
    r"@sha256:[0-9a-f]{64}"
)


class ProofConfigurationError(ValueError):
    """The requested proof cannot start because its inputs are invalid."""


class ProofInfrastructureError(RuntimeError):
    """The proof runtime is unavailable or failed before a verdict."""


class ProofRejected(ValueError):
    """The proof ran far enough to reject the proposed repair."""


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


@dataclass(frozen=True, slots=True)
class CommandRun:
    exit_code: int | None
    timed_out: bool
    duration_ms: float
    stdout: str
    stderr: str
    output_truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": round(self.duration_ms, 3),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_truncated": self.output_truncated,
        }


@dataclass(frozen=True, slots=True)
class ProofResult:
    verified: bool
    command: tuple[str, ...]
    before: CommandRun
    after: CommandRun
    patch: PatchResult
    original_unchanged: bool
    verification: VerificationResult
    backend: str
    runtime: str
    project_manifest_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "command": list(self.command),
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "patch": self.patch.to_dict(),
            "original_unchanged": self.original_unchanged,
            "verification": self.verification.to_dict(),
            "backend": self.backend,
            "runtime": self.runtime,
            "project_manifest_fingerprint": self.project_manifest_fingerprint,
        }


class PatchEngine:
    _PYTHON_KINDS = frozenset(
        {
            "attribute_error",
            "missing_import_name",
            "missing_key",
            "missing_module",
            "unbound_local_variable",
            "undefined_name",
        }
    )
    _JAVASCRIPT_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx"})
    _JAVASCRIPT_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

    def repair(self, project: Path, hypothesis: Hypothesis, *, apply: bool = False) -> PatchResult:
        replacement = hypothesis.suggested_replacement
        if not replacement:
            raise ValueError("this repair engine requires a known replacement")
        if not hypothesis.location:
            raise ValueError("hypothesis has no source location")
        if not hypothesis.target or any(
            marker in value for value in (hypothesis.target, replacement) for marker in "\r\n"
        ):
            raise ValueError("repair target and replacement must be single-line values")

        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project directory does not exist: {root}")
        relative_hint, line_number = self._parse_location(hypothesis.location)
        source_path = self._resolve_source(root, relative_hint)
        if source_path.stat().st_size > 1_000_000:
            raise ValueError("source file exceeds the 1 MB repair limit")

        original = source_path.read_text(encoding="utf-8")
        suffix = source_path.suffix.lower()
        if suffix in {".py", ".pyi"}:
            checks = self._repair_python(
                root,
                source_path,
                original,
                line_number,
                hypothesis.kind,
                hypothesis.target,
                replacement,
            )
            updated = checks[0]
            verification_checks = checks[1]
        elif suffix in self._JAVASCRIPT_SUFFIXES:
            if hypothesis.kind != "undefined_name":
                raise ValueError("JavaScript repair supports undefined-name typos only")
            updated = self._replace_javascript_identifier_on_line(
                original, line_number, hypothesis.target, replacement
            )
            verification_checks = (
                "path_in_project",
                "single_identifier_replacement",
                "javascript_identifier_token",
            )
        else:
            raise ValueError("repair supports Python and TypeScript/JavaScript source files only")

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
                checks=verification_checks,
                limitations=("لم تُشغّل اختبارات المشروع", "لم يُفحص السلوك وقت التشغيل"),
            ),
        )

    def _repair_python(
        self,
        root: Path,
        source_path: Path,
        original: str,
        line_number: int,
        kind: str,
        target: str,
        replacement: str,
    ) -> tuple[str, tuple[str, ...]]:
        if kind not in self._PYTHON_KINDS:
            raise ValueError("unsupported conservative Python repair kind")
        try:
            tree = ast.parse(original, filename=source_path.name)
        except SyntaxError as error:
            raise ValueError("source is not valid Python") from error
        self._validate_python_context(tree, line_number, kind, target)
        if kind != "missing_key" and kind != "missing_module" and (
            not replacement.isidentifier() or keyword.iskeyword(replacement)
        ):
            raise ValueError("Python name replacement must be a valid identifier")
        if kind == "missing_key" and (
            not replacement.isprintable()
            or any(marker in replacement for marker in ("'", '"', "\\"))
        ):
            raise ValueError("replacement must remain a single literal key")
        if kind == "missing_module" and not self._local_module_exists(
            root, source_path, tree, line_number, target, replacement
        ):
            raise ValueError("module replacement must name a local module")
        updated = self._replace_on_line(original, line_number, target, replacement)
        try:
            ast.parse(updated, filename=source_path.name)
        except SyntaxError as error:
            raise ValueError("patched source is not valid Python") from error
        return updated, (
            "path_in_project",
            "single_identifier_replacement",
            "python_syntax_context",
            "python_ast_parse",
        )

    @staticmethod
    def _validate_python_context(
        tree: ast.AST, line_number: int, kind: str, target: str
    ) -> None:
        matches = 0
        for node in ast.walk(tree):
            if kind in {"undefined_name", "unbound_local_variable"}:
                matches += int(
                    isinstance(node, ast.Name)
                    and node.id == target
                    and node.lineno == line_number
                )
            elif kind == "attribute_error":
                matches += int(
                    isinstance(node, ast.Attribute)
                    and node.attr == target
                    and getattr(node, "end_lineno", node.lineno) == line_number
                )
            elif kind == "missing_import_name":
                matches += PatchEngine._matching_imported_names(node, line_number, target)
            elif kind == "missing_module":
                matches += PatchEngine._matching_imported_modules(node, line_number, target)
            elif kind == "missing_key":
                matches += int(
                    isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                    and node.slice.value == target
                    and node.slice.lineno == line_number
                )
        if matches == 0:
            raise ValueError("target must occur once in the required Python syntax context")
        if matches > 1:
            raise ValueError("target must appear exactly once on the reported line")

    @staticmethod
    def _matching_imported_names(node: ast.AST, line_number: int, target: str) -> int:
        if not isinstance(node, ast.ImportFrom):
            return 0
        return sum(
            alias.name == target
            and getattr(alias, "lineno", node.lineno) == line_number
            for alias in node.names
        )

    @staticmethod
    def _matching_imported_modules(node: ast.AST, line_number: int, target: str) -> int:
        if isinstance(node, ast.Import):
            return sum(
                alias.name == target
                and getattr(alias, "lineno", node.lineno) == line_number
                for alias in node.names
            )
        if isinstance(node, ast.ImportFrom):
            return int(node.module == target and node.lineno == line_number)
        return 0

    @staticmethod
    def _local_module_exists(
        root: Path,
        source_path: Path,
        tree: ast.AST,
        line_number: int,
        target: str,
        module: str,
    ) -> bool:
        if not module or not all(part.isidentifier() for part in module.split(".")):
            return False
        relative = Path(*module.split("."))
        search_roots = PatchEngine._module_search_roots(
            root, source_path, tree, line_number, target
        )
        candidates = tuple(
            candidate
            for search_root in search_roots
            for candidate in (
                search_root / relative.with_suffix(".py"),
                search_root / relative.with_suffix(".pyi"),
                search_root / relative / "__init__.py",
                search_root / relative / "__init__.pyi",
            )
        )
        return any(
            candidate.is_file()
            and not PatchEngine._has_link_or_reparse(root, candidate)
            and candidate.resolve().is_relative_to(root)
            for candidate in candidates
        )

    @staticmethod
    def _module_search_roots(
        root: Path,
        source_path: Path,
        tree: ast.AST,
        line_number: int,
        target: str,
    ) -> tuple[Path, ...]:
        relative_import = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == target
                and node.lineno == line_number
                and node.level > 0
            ),
            None,
        )
        if relative_import is None:
            return (root, root / "src")
        package_root = source_path.parent
        for _ in range(relative_import.level - 1):
            package_root = package_root.parent
        return (package_root,)

    @classmethod
    def _replace_javascript_identifier_on_line(
        cls, content: str, line_number: int, target: str, replacement: str
    ) -> str:
        if not cls._JAVASCRIPT_IDENTIFIER.fullmatch(target) or not cls._JAVASCRIPT_IDENTIFIER.fullmatch(
            replacement
        ):
            raise ValueError("JavaScript repair requires valid identifier tokens")
        lines = content.splitlines(keepends=True)
        if line_number > len(lines):
            raise ValueError("reported line is outside the source file")
        line = lines[line_number - 1]
        pattern = re.compile(rf"(?<![\w$]){re.escape(target)}(?![\w$])")
        occurrences = tuple(pattern.finditer(line))
        if len(occurrences) != 1:
            raise ValueError("target must appear exactly once on the reported line")
        occurrence = occurrences[0]
        prefix = "".join(lines[: line_number - 1])
        if (
            cls._javascript_prefix_has_open_context(prefix)
            or occurrence.span() not in cls._javascript_identifier_spans(line)
        ):
            raise ValueError("target is not a JavaScript identifier token")
        updated_line = line[: occurrence.start()] + replacement + line[occurrence.end() :]
        return "".join(lines[: line_number - 1] + [updated_line] + lines[line_number:])

    @classmethod
    def _javascript_identifier_spans(cls, line: str) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        index = 0
        while index < len(line):
            if line.startswith("//", index):
                break
            if line.startswith("/*", index):
                end = line.find("*/", index + 2)
                if end < 0:
                    break
                index = end + 2
                continue
            if line[index] == "/":
                index = cls._skip_javascript_regex(line, index)
                continue
            if line[index] in {"'", '"', "`"}:
                index = cls._skip_javascript_string(line, index)
                continue
            match = cls._JAVASCRIPT_IDENTIFIER.match(line, index)
            if match:
                spans.append(match.span())
                index = match.end()
                continue
            index += 1
        return tuple(spans)

    @staticmethod
    def _javascript_prefix_has_open_context(content: str) -> bool:
        quote: str | None = None
        in_block_comment = False
        in_line_comment = False
        index = 0
        while index < len(content):
            if in_line_comment:
                if content[index] == "\n":
                    in_line_comment = False
                index += 1
            elif in_block_comment:
                if content.startswith("*/", index):
                    in_block_comment = False
                    index += 2
                else:
                    index += 1
            elif quote is not None:
                if content[index] == "\\":
                    index += 2
                elif content[index] == quote:
                    quote = None
                    index += 1
                else:
                    index += 1
            elif content.startswith("//", index):
                in_line_comment = True
                index += 2
            elif content.startswith("/*", index):
                in_block_comment = True
                index += 2
            elif content[index] in {"'", '"', "`"}:
                quote = content[index]
                index += 1
            else:
                index += 1
        return in_block_comment or quote is not None

    @staticmethod
    def _skip_javascript_regex(line: str, start: int) -> int:
        index = start + 1
        in_character_class = False
        while index < len(line):
            if line[index] == "\\":
                index += 2
            elif line[index] == "[":
                in_character_class = True
                index += 1
            elif line[index] == "]":
                in_character_class = False
                index += 1
            elif line[index] == "/" and not in_character_class:
                index += 1
                while index < len(line) and line[index].isalpha():
                    index += 1
                return index
            else:
                index += 1
        return len(line)

    @staticmethod
    def _skip_javascript_string(line: str, start: int) -> int:
        quote = line[start]
        index = start + 1
        while index < len(line):
            if line[index] == "\\":
                index += 2
            elif line[index] == quote:
                return index + 1
            else:
                index += 1
        return len(line)

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
        lexical = Path(os.path.abspath(candidate if candidate.is_absolute() else root / candidate))
        if lexical.is_relative_to(root) and PatchEngine._has_link_or_reparse(root, lexical):
            raise ValueError("source path contains a link or reparse point")
        resolved = lexical.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("source path is outside project")
        if not resolved.is_file():
            raise ValueError(f"source file does not exist: {path_text}")
        return resolved

    @staticmethod
    def _has_link_or_reparse(root: Path, candidate: Path) -> bool:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return True
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink() or (current.exists() and is_reparse_path(current)):
                return True
        return False

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


class ProofRunner:
    _IGNORED_DIRECTORIES = frozenset(
        {
            ".git",
            ".hg",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".svn",
            ".venv",
            "__pycache__",
            "build",
            "coverage",
            "dist",
            "node_modules",
            "venv",
        }
    )
    _MAX_FILES = 2_000
    _MAX_ENTRIES = 10_000
    _MAX_DIRECTORIES = 2_000
    _MAX_DIRECTORY_DEPTH = 32
    _MAX_TOTAL_BYTES = 50_000_000
    _MAX_FILE_BYTES = 5_000_000
    _MAX_OUTPUT_BYTES = 65_536
    _MAX_MANIFEST_FILES = 10_000
    _MAX_MANIFEST_ENTRIES = 15_000
    _MAX_MANIFEST_DIRECTORIES = 5_000
    _MAX_MANIFEST_TOTAL_BYTES = 250_000_000
    _MAX_MANIFEST_FILE_BYTES = 100_000_000

    def prove(
        self,
        project: Path,
        hypothesis: Hypothesis | None,
        *,
        external_patch: str | bytes | None = None,
        test_program: str = "python",
        test_args: tuple[str, ...] = ("app.py",),
        timeout_seconds: float = 30.0,
        backend: str = "local",
        docker_image: str = DEFAULT_DOCKER_IMAGE,
        expected_project_fingerprint: str | None = None,
    ) -> ProofResult:
        if backend not in {"local", "docker"}:
            raise ProofConfigurationError("unsupported proof backend; use local or docker")
        if (hypothesis is None) == (external_patch is None):
            raise ProofConfigurationError(
                "provide exactly one analyzed hypothesis or external patch"
            )
        command, executable = self._command(
            test_program, test_args, local=backend == "local"
        )
        if backend == "local" and test_program == "pytest" and importlib.util.find_spec("pytest") is None:
            raise ProofInfrastructureError("pytest is not installed in the local proof runtime")
        if backend == "docker" and PINNED_DOCKER_IMAGE_PATTERN.fullmatch(docker_image) is None:
            raise ProofConfigurationError("pinned Docker image must be a safe OCI digest reference")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 300:
            raise ProofConfigurationError("timeout must be between 0 and 300 seconds")

        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ProofConfigurationError(f"project directory does not exist: {root}")
        if hypothesis is not None:
            if not hypothesis.location:
                raise ProofConfigurationError("hypothesis has no source location")
            relative_hint, _ = PatchEngine._parse_location(hypothesis.location)
            original_source = PatchEngine._resolve_source(root, relative_hint)
            relative_source = original_source.relative_to(root)
            if any(is_excluded_directory(part) for part in relative_source.parts[:-1]):
                raise ProofConfigurationError("proof target is outside the analyzed scan scope")
        else:
            assert external_patch is not None
            try:
                apply_external_patch(root, external_patch, apply=False)
            except ValueError as error:
                raise ProofConfigurationError("external patch is invalid or unsafe") from error
        original_manifest = self._project_manifest(
            root, allow_secret_metadata=backend == "docker"
        )
        project_fingerprint = f"sha256:{original_manifest}"
        if (
            expected_project_fingerprint is not None
            and project_fingerprint != expected_project_fingerprint
        ):
            raise ProofRejected("project changed after the analysis snapshot")

        with tempfile.TemporaryDirectory(prefix="burhan-proof-") as directory:
            sandbox = Path(directory) / "project"
            self._copy_project(root, sandbox)
            before = (
                self._run_docker(
                    command,
                    sandbox,
                    image=docker_image,
                    timeout_seconds=timeout_seconds,
                )
                if backend == "docker"
                else self._run(
                    executable,
                    sandbox,
                    timeout_seconds=timeout_seconds,
                )
            )
            if before.timed_out:
                raise ProofRejected("test timed out before patch")
            if before.exit_code == 0:
                raise ProofRejected("test already passes before patch")
            if hypothesis is not None and not self._baseline_matches_hypothesis(
                before, hypothesis
            ):
                raise ProofRejected("baseline failure does not match analyzed error")

            try:
                if hypothesis is not None:
                    patch = PatchEngine().repair(sandbox, hypothesis, apply=True)
                else:
                    assert external_patch is not None
                    patch = self._external_patch_result(
                        apply_external_patch(sandbox, external_patch, apply=True)
                    )
            except ValueError as error:
                raise ProofRejected("patch could not be applied to the proof copy") from error
            after = (
                self._run_docker(
                    command,
                    sandbox,
                    image=docker_image,
                    timeout_seconds=timeout_seconds,
                )
                if backend == "docker"
                else self._run(
                    executable,
                    sandbox,
                    timeout_seconds=timeout_seconds,
                )
            )
            if after.timed_out:
                raise ProofRejected("test timed out after patch")
            if after.exit_code != 0:
                raise ProofRejected("test still fails after patch")

        original_unchanged = (
            self._project_manifest(root, allow_secret_metadata=backend == "docker")
            == original_manifest
        )
        if not original_unchanged:
            raise ProofRejected("original project changed during proof")
        return ProofResult(
            verified=True,
            command=command,
            before=before,
            after=after,
            patch=patch,
            original_unchanged=True,
            verification=VerificationResult(
                grade="V2" if backend == "docker" else "V1",
                checks=(
                    "temporary_copy",
                    "test_failed_before_patch",
                    "patch_applied_to_copy",
                    "test_passed_after_patch",
                    "original_unchanged",
                    "shell_false",
                    "sanitized_environment",
                    "parent_timeout_enforced",
                )
                + (("external_patch_validated",) if external_patch is not None else ())
                + (
                    (
                        "network_disabled",
                        "read_only_container",
                        "capabilities_dropped",
                        "resource_limits",
                    )
                    if backend == "docker"
                    else ()
                ),
                limitations=(
                    "الإثبات السلوكي يثبت انتقال الأمر نفسه من الفشل إلى النجاح ولا يثبت السبب وحده",
                )
                + (
                    ("صورة Docker يجب تثبيتها إلى digest لإعادة إنتاج طويلة الأمد",)
                    if backend == "docker"
                    else (
                        "اختبارات محلية موثوقة فقط؛ ليست حاوية أمنية ولا تضمن إنهاء العمليات الفرعية",
                    )
                ),
            ),
            backend=backend,
            runtime=docker_image if backend == "docker" else sys.version.split()[0],
            project_manifest_fingerprint=project_fingerprint,
        )

    @classmethod
    def fingerprint_project(cls, project: Path, *, backend: str) -> str:
        if backend not in {"local", "docker"}:
            raise ProofConfigurationError("unsupported proof backend; use local or docker")
        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ProofConfigurationError(f"project directory does not exist: {root}")
        manifest = cls._project_manifest(root, allow_secret_metadata=backend == "docker")
        return f"sha256:{manifest}"

    @staticmethod
    def _command(
        test_program: str,
        test_args: tuple[str, ...],
        *,
        local: bool = True,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if test_program not in {"python", "pytest", "tsc"}:
            raise ProofConfigurationError("unsupported test program; use python, pytest, or tsc")
        if len(test_args) > 64 or sum(len(argument) for argument in test_args) > 16_384:
            raise ProofConfigurationError("test arguments exceed count or total-size limits")
        if not all(
            isinstance(argument, str)
            and argument
            and "\0" not in argument
            and len(argument) <= 4_096
            for argument in test_args
        ):
            raise ProofConfigurationError(
                "test arguments must be non-empty strings of at most 4096 characters"
            )
        display = (test_program,) + tuple(test_args)
        if test_program == "python":
            executable = (sys.executable,) + tuple(test_args)
        elif test_program == "pytest":
            executable = (sys.executable, "-m", "pytest") + tuple(test_args)
        elif local:
            compiler = shutil.which("tsc")
            if compiler is None:
                raise ProofInfrastructureError("tsc is not installed in the local proof runtime")
            executable = (compiler,) + tuple(test_args)
        else:
            executable = display
        return display, executable

    @staticmethod
    def _external_patch_result(result: ExternalPatchResult) -> PatchResult:
        return PatchResult(
            diff=result.diff,
            changed_files=result.changed_files,
            applied=result.applied,
            artifact_hash=result.artifact_hash,
            verification=VerificationResult(
                grade="V0",
                checks=result.verification_checks + ("external_patch_validated",),
                limitations=(
                    "تم التحقق تركيبيًا من الرقعة؛ الإثبات السلوكي مستقل في نتيجة V1/V2",
                ),
            ),
        )

    @classmethod
    def _copy_project(cls, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True)
        file_count = 0
        total_bytes = 0
        try:
            for current_path, _directories, names in bounded_walk(
                source,
                max_entries=cls._MAX_ENTRIES,
                max_directories=cls._MAX_DIRECTORIES,
                max_depth=cls._MAX_DIRECTORY_DEPTH,
                exclude_directory=lambda name: (
                    name in cls._IGNORED_DIRECTORIES
                    or is_excluded_directory(name)
                    or name.startswith(".env")
                ),
            ):
                current_relative = current_path.relative_to(source)
                target_directory = destination / current_relative
                target_directory.mkdir(parents=True, exist_ok=True)
                for name in names:
                    path = current_path / name
                    relative = path.relative_to(source)
                    if is_secret_file(path):
                        continue
                    if path.suffix.lower() in {".sqlite3", ".sqlite3-shm", ".sqlite3-wal"}:
                        continue
                    if path.is_symlink() or is_reparse_path(path) or not path.is_file():
                        continue
                    size = path.stat().st_size
                    file_count += 1
                    total_bytes += size
                    if size > cls._MAX_FILE_BYTES:
                        raise ProofConfigurationError(
                            f"proof input file exceeds 5 MB: {relative.as_posix()}"
                        )
                    if file_count > cls._MAX_FILES or total_bytes > cls._MAX_TOTAL_BYTES:
                        raise ProofConfigurationError("project exceeds local proof copy limits")
                    shutil.copy2(path, target_directory / name, follow_symlinks=False)
        except TraversalLimitError as error:
            raise ProofConfigurationError(str(error)) from error

    @classmethod
    def _project_manifest(cls, root: Path, *, allow_secret_metadata: bool = True) -> str:
        """Hash the complete original tree without following links or exposing content."""

        digest = hashlib.sha256()
        file_count = 0
        total_bytes = 0
        try:
            cls._add_manifest_record(
                digest,
                b"root",
                b"",
                str(root.stat().st_mode).encode("ascii"),
            )
            for current_path, directories, names in bounded_walk(
                root,
                max_entries=cls._MAX_MANIFEST_ENTRIES,
                max_directories=cls._MAX_MANIFEST_DIRECTORIES,
                max_depth=cls._MAX_DIRECTORY_DEPTH,
            ):
                relative_root = current_path.relative_to(root)
                for name in directories:
                    path = current_path / name
                    relative = (relative_root / name).as_posix()
                    cls._add_manifest_record(
                        digest,
                        b"directory",
                        relative.encode("utf-8"),
                        str(path.stat().st_mode).encode("ascii"),
                    )

                for name in names:
                    path = current_path / name
                    relative = (relative_root / name).as_posix()
                    if path.is_symlink() or is_reparse_path(path):
                        link_stat = path.lstat()
                        cls._add_manifest_record(
                            digest,
                            b"link",
                            relative.encode("utf-8"),
                            str(link_stat.st_mode).encode("ascii"),
                            os.readlink(path).encode("utf-8", errors="surrogatepass"),
                        )
                        continue
                    if not path.is_file():
                        cls._add_manifest_record(
                            digest,
                            b"other",
                            relative.encode("utf-8"),
                            str(path.lstat().st_mode).encode("ascii"),
                        )
                        continue
                    file_stat = path.stat()
                    size = file_stat.st_size
                    file_count += 1
                    total_bytes += size
                    if size > cls._MAX_MANIFEST_FILE_BYTES:
                        raise ProofConfigurationError("project file exceeds original-tree size limit")
                    if (
                        file_count > cls._MAX_MANIFEST_FILES
                        or total_bytes > cls._MAX_MANIFEST_TOTAL_BYTES
                    ):
                        raise ProofConfigurationError("project exceeds original-tree manifest limits")
                    if is_secret_file(path):
                        if not allow_secret_metadata:
                            raise ProofConfigurationError(
                                "local proof cannot attest projects containing secret files"
                            )
                        cls._add_manifest_record(
                            digest,
                            b"secret-metadata",
                            relative.encode("utf-8"),
                            str(size).encode("ascii"),
                            str(file_stat.st_mtime_ns).encode("ascii"),
                            str(file_stat.st_ctime_ns).encode("ascii"),
                            str(file_stat.st_mode).encode("ascii"),
                            str(file_stat.st_dev).encode("ascii"),
                            str(file_stat.st_ino).encode("ascii"),
                        )
                        continue
                    content_digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            content_digest.update(chunk)
                    cls._add_manifest_record(
                        digest,
                        b"file",
                        relative.encode("utf-8"),
                        str(size).encode("ascii"),
                        str(file_stat.st_mode).encode("ascii"),
                        content_digest.digest(),
                    )
        except ProofConfigurationError:
            raise
        except TraversalLimitError as error:
            raise ProofConfigurationError(str(error)) from error
        except OSError as error:
            raise ProofInfrastructureError("could not fingerprint the original project") from error
        return digest.hexdigest()

    @staticmethod
    def _add_manifest_record(digest: Any, *fields: bytes) -> None:
        digest.update(len(fields).to_bytes(4, "big"))
        for field in fields:
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)

    @classmethod
    def _run(
        cls,
        command: tuple[str, ...],
        cwd: Path,
        *,
        timeout_seconds: float,
    ) -> CommandRun:
        started = perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=cls._safe_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
            stdout, stdout_truncated = cls._decode_output(completed.stdout)
            stderr, stderr_truncated = cls._decode_output(completed.stderr)
            return CommandRun(
                exit_code=completed.returncode,
                timed_out=False,
                duration_ms=(perf_counter() - started) * 1_000,
                stdout=stdout,
                stderr=stderr,
                output_truncated=stdout_truncated or stderr_truncated,
            )
        except subprocess.TimeoutExpired as error:
            stdout, stdout_truncated = cls._decode_output(error.stdout or b"")
            stderr, stderr_truncated = cls._decode_output(error.stderr or b"")
            return CommandRun(
                exit_code=None,
                timed_out=True,
                duration_ms=(perf_counter() - started) * 1_000,
                stdout=stdout,
                stderr=stderr,
                output_truncated=stdout_truncated or stderr_truncated,
            )

    @classmethod
    def _run_docker(
        cls,
        command: tuple[str, ...],
        cwd: Path,
        *,
        image: str,
        timeout_seconds: float,
    ) -> CommandRun:
        docker = shutil.which("docker")
        if docker is None:
            raise ProofInfrastructureError("Docker CLI is not installed")
        source = str(cwd.resolve())
        if "," in source:
            raise ProofConfigurationError("Docker proof path cannot contain a comma")
        container_name = f"burhan-proof-{uuid4().hex[:12]}"
        docker_command = (
            docker,
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,source={source},target=/workspace,readonly",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONIOENCODING=utf-8",
            image,
        ) + tuple(command)
        result = cls._run(docker_command, cwd, timeout_seconds=timeout_seconds)
        if result.timed_out:
            try:
                subprocess.run(
                    (docker, "rm", "-f", container_name),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                    shell=False,
                    env=cls._safe_environment(),
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise ProofInfrastructureError("Docker timeout cleanup failed") from error
        if result.exit_code in {125, 126, 127}:
            raise ProofInfrastructureError("Docker could not start the proof command")
        return result

    @staticmethod
    def _baseline_matches_hypothesis(run: CommandRun, hypothesis: Hypothesis) -> bool:
        supported_kinds = PatchEngine._PYTHON_KINDS
        if hypothesis.kind not in supported_kinds or not hypothesis.location:
            return False
        relative_hint, line_number = PatchEngine._parse_location(hypothesis.location)
        combined = f"{run.stdout}\n{run.stderr}".replace("\\", "/")
        target = re.escape(hypothesis.target)
        normalized_hint = relative_hint.replace("\\", "/")
        locations = {normalized_hint}
        if "/" not in normalized_hint:
            locations.add(Path(relative_hint).name)

        if hypothesis.kind == "undefined_name" and any(
            re.search(
                rf"{re.escape(location)}\({line_number},\d+\):\s+error\s+TS2304:"
                rf"[^\r\n]*Cannot find name\s+['\"]{target}['\"]",
                combined,
            )
            for location in locations
        ):
            return True
        if hypothesis.kind == "undefined_name" and any(
            re.search(
                rf"ReferenceError:[^\r\n]*\b{target}\s+is not defined[^\r\n]*\r?\n"
                rf"[ \t]*at[ \t]+(?:[^\r\n(]*\()?"
                rf"{re.escape(location)}:{line_number}:\d+\)?",
                combined,
            )
            for location in locations
        ):
            return True

        if hypothesis.kind == "unbound_local_variable":
            matching_error = re.search(
                rf"UnboundLocalError:\s+(?:cannot access local variable\s+|local variable\s+)"
                rf"['\"]{target}['\"]",
                combined,
            )
        elif hypothesis.kind == "undefined_name":
            matching_error = re.search(
                rf"NameError:\s*name\s*['\"]{target}['\"]\s*is not defined",
                combined,
            )
        elif hypothesis.kind == "attribute_error":
            matching_error = re.search(
                rf"AttributeError:\s*['\"][^'\"]+['\"] object has no attribute "
                rf"['\"]{target}['\"]",
                combined,
            )
        elif hypothesis.kind == "missing_import_name":
            matching_error = re.search(
                rf"ImportError:\s*cannot import name\s*['\"]{target}['\"]",
                combined,
            )
        elif hypothesis.kind == "missing_module":
            matching_error = re.search(
                rf"(?:ModuleNotFoundError|ImportError):\s*No module named\s*"
                rf"['\"]{target}['\"]",
                combined,
            )
        else:
            matching_error = re.search(
                rf"KeyError:\s*['\"]{target}['\"]",
                combined,
            )
        matching_location = any(
            re.search(
                rf"{re.escape(location)}(?:['\"],?\s*line\s+|:){line_number}\b",
                combined,
            )
            for location in locations
        )
        return bool(matching_error) and matching_location

    @classmethod
    def _decode_output(cls, output: bytes) -> tuple[str, bool]:
        truncated = len(output) > cls._MAX_OUTPUT_BYTES
        return (
            output[: cls._MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
            truncated,
        )

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        allowed = ("COMSPEC", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
        environment = {key: os.environ[key] for key in allowed if key in os.environ}
        environment.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        return environment

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()


# ---------------------------------------------------------------------------
# Test-result evidence injection
# ---------------------------------------------------------------------------

def inject_test_evidence(analysis: AnalysisResult, proof: ProofResult) -> AnalysisResult:
    """Return a new :class:`AnalysisResult` with test-run outcomes added as BIR evidence.

    Two new evidence items are appended to the primary hypothesis:

    * ``test_run_before`` — the baseline run that *failed* before the patch.
    * ``test_run_after``  — the run that *passed* after the patch.

    Two ``BirNode`` entries of kind ``EVIDENCE`` are also added to the BIR
    state so that the graph explicitly records the test outcomes.
    """
    from dataclasses import replace as _replace

    before = proof.before
    after = proof.after

    before_summary = (
        f"اختبار قبل الرقعة: فشل (exit={before.exit_code}, "
        f"{round(before.duration_ms, 1)} ms)"
    )
    after_summary = (
        f"اختبار بعد الرقعة: نجح (exit={after.exit_code}, "
        f"{round(after.duration_ms, 1)} ms)"
    )

    new_evidence = (
        Evidence(source="test_run_before", summary=before_summary, weight=1.5),
        Evidence(source="test_run_after", summary=after_summary, weight=2.0),
    )

    primary = analysis.primary
    updated_primary = _replace(primary, evidence=primary.evidence + new_evidence)
    updated_hypotheses = (updated_primary,) + analysis.hypotheses[1:]

    state = analysis.state
    for idx, ev in enumerate(new_evidence):
        node_id = f"evidence:test_run:{idx}"
        state = state.with_node(
            BirNode(
                node_id,
                NodeKind.EVIDENCE,
                ev.summary,
                (("source", ev.source), ("weight", str(ev.weight))),
            )
        )
        state = state.with_edge(BirEdge("hypothesis:0", "supported_by", node_id, ev.weight))

    return _replace(analysis, hypotheses=updated_hypotheses, state=state)


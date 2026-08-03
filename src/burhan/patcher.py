from __future__ import annotations

import ast
import difflib
import hashlib
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

from .model import AnalysisResult, BirEdge, BirNode, Evidence, Hypothesis, NodeKind
from .scanner import is_secret_file


DEFAULT_DOCKER_IMAGE = (
    "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)

# Pinned pytest image (python:3.12-slim + pytest 8.x).
# Rebuild with: docker build -f docker/Dockerfile.pytest -t burhan-pytest:local .
# Then pin: docker inspect --format='{{index .RepoDigests 0}}' burhan-pytest:local
PYTEST_DOCKER_IMAGE = (
    "burhan-pytest@sha256:0000000000000000000000000000000000000000000000000000000000000000"
)


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
        }


class PatchEngine:
    def repair(self, project: Path, hypothesis: Hypothesis, *, apply: bool = False) -> PatchResult:
        if hypothesis.kind not in ("undefined_name", "unbound_local_variable") or not hypothesis.suggested_replacement:
            raise ValueError("this repair engine currently supports undefined/unbound names with a known replacement")
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
    _MAX_TOTAL_BYTES = 50_000_000
    _MAX_FILE_BYTES = 5_000_000
    _MAX_OUTPUT_BYTES = 65_536

    def prove(
        self,
        project: Path,
        hypothesis: Hypothesis,
        *,
        test_program: str = "python",
        test_args: tuple[str, ...] = ("app.py",),
        timeout_seconds: float = 30.0,
        backend: str = "local",
        docker_image: str = DEFAULT_DOCKER_IMAGE,
    ) -> ProofResult:
        command, executable = self._command(test_program, test_args)
        if backend not in {"local", "docker"}:
            raise ValueError("unsupported proof backend; use local or docker")
        if backend == "docker" and not re.fullmatch(
            r"[^\s@]+@sha256:[0-9a-f]{64}", docker_image
        ):
            raise ValueError("Docker V2 image must be pinned by sha256 digest")
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("timeout must be between 0 and 300 seconds")

        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project directory does not exist: {root}")
        if not hypothesis.location:
            raise ValueError("hypothesis has no source location")
        relative_hint, _ = PatchEngine._parse_location(hypothesis.location)
        original_source = PatchEngine._resolve_source(root, relative_hint)
        original_hash = self._file_hash(original_source)

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
                raise ValueError("test timed out before patch")
            if before.exit_code == 0:
                raise ValueError("test already passes before patch")
            if not self._baseline_matches_hypothesis(before, hypothesis):
                raise ValueError("baseline failure does not match analyzed error")

            patch = PatchEngine().repair(sandbox, hypothesis, apply=True)
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
                raise ValueError("test timed out after patch")
            if after.exit_code != 0:
                raise ValueError("test still fails after patch")

        original_unchanged = self._file_hash(original_source) == original_hash
        if not original_unchanged:
            raise RuntimeError("original project changed during proof")
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
        )

    @staticmethod
    def _command(
        test_program: str, test_args: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if test_program not in {"python", "pytest"}:
            raise ValueError("unsupported test program; use python or pytest")
        if not all(
            isinstance(argument, str)
            and argument
            and "\0" not in argument
            and len(argument) <= 4_096
            for argument in test_args
        ):
            raise ValueError("test arguments must be non-empty strings of at most 4096 characters")
        display = (test_program,) + tuple(test_args)
        executable = (
            (sys.executable,) + tuple(test_args)
            if test_program == "python"
            else (sys.executable, "-m", "pytest") + tuple(test_args)
        )
        return display, executable

    @classmethod
    def _copy_project(cls, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True)
        file_count = 0
        total_bytes = 0
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if any(part in cls._IGNORED_DIRECTORIES for part in relative.parts):
                continue
            if path.name == ".env" or path.name.startswith(".env."):
                continue
            if is_secret_file(path):
                continue
            if path.suffix.lower() in {".sqlite3", ".sqlite3-shm", ".sqlite3-wal"}:
                continue
            is_junction = getattr(path, "is_junction", lambda: False)()
            if path.is_symlink() or is_junction:
                continue
            target = destination / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not path.is_file():
                continue
            size = path.stat().st_size
            file_count += 1
            total_bytes += size
            if size > cls._MAX_FILE_BYTES:
                raise ValueError(f"proof input file exceeds 5 MB: {relative.as_posix()}")
            if file_count > cls._MAX_FILES or total_bytes > cls._MAX_TOTAL_BYTES:
                raise ValueError("project exceeds local proof copy limits")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=False)

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
            raise ValueError("Docker CLI is not installed")
        source = str(cwd.resolve())
        if "," in source:
            raise ValueError("Docker proof path cannot contain a comma")
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
        return result

    @staticmethod
    def _baseline_matches_hypothesis(run: CommandRun, hypothesis: Hypothesis) -> bool:
        if hypothesis.kind != "undefined_name" or not hypothesis.location:
            return False
        relative_hint, line_number = PatchEngine._parse_location(hypothesis.location)
        combined = f"{run.stdout}\n{run.stderr}".replace("\\", "/")
        target = re.escape(hypothesis.target)
        name_error = re.search(
            rf"NameError:\s*name\s*['\"]{target}['\"]\s*is not defined",
            combined,
        )
        normalized_hint = relative_hint.replace("\\", "/")
        locations = {normalized_hint}
        if "/" not in normalized_hint:
            locations.add(Path(relative_hint).name)
        matching_location = any(
            re.search(
                rf"{re.escape(location)}(?:['\"],?\s*line\s+|:){line_number}\b",
                combined,
            )
            for location in locations
        )
        return bool(name_error) and matching_location

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
        state = state.with_edge(BirEdge(f"hypothesis:0", "supported_by", node_id, ev.weight))

    return _replace(analysis, hypotheses=updated_hypotheses, state=state)


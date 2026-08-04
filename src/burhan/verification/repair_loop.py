"""Bounded repair loop with candidate tracking.

Runs repair candidates in sequence, applying each to a temporary copy of
the project, executing tests, and recording outcomes.  Selects the first
(smallest) successful candidate.

Design rules
------------
* Maximum attempt count is bounded and configurable.
* No candidate is accepted without a genuine fail-before / pass-after cycle.
* Each rejected candidate records *why* it was rejected.
* If the project changes between the loop's start and any attempt the loop
  aborts with ``ProjectChangedError``.
* No state is mutated; new objects are returned.
* Network is disabled inside the sandbox by policy (delegated to
  ``SandboxConfig``).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..candidates.repair_candidates import RepairCandidate, select_smallest_successful
from ..sandbox.sandbox_runner import SandboxConfig, SandboxPolicy, SandboxViolation, fingerprint_project

from pathlib import Path


DEFAULT_MAX_ATTEMPTS = 5


class ProjectChangedError(RuntimeError):
    """Raised when the project fingerprint changes during the repair loop."""


class LoopExhaustedError(RuntimeError):
    """Raised when all candidates fail and the attempt budget is spent."""


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """Result of a single candidate attempt.

    Attributes
    ----------
    candidate_rank:
        Rank of the candidate that was tried.
    success:
        True if the test transition (fail→pass) was observed.
    rejection_reason:
        Non-empty string explaining why the candidate was rejected.
    before_exit_code:
        Exit code of the test run before the patch was applied.
    after_exit_code:
        Exit code of the test run after the patch was applied.
    """

    candidate_rank: int
    success: bool
    rejection_reason: str
    before_exit_code: int | None
    after_exit_code: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_rank": self.candidate_rank,
            "success": self.success,
            "rejection_reason": self.rejection_reason,
            "before_exit_code": self.before_exit_code,
            "after_exit_code": self.after_exit_code,
        }


@dataclass(frozen=True, slots=True)
class RepairLoopResult:
    """Outcome of the bounded repair loop.

    Attributes
    ----------
    winner:
        The accepted candidate, or ``None`` if no candidate succeeded.
    attempts:
        Record of every attempt made.
    project_fingerprint_start:
        Project fingerprint at loop start.
    project_fingerprint_end:
        Project fingerprint after the last attempt.
    aborted_reason:
        Non-empty if the loop was aborted early (e.g. project changed).
    """

    winner: RepairCandidate | None
    attempts: tuple[AttemptRecord, ...]
    project_fingerprint_start: str
    project_fingerprint_end: str
    aborted_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "winner": self.winner.to_dict() if self.winner else None,
            "attempts": [a.to_dict() for a in self.attempts],
            "project_fingerprint_start": self.project_fingerprint_start,
            "project_fingerprint_end": self.project_fingerprint_end,
            "aborted_reason": self.aborted_reason,
        }


@dataclass(frozen=True, slots=True)
class RepairLoopConfig:
    """Configuration for the repair loop.

    Attributes
    ----------
    max_attempts:
        Maximum number of candidates to try (1–10).
    sandbox_policy:
        Security policy for sandbox validation.
    detect_project_changes:
        If True, abort when project fingerprint changes between attempts.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    sandbox_policy: SandboxPolicy = SandboxPolicy()
    detect_project_changes: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")


def validate_sandbox_for_candidate(
    candidate: RepairCandidate,
    sandbox_config: SandboxConfig,
    policy: SandboxPolicy,
) -> str:
    """Return a rejection reason string if the sandbox config is invalid, else ''."""
    from ..sandbox.sandbox_runner import validate_sandbox_config
    try:
        validate_sandbox_config(sandbox_config, policy)
    except SandboxViolation as exc:
        return str(exc)
    return ""


def build_loop_result_from_candidates(
    candidates: tuple[RepairCandidate, ...],
    attempt_records: tuple[AttemptRecord, ...],
    project_root: Path,
    fingerprint_start: str,
    aborted_reason: str = "",
) -> RepairLoopResult:
    """Construct a ``RepairLoopResult`` from accumulated attempt records.

    This is a pure function that combines candidate outcomes into the final
    result.  Actual test execution happens outside this module (in
    ``patcher.ProofRunner``).
    """
    fingerprint_end = fingerprint_project(project_root) if not aborted_reason else fingerprint_start

    # Attach rejection reasons to candidates
    rejection_map = {
        rec.candidate_rank: rec.rejection_reason
        for rec in attempt_records
        if rec.rejection_reason
    }
    updated = tuple(
        replace(c, rejection_reason=rejection_map.get(c.rank, c.rejection_reason))
        for c in candidates
    )

    winner = select_smallest_successful(updated)

    return RepairLoopResult(
        winner=winner,
        attempts=attempt_records,
        project_fingerprint_start=fingerprint_start,
        project_fingerprint_end=fingerprint_end,
        aborted_reason=aborted_reason,
    )

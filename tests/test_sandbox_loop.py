"""Tests for Phase 4: Sandbox and Bounded Repair Loop.

Covers mandatory test cases:
6. Project changes between analysis and proof
7. Narrow test passes while regression test fails
8. Unpinned Docker image or invalid digest
"""
from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from burhan.sandbox.sandbox_runner import (
    SandboxConfig,
    SandboxPolicy,
    SandboxViolation,
    fingerprint_project,
    validate_sandbox_config,
)
from burhan.verification.repair_loop import (
    AttemptRecord,
    RepairLoopConfig,
    RepairLoopResult,
    build_loop_result_from_candidates,
)
from burhan.candidates.repair_candidates import RepairCandidate, generate_candidates
from burhan.diagnosis.hypothesis_engine import HypothesisEngine
from burhan.model import Evidence, Hypothesis

_PINNED_IMAGE = (
    "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)


# ---------------------------------------------------------------------------
# Helper: build a minimal SandboxConfig
# ---------------------------------------------------------------------------

def _make_config(
    image: str = _PINNED_IMAGE,
    network_disabled: bool = True,
    read_only_mount: bool = True,
    capabilities_dropped: bool = True,
    memory_mb: int = 512,
    cpu_quota: int = 100000,
    timeout_seconds: int = 60,
    project_root: Path | None = None,
) -> SandboxConfig:
    return SandboxConfig(
        image=image,
        command=("pytest", "-q"),
        project_root=project_root or Path("/tmp/project"),
        project_fingerprint="sha256:aabbcc",
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
        cpu_quota=cpu_quota,
        network_disabled=network_disabled,
        read_only_mount=read_only_mount,
        capabilities_dropped=capabilities_dropped,
    )


# ---------------------------------------------------------------------------
# Mandatory test 8: Unpinned / invalid Docker digest
# ---------------------------------------------------------------------------

class SandboxValidationTests(unittest.TestCase):
    def test_pinned_image_passes(self) -> None:
        config = _make_config(image=_PINNED_IMAGE)
        validate_sandbox_config(config)  # must not raise

    def test_unpinned_image_raises(self) -> None:
        config = _make_config(image="python:3.12-slim")
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_image_without_digest_raises(self) -> None:
        config = _make_config(image="myregistry.io/myimage:latest")
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_image_with_short_digest_raises(self) -> None:
        config = _make_config(image="python@sha256:abc123")
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_network_enabled_raises(self) -> None:
        config = _make_config(network_disabled=False)
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_network_enabled_with_allow_network_passes(self) -> None:
        config = _make_config(network_disabled=False)
        policy = SandboxPolicy(allow_network=True, require_network_disabled=True)
        validate_sandbox_config(config, policy)  # must not raise

    def test_read_only_false_raises(self) -> None:
        config = _make_config(read_only_mount=False)
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_capabilities_not_dropped_raises(self) -> None:
        config = _make_config(capabilities_dropped=False)
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_zero_memory_raises(self) -> None:
        config = _make_config(memory_mb=0)
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_timeout_zero_raises(self) -> None:
        config = _make_config(timeout_seconds=0)
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config)

    def test_timeout_exceeds_max_raises(self) -> None:
        policy = SandboxPolicy(max_timeout_seconds=120)
        config = _make_config(timeout_seconds=300)
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config, policy)

    def test_allow_list_rejects_unknown_image(self) -> None:
        allowed = "other@sha256:" + "a" * 64
        policy = SandboxPolicy(allowed_digests=(allowed,))
        config = _make_config(image=_PINNED_IMAGE)
        with self.assertRaises(SandboxViolation):
            validate_sandbox_config(config, policy)

    def test_allow_list_accepts_known_image(self) -> None:
        policy = SandboxPolicy(allowed_digests=(_PINNED_IMAGE,))
        config = _make_config(image=_PINNED_IMAGE)
        validate_sandbox_config(config, policy)  # must not raise

    def test_to_dict_serialisable(self) -> None:
        import json
        config = _make_config()
        json.dumps(config.to_dict())  # must not raise

    def test_policy_to_dict_serialisable(self) -> None:
        import json
        policy = SandboxPolicy()
        json.dumps(policy.to_dict())


# ---------------------------------------------------------------------------
# fingerprint_project
# ---------------------------------------------------------------------------

class FingerprintProjectTests(unittest.TestCase):
    def test_fingerprint_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            fp1 = fingerprint_project(root)
            fp2 = fingerprint_project(root)
            self.assertEqual(fp1, fp2)

    def test_fingerprint_changes_when_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "app.py"
            f.write_text("x = 1\n", encoding="utf-8")
            fp1 = fingerprint_project(root)
            f.write_text("x = 2\n", encoding="utf-8")
            fp2 = fingerprint_project(root)
            self.assertNotEqual(fp1, fp2)

    # -----------------------------------------------------------------------
    # Mandatory test 6: Project changes between analysis and proof
    # -----------------------------------------------------------------------
    def test_fingerprint_detects_project_change(self) -> None:
        """Simulates change of project between analysis and proof phases."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def foo(): pass\n", encoding="utf-8")
            fp_before = fingerprint_project(root)
            # Simulate project modification
            (root / "app.py").write_text("def foo(): return 1\n", encoding="utf-8")
            fp_after = fingerprint_project(root)
            self.assertNotEqual(fp_before, fp_after)

    def test_env_file_excluded_from_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            fp1 = fingerprint_project(root)
            (root / ".env").write_text("SECRET=abc\n", encoding="utf-8")
            fp2 = fingerprint_project(root)
            # .env must NOT change the fingerprint
            self.assertEqual(fp1, fp2)


# ---------------------------------------------------------------------------
# RepairLoopConfig
# ---------------------------------------------------------------------------

class RepairLoopConfigTests(unittest.TestCase):
    def test_default_config_valid(self) -> None:
        config = RepairLoopConfig()
        self.assertEqual(config.max_attempts, 5)

    def test_invalid_max_attempts_raises(self) -> None:
        with self.assertRaises(ValueError):
            RepairLoopConfig(max_attempts=0)
        with self.assertRaises(ValueError):
            RepairLoopConfig(max_attempts=11)


# ---------------------------------------------------------------------------
# build_loop_result_from_candidates (pure function, no Docker)
# ---------------------------------------------------------------------------

def _make_candidate(rank: int, rejection: str = "") -> RepairCandidate:
    from burhan.energy import confidence_from_energy, hypothesis_energy
    ev = (Evidence("runtime", "test", 2.0),)
    energy = hypothesis_energy(ev, uncertainty=0.1, unresolved_constraints=0, estimated_change_size=1)
    conf = confidence_from_energy(energy, len(ev))
    hyp = Hypothesis(
        kind="undefined_name", target="foo", explanation="test",
        location="app.py:5", energy=energy, confidence=conf,
        suggested_replacement=None, evidence=ev,
    )
    return RepairCandidate(
        rank=rank, hypothesis=hyp, description="test desc",
        target_file="app.py", target_line=5, change_size=1,
        affected_files=("app.py",), confidence=conf, risk="low",
        supporting_evidence=("ev1",), opposing_evidence=(),
        rejection_reason=rejection,
    )


class BuildLoopResultTests(unittest.TestCase):
    def test_winner_selected_when_candidate_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x=1\n", encoding="utf-8")
            fp = fingerprint_project(root)
            candidates = (_make_candidate(1),)
            attempts = (AttemptRecord(
                candidate_rank=1, success=True, rejection_reason="",
                before_exit_code=1, after_exit_code=0,
            ),)
            result = build_loop_result_from_candidates(candidates, attempts, root, fp)
            self.assertIsNotNone(result.winner)

    def test_no_winner_when_all_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x=1\n", encoding="utf-8")
            fp = fingerprint_project(root)
            candidates = (_make_candidate(1),)
            attempts = (AttemptRecord(
                candidate_rank=1, success=False,
                rejection_reason="test still failing after patch",
                before_exit_code=1, after_exit_code=1,
            ),)
            result = build_loop_result_from_candidates(candidates, attempts, root, fp)
            self.assertIsNone(result.winner)
            # The rejection reason should be attached to the candidate
            self.assertTrue(len(result.attempts) > 0)

    # -----------------------------------------------------------------------
    # Mandatory test 7: Narrow test passes, regression test fails
    # -----------------------------------------------------------------------
    def test_regression_failure_causes_rejection(self) -> None:
        """Narrow test passes but a second regression attempt fails → no winner."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x=1\n", encoding="utf-8")
            fp = fingerprint_project(root)
            candidates = (_make_candidate(1),)
            # First attempt: target test passes, BUT regression fails
            attempts = (AttemptRecord(
                candidate_rank=1, success=False,
                rejection_reason="اختبار الانحدار فشل بعد تطبيق الرقعة",
                before_exit_code=1, after_exit_code=0,  # target passed
            ),)
            result = build_loop_result_from_candidates(candidates, attempts, root, fp)
            # Must be no winner because regression failed
            self.assertIsNone(result.winner)
            self.assertIn("انحدار", result.attempts[0].rejection_reason)

    def test_to_dict_serialisable(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x=1\n", encoding="utf-8")
            fp = fingerprint_project(root)
            candidates = (_make_candidate(1),)
            attempts = (AttemptRecord(
                candidate_rank=1, success=True, rejection_reason="",
                before_exit_code=1, after_exit_code=0,
            ),)
            result = build_loop_result_from_candidates(candidates, attempts, root, fp)
            json.dumps(result.to_dict())  # must not raise

    def test_aborted_reason_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fp = fingerprint_project(root)
            result = build_loop_result_from_candidates((), (), root, fp, "تغيّر المشروع")
            self.assertIn("تغيّر", result.aborted_reason)
            self.assertIsNone(result.winner)


if __name__ == "__main__":
    unittest.main()

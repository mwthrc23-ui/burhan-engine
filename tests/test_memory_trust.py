"""Tests for Phase 6: Trusted Repair Memory.

Covers mandatory test case:
10. Attempt to promote a memory entry with a forged ProofResult
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from burhan.memory import TrustLevel, RepairEpisode, RepairMemory


# ---------------------------------------------------------------------------
# TrustLevel
# ---------------------------------------------------------------------------

class TrustLevelTests(unittest.TestCase):
    def test_enum_values(self) -> None:
        self.assertEqual(TrustLevel.RAW_SOURCE.value, "raw_source")
        self.assertEqual(TrustLevel.UNVERIFIED_LOCAL.value, "unverified_local")
        self.assertEqual(TrustLevel.LOCALLY_PROVEN.value, "locally_proven")
        self.assertEqual(TrustLevel.HUMAN_REVIEWED.value, "human_reviewed")

    def test_is_proven_locally_proven(self) -> None:
        self.assertTrue(TrustLevel.LOCALLY_PROVEN.is_proven())

    def test_is_proven_human_reviewed(self) -> None:
        self.assertTrue(TrustLevel.HUMAN_REVIEWED.is_proven())

    def test_is_not_proven_raw_source(self) -> None:
        self.assertFalse(TrustLevel.RAW_SOURCE.is_proven())

    def test_is_not_proven_unverified(self) -> None:
        self.assertFalse(TrustLevel.UNVERIFIED_LOCAL.is_proven())

    def test_minimum_for_retrieval(self) -> None:
        level = TrustLevel.minimum_for_retrieval()
        self.assertIsInstance(level, TrustLevel)

    def test_ordering_via_list(self) -> None:
        levels = list(TrustLevel)
        # All four must be present
        self.assertIn(TrustLevel.RAW_SOURCE, levels)
        self.assertIn(TrustLevel.HUMAN_REVIEWED, levels)


# ---------------------------------------------------------------------------
# Mandatory test 10: memory-promote rejects forged ProofResult
# ---------------------------------------------------------------------------

class MemoryPromoteSecurityTests(unittest.TestCase):
    """Validate that memory-promote stays fail-closed (disabled).

    The memory-promote gate must continue to reject self-asserted ProofResult
    objects passed in by the user.  This is not merely a policy decision but
    a hard security invariant: the engine itself must re-execute proof, not
    accept user-supplied proof data as truth.
    """

    _EPISODE: dict = {
        "id": "ep-phase6-001",
        "title": "phase 6 promote test",
        "domain": "python-pytest-attribute-error",
        "signature": {
            "error_kind": "attribute_error",
            "exception_type": "AttributeError",
            "normalized_message": "object has no attribute",
            "attribute_name": "foo",
        },
        "environment": {
            "language": "python",
            "test_framework": "pytest",
            "runtime_version": "3.12",
            "dependencies": [],
        },
        "root_cause": "typo in attribute name",
        "patch_pattern": {"kind": "rename", "from": "foo", "to": "bar"},
        "verification": {
            "grade": "V2",
            "reproduction_test": "test_foo",
            "evidence": ["fail-to-pass confirmed in docker"],
        },
        "provenance": {
            "source_type": "manual",
            "repository_url": None,
            "issue_url": None,
            "pull_request_url": None,
            "commit_sha": None,
            "license_spdx": None,
        },
    }

    def _proof_payload(self, verified: bool = True, backend: str = "docker") -> dict:
        return {
            "verified": verified,
            "command": ["pytest", "-q", "test_foo.py"],
            "before": {"exit_code": 1, "timed_out": False, "duration_ms": 12.0,
                       "stdout": "", "stderr": "AssertionError", "output_truncated": False},
            "after": {"exit_code": 0, "timed_out": False, "duration_ms": 8.0,
                      "stdout": "1 passed\n", "stderr": "", "output_truncated": False},
            "patch": {"diff": "", "changed_files": ["app.py"], "applied": True,
                      "artifact_hash": "sha256:abc",
                      "verification": {"grade": "V0", "checks": [], "limitations": []}},
            "original_unchanged": True,
            "verification": {
                "grade": "V2",
                "checks": ["test_failed_before_patch", "test_passed_after_patch", "original_unchanged"],
                "limitations": [],
            },
            "backend": backend,
            "runtime": "burhan-pytest@sha256:" + "1" * 64,
        }

    def _run(self, argv: list[str]) -> int:
        from burhan.cli import main
        return main(argv)

    def test_promote_always_rejects_user_proof(self) -> None:
        """memory-promote must return exit code 2 regardless of proof quality."""
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._proof_payload()), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = self._run([
                    "memory-promote",
                    "--database", str(db),
                    "--episode", str(ep),
                    "--proof", str(proof),
                    "--human-review-note", "مراجع: اختبار تلقائي",
                ])
            self.assertEqual(code, 2, "memory-promote يجب أن يرفض مع رمز خروج 2")
            self.assertFalse(db.exists(), "يجب ألا تُنشأ قاعدة البيانات عند الرفض")
            self.assertIn("معطلة", stderr.getvalue())

    def test_promote_rejects_even_with_valid_v2_proof(self) -> None:
        """A well-formed V2 proof must still be rejected (engine must re-prove)."""
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._proof_payload(verified=True)), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            code = self._run([
                "memory-promote",
                "--database", str(db),
                "--episode", str(ep),
                "--proof", str(proof),
                "--human-review-note", "valid looking proof",
            ])
            self.assertEqual(code, 2)

    def test_promote_rejects_unverified_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._proof_payload(verified=False)), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            code = self._run([
                "memory-promote",
                "--database", str(db),
                "--episode", str(ep),
                "--proof", str(proof),
                "--human-review-note", "unverified",
            ])
            self.assertEqual(code, 2)

    def test_promote_rejects_local_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._proof_payload(backend="local")), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            code = self._run([
                "memory-promote",
                "--database", str(db),
                "--episode", str(ep),
                "--proof", str(proof),
                "--human-review-note", "local backend",
            ])
            self.assertEqual(code, 2)


# ---------------------------------------------------------------------------
# RepairMemory – trust level integration
# ---------------------------------------------------------------------------

class RepairMemoryTrustTests(unittest.TestCase):
    _EPISODE_DATA: dict = {
        "id": "ep-trust-001",
        "title": "trust level test",
        "domain": "python-pytest-attribute-error",
        "signature": {
            "error_kind": "attribute_error",
            "exception_type": "AttributeError",
            "normalized_message": "object has no attribute",
            "attribute_name": "widget",
        },
        "environment": {
            "language": "python",
            "test_framework": "pytest",
            "runtime_version": "3.12",
            "dependencies": [],
        },
        "root_cause": "widget renamed to control",
        "patch_pattern": {"kind": "rename", "from": "widget", "to": "control"},
        "verification": {
            "grade": "V2",
            "reproduction_test": "test_widget",
            "evidence": ["fail-to-pass in docker"],
        },
        "provenance": {
            "source_type": "manual",
            "repository_url": None,
            "issue_url": None,
            "pull_request_url": None,
            "commit_sha": None,
            "license_spdx": None,
        },
        "root_cause_status": "curated",
    }

    def test_curated_episode_can_be_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.db"
            mem = RepairMemory(db)
            ep = RepairEpisode.from_dict(self._EPISODE_DATA)
            mem.add(ep)
            self.assertEqual(mem.count(), 1)

    def test_inferred_root_cause_cannot_be_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.db"
            mem = RepairMemory(db)
            data = dict(self._EPISODE_DATA)
            data["root_cause_status"] = "inferred"
            ep = RepairEpisode.from_dict(data)
            with self.assertRaises(ValueError):
                mem.add(ep)

    def test_unknown_root_cause_cannot_be_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.db"
            mem = RepairMemory(db)
            data = dict(self._EPISODE_DATA)
            data["root_cause_status"] = "unknown"
            ep = RepairEpisode.from_dict(data)
            with self.assertRaises(ValueError):
                mem.add(ep)


if __name__ == "__main__":
    unittest.main()

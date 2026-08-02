"""Tests for the four new roadmap features:

1. IncrementalProjectScanner with ScanCache
2. inject_test_evidence (test results → BIR)
3. PYTEST_DOCKER_IMAGE constant
4. memory-promote CLI gate
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from burhan.scanner import IncrementalProjectScanner, ScanCache, ScanLimits


# ---------------------------------------------------------------------------
# 1. ScanCache & IncrementalProjectScanner
# ---------------------------------------------------------------------------

class ScanCacheTests(unittest.TestCase):
    def test_new_file_needs_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            cache = ScanCache(cache_path)
            py = Path(tmp) / "app.py"
            py.write_text("x = 1\n", encoding="utf-8")
            stat = py.stat()
            self.assertTrue(cache.needs_reindex("app.py", stat))

    def test_unchanged_file_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            py = Path(tmp) / "app.py"
            py.write_text("x = 1\n", encoding="utf-8")
            stat = py.stat()
            cache = ScanCache(cache_path)
            cache.update("app.py", stat, "x = 1\n", ["x"])
            cache.flush()
            # Reload
            cache2 = ScanCache(cache_path)
            self.assertFalse(cache2.needs_reindex("app.py", stat))
            self.assertEqual(cache2.get_symbols("app.py"), ["x"])

    def test_modified_file_needs_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            py = Path(tmp) / "app.py"
            py.write_text("x = 1\n", encoding="utf-8")
            stat1 = py.stat()
            cache = ScanCache(cache_path)
            cache.update("app.py", stat1, "x = 1\n", ["x"])
            cache.flush()
            # Modify file
            time.sleep(0.01)
            py.write_text("x = 2\n", encoding="utf-8")
            stat2 = py.stat()
            cache2 = ScanCache(cache_path)
            self.assertTrue(cache2.needs_reindex("app.py", stat2))

    def test_cache_survives_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            cache_path.write_text("not-json!!!", encoding="utf-8")
            cache = ScanCache(cache_path)  # must not raise
            self.assertIsNone(cache.get_symbols("anything"))

    def test_cache_ignores_wrong_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            cache_path.write_text(
                json.dumps({"version": 99, "entries": {"f": {"mtime_ns": 0, "size_bytes": 0, "content_hash": "", "symbols": []}}}),
                encoding="utf-8",
            )
            cache = ScanCache(cache_path)
            self.assertIsNone(cache.get_symbols("f"))


class IncrementalScannerTests(unittest.TestCase):
    def test_incremental_scanner_returns_same_files_as_plain_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
            (root / "utils.py").write_text("X = 1\n", encoding="utf-8")
            scanner = IncrementalProjectScanner()
            snapshot = scanner.scan(root)
            paths = {f.relative_path for f in snapshot.files}
            self.assertIn("app.py", paths)
            self.assertIn("utils.py", paths)

    def test_cache_file_created_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            cache_path = Path(tmp) / "my-cache.json"
            scanner = IncrementalProjectScanner(cache_path=cache_path)
            scanner.scan(root)
            self.assertTrue(cache_path.exists())

    def test_second_scan_skips_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            py = root / "app.py"
            py.write_text("x = 1\n", encoding="utf-8")
            cache_path = Path(tmp) / "cache.json"
            scanner = IncrementalProjectScanner(cache_path=cache_path)
            snapshot1 = scanner.scan(root)
            snapshot2 = scanner.scan(root)
            self.assertEqual(len(snapshot1.files), len(snapshot2.files))
            self.assertEqual(snapshot1.files[0].content, snapshot2.files[0].content)

    def test_incremental_scanner_excludes_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=abc\n", encoding="utf-8")
            scanner = IncrementalProjectScanner()
            snapshot = scanner.scan(root)
            self.assertEqual(len(snapshot.files), 1)
            self.assertEqual(snapshot.skipped_secret_files, 1)


# ---------------------------------------------------------------------------
# 2. inject_test_evidence
# ---------------------------------------------------------------------------

class InjectTestEvidenceTests(unittest.TestCase):
    def _make_analysis(self) -> object:
        from pathlib import Path
        import tempfile
        from burhan.analyzer import BurhanAnalyzer

        error = "File \"app.py\", line 3\nNameError: name 'greet' is not defined"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text(
                "def greet_user(): pass\ngreet()\n",
                encoding="utf-8",
            )
            return BurhanAnalyzer().analyze(root, "شخّص الخطأ", error)

    def _make_proof(self) -> object:
        from burhan.patcher import CommandRun, PatchResult, ProofResult, VerificationResult

        before = CommandRun(
            exit_code=1,
            timed_out=False,
            duration_ms=120.0,
            stdout="",
            stderr="NameError: name 'greet' is not defined",
            output_truncated=False,
        )
        after = CommandRun(
            exit_code=0,
            timed_out=False,
            duration_ms=80.0,
            stdout="OK",
            stderr="",
            output_truncated=False,
        )
        patch = PatchResult(
            diff="",
            changed_files=("app.py",),
            applied=False,
            artifact_hash="sha256:abc",
            verification=VerificationResult(grade="V2", checks=(), limitations=()),
        )
        return ProofResult(
            verified=True,
            command=("python", "app.py"),
            before=before,
            after=after,
            patch=patch,
            original_unchanged=True,
            verification=VerificationResult(grade="V2", checks=(), limitations=()),
            backend="docker",
            runtime="python@sha256:abc",
        )

    def test_evidence_added_to_primary_hypothesis(self) -> None:
        from burhan.patcher import inject_test_evidence

        analysis = self._make_analysis()
        proof = self._make_proof()
        updated = inject_test_evidence(analysis, proof)
        sources = {ev.source for ev in updated.primary.evidence}
        self.assertIn("test_run_before", sources)
        self.assertIn("test_run_after", sources)

    def test_bir_nodes_added_for_test_runs(self) -> None:
        from burhan.patcher import inject_test_evidence

        analysis = self._make_analysis()
        proof = self._make_proof()
        updated = inject_test_evidence(analysis, proof)
        node_ids = {node.id for node in updated.state.nodes}
        self.assertIn("evidence:test_run:0", node_ids)
        self.assertIn("evidence:test_run:1", node_ids)

    def test_original_analysis_unchanged(self) -> None:
        from burhan.patcher import inject_test_evidence

        analysis = self._make_analysis()
        proof = self._make_proof()
        original_evidence_count = len(analysis.primary.evidence)
        inject_test_evidence(analysis, proof)
        self.assertEqual(len(analysis.primary.evidence), original_evidence_count)


# ---------------------------------------------------------------------------
# 3. PYTEST_DOCKER_IMAGE constant
# ---------------------------------------------------------------------------

class PytestDockerImageTests(unittest.TestCase):
    def test_constant_defined(self) -> None:
        from burhan.patcher import PYTEST_DOCKER_IMAGE

        self.assertIsInstance(PYTEST_DOCKER_IMAGE, str)
        self.assertIn("burhan-pytest@sha256:", PYTEST_DOCKER_IMAGE)

    def test_dockerfile_exists(self) -> None:
        here = Path(__file__).parent.parent
        dockerfile = here / "docker" / "Dockerfile.pytest"
        self.assertTrue(dockerfile.exists(), f"لم يُعثر على {dockerfile}")
        content = dockerfile.read_text(encoding="utf-8")
        self.assertIn("pytest", content.lower())


# ---------------------------------------------------------------------------
# 4. memory-promote CLI gate
# ---------------------------------------------------------------------------

class MemoryPromoteTests(unittest.TestCase):
    _EPISODE: dict = {
        "id": "ep-promote-001",
        "title": "test promote gate",
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

    _PROOF_V2: dict = {
        "verified": True,
        "verification": {"grade": "V2", "checks": [], "limitations": []},
    }

    _PROOF_V1: dict = {
        "verified": True,
        "verification": {"grade": "V1", "checks": [], "limitations": []},
    }

    _PROOF_UNVERIFIED: dict = {
        "verified": False,
        "verification": {"grade": "V2", "checks": [], "limitations": []},
    }

    def _run(self, argv: list[str]) -> int:
        from burhan.cli import main
        return main(argv)

    def test_promote_succeeds_with_v2_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._PROOF_V2), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            code = self._run([
                "memory-promote",
                "--database", str(db),
                "--episode", str(ep),
                "--proof", str(proof),
                "--human-review-note", "مراجع: محمد",
            ])
            self.assertEqual(code, 0)

    def test_promote_rejected_with_v1_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._PROOF_V1), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            code = self._run([
                "memory-promote",
                "--database", str(db),
                "--episode", str(ep),
                "--proof", str(proof),
                "--human-review-note", "مراجع: محمد",
            ])
            self.assertEqual(code, 2)

    def test_promote_rejected_when_proof_not_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._PROOF_UNVERIFIED), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            code = self._run([
                "memory-promote",
                "--database", str(db),
                "--episode", str(ep),
                "--proof", str(proof),
                "--human-review-note", "مراجع: محمد",
            ])
            self.assertEqual(code, 2)

    def test_promote_json_output(self) -> None:
        import io, contextlib
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            proof.write_text(json.dumps(self._PROOF_V2), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = self._run([
                    "memory-promote",
                    "--database", str(db),
                    "--episode", str(ep),
                    "--proof", str(proof),
                    "--human-review-note", "مراجع: علي",
                    "--json",
                ])
            self.assertEqual(code, 0)
            result = json.loads(buf.getvalue())
            self.assertEqual(result["promoted"], "ep-promote-001")
            self.assertEqual(result["grade"], "V2")
            self.assertIn("human_review_note", result)

    def test_promote_wrapped_proof_json(self) -> None:
        """يجب أن يقبل الـ proof المغلّف داخل {"analysis":..., "proof":...}."""
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp) / "ep.json"
            ep.write_text(json.dumps(self._EPISODE), encoding="utf-8")
            proof = Path(tmp) / "proof.json"
            wrapped = {"analysis": {}, "proof": self._PROOF_V2}
            proof.write_text(json.dumps(wrapped), encoding="utf-8")
            db = Path(tmp) / "mem.db"
            code = self._run([
                "memory-promote",
                "--database", str(db),
                "--episode", str(ep),
                "--proof", str(proof),
                "--human-review-note", "مراجع: سارة",
            ])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

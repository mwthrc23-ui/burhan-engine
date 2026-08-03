from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from burhan.cli import main
from burhan.model import AnalysisResult, BurhanState, Evidence, Hypothesis, Provenance
from burhan.patcher import (
    CommandRun,
    PatchResult,
    ProofRejected,
    ProofResult,
    VerificationResult,
)
from burhan.policy import (
    GatePolicy,
    evaluate_gate,
    fingerprint_command,
    fingerprint_runtime,
    load_policy,
    proof_failure_report,
)


GENERAL_CHECKS = (
    "temporary_copy",
    "test_failed_before_patch",
    "patch_applied_to_copy",
    "test_passed_after_patch",
    "original_unchanged",
    "shell_false",
    "sanitized_environment",
    "parent_timeout_enforced",
)


def _analysis(*, confidence: float = 0.9, truncated: bool = False) -> AnalysisResult:
    hypothesis = Hypothesis(
        kind="undefined_name",
        target="mesage",
        explanation="اسم قريب موجود في المشروع",
        location="main.py:4",
        energy=1.0,
        confidence=confidence,
        suggested_replacement="message",
        evidence=(Evidence(source="traceback", summary="matched", weight=1.0),),
    )
    return AnalysisResult(
        state=BurhanState.empty("SECRET_GOAL=must-not-leak"),
        hypotheses=(hypothesis,),
        elapsed_ms=4.2,
        case_id="case-123",
        provenance=Provenance(
            engine_version="0.7.0",
            input_fingerprint="sha256:input",
            analyzed_files=3,
            scan_truncated=truncated,
        ),
        residual_risks=("خطر متبقٍ",),
    )


def _proof(*, grade: str = "V2", backend: str = "docker") -> ProofResult:
    before = CommandRun(
        exit_code=1,
        timed_out=False,
        duration_ms=10.0,
        stdout="SECRET_TOKEN=must-not-leak",
        stderr="NameError: mesage",
        output_truncated=False,
    )
    after = CommandRun(
        exit_code=0,
        timed_out=False,
        duration_ms=8.0,
        stdout="ok",
        stderr="",
        output_truncated=False,
    )
    patch = PatchResult(
        diff="-print(mesage)\n+print(message)\n",
        changed_files=("SECRET_PATH.py",),
        applied=True,
        artifact_hash="sha256:patch",
        verification=VerificationResult(grade="V0", checks=("syntax_valid",), limitations=()),
    )
    checks = GENERAL_CHECKS
    if backend == "docker":
        checks += (
            "network_disabled",
            "read_only_container",
            "capabilities_dropped",
            "resource_limits",
        )
    return ProofResult(
        verified=True,
        command=("python", "main.py"),
        before=before,
        after=after,
        patch=patch,
        original_unchanged=True,
        verification=VerificationResult(grade=grade, checks=checks, limitations=()),
        backend=backend,
        runtime="SECRET_RUNTIME=must-not-leak",
        project_manifest_fingerprint="sha256:" + "1" * 64,
    )


class GatePolicyTests(unittest.TestCase):
    def test_repository_v2_policy_matches_secure_defaults(self) -> None:
        project = Path(__file__).resolve().parents[1]
        policy = load_policy(project / "examples" / "ci-policy-v2.json")

        self.assertEqual(policy.minimum_grade, "V2")
        self.assertEqual(policy.allowed_backends, ("docker",))
        self.assertEqual(
            policy.allowed_command_fingerprints,
            (fingerprint_command(("python", "app.py")),),
        )
        self.assertEqual(
            policy.allowed_runtime_fingerprints,
            (
                fingerprint_runtime(
                    "python@sha256:"
                    "57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
                ),
            ),
        )

    def test_direct_policy_construction_cannot_disable_invariants(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be disabled"):
            GatePolicy(require_complete_scan=False)
        with self.assertRaisesRegex(ValueError, "cannot be disabled"):
            GatePolicy(require_original_unchanged=False)
        with self.assertRaisesRegex(ValueError, "minimum_grade"):
            GatePolicy(minimum_grade="V0")

    def test_default_policy_accepts_supported_v2_without_leaking_raw_outputs(self) -> None:
        report = evaluate_gate(_analysis(), _proof(), GatePolicy())

        payload = report.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertTrue(report.passed)
        self.assertEqual(payload["schema_version"], "burhan.ci-gate/v1")
        self.assertEqual(payload["decision"], "pass")
        self.assertEqual(payload["observed"]["patch_artifact_hash"], "sha256:patch")
        self.assertNotIn("SECRET_TOKEN", encoded)
        self.assertNotIn("print(mesage)", encoded)
        self.assertNotIn("NameError: mesage", encoded)
        self.assertNotIn("SECRET_GOAL", encoded)
        self.assertNotIn("SECRET_PATH", encoded)
        self.assertNotIn("SECRET_RUNTIME", encoded)
        self.assertRegex(payload["policy_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(payload["report_fingerprint"], r"^sha256:[0-9a-f]{64}$")

    def test_default_policy_rejects_local_v1_and_low_confidence(self) -> None:
        report = evaluate_gate(
            _analysis(confidence=0.4),
            _proof(grade="V1", backend="local"),
            GatePolicy(),
        )

        codes = {violation.code for violation in report.violations}
        self.assertFalse(report.passed)
        self.assertIn("grade_below_minimum", codes)
        self.assertIn("confidence_below_minimum", codes)
        self.assertIn("backend_not_allowed", codes)

    def test_policy_rejects_truncated_scan_and_missing_required_check(self) -> None:
        proof = _proof()
        proof = ProofResult(
            verified=proof.verified,
            command=proof.command,
            before=proof.before,
            after=proof.after,
            patch=proof.patch,
            original_unchanged=proof.original_unchanged,
            verification=VerificationResult(
                grade=proof.verification.grade,
                checks=tuple(
                    check for check in proof.verification.checks if check != "resource_limits"
                ),
                limitations=proof.verification.limitations,
            ),
            backend=proof.backend,
            runtime=proof.runtime,
        )

        report = evaluate_gate(_analysis(truncated=True), proof, GatePolicy())

        codes = {violation.code for violation in report.violations}
        self.assertIn("scan_incomplete", codes)
        self.assertIn("required_check_missing", codes)
        missing = [
            item for item in report.violations
            if item.code == "required_check_missing" and "resource_limits" in item.message
        ]
        self.assertEqual(len(missing), 1)

    def test_policy_rechecks_fail_to_pass_invariants(self) -> None:
        proof = _proof()
        inconsistent = replace(
            proof,
            before=replace(proof.before, exit_code=0),
            after=replace(proof.after, exit_code=1),
        )

        report = evaluate_gate(_analysis(), inconsistent, GatePolicy())

        codes = {violation.code for violation in report.violations}
        self.assertIn("baseline_not_failed", codes)
        self.assertIn("patched_test_not_passed", codes)

    def test_policy_rejects_missing_project_manifest_fingerprint(self) -> None:
        report = evaluate_gate(
            _analysis(),
            replace(_proof(), project_manifest_fingerprint=""),
            GatePolicy(),
        )

        self.assertIn(
            "project_manifest_missing",
            {violation.code for violation in report.violations},
        )

    def test_report_fingerprint_changes_when_observation_changes(self) -> None:
        first = evaluate_gate(_analysis(confidence=0.9), _proof(), GatePolicy()).to_dict()
        second = evaluate_gate(_analysis(confidence=0.91), _proof(), GatePolicy()).to_dict()

        self.assertNotEqual(first["report_fingerprint"], second["report_fingerprint"])

    def test_report_binds_sanitized_command_and_runtime_fingerprints(self) -> None:
        proof = replace(_proof(), project_manifest_fingerprint="sha256:" + "2" * 64)
        first = evaluate_gate(_analysis(), proof, GatePolicy()).to_dict()
        command_changed = evaluate_gate(
            _analysis(), replace(proof, command=("python", "narrow.py")), GatePolicy()
        ).to_dict()
        runtime_changed = evaluate_gate(
            _analysis(), replace(proof, runtime="python@sha256:" + "1" * 64), GatePolicy()
        ).to_dict()

        self.assertEqual(
            first["observed"]["command_fingerprint"],
            fingerprint_command(proof.command),
        )
        self.assertEqual(
            first["observed"]["runtime_fingerprint"],
            fingerprint_runtime(proof.runtime),
        )
        self.assertEqual(
            first["observed"]["project_manifest_fingerprint"],
            "sha256:" + "2" * 64,
        )
        self.assertNotEqual(first["report_fingerprint"], command_changed["report_fingerprint"])
        self.assertNotEqual(first["report_fingerprint"], runtime_changed["report_fingerprint"])
        encoded = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("main.py", encoded)
        self.assertNotIn("SECRET_RUNTIME", encoded)

    def test_report_fingerprint_changes_with_full_project_manifest(self) -> None:
        first = evaluate_gate(
            _analysis(),
            replace(_proof(), project_manifest_fingerprint="sha256:" + "2" * 64),
            GatePolicy(),
        ).to_dict()
        second = evaluate_gate(
            _analysis(),
            replace(_proof(), project_manifest_fingerprint="sha256:" + "3" * 64),
            GatePolicy(),
        ).to_dict()

        self.assertNotEqual(first["report_fingerprint"], second["report_fingerprint"])

    def test_policy_can_pin_test_command_and_runtime_fingerprints(self) -> None:
        proof = _proof()
        policy = GatePolicy(
            allowed_command_fingerprints=(fingerprint_command(("python", "approved.py")),),
            allowed_runtime_fingerprints=(fingerprint_runtime("approved-runtime"),),
        )

        report = evaluate_gate(_analysis(), proof, policy)

        codes = {violation.code for violation in report.violations}
        self.assertIn("command_not_allowed", codes)
        self.assertIn("runtime_not_allowed", codes)

    def test_rejected_proof_report_binds_command_and_runtime(self) -> None:
        report = proof_failure_report(
            _analysis(),
            GatePolicy(),
            backend="docker",
            command=("python", "main.py"),
            runtime="python@sha256:" + "1" * 64,
            project_manifest_fingerprint="sha256:" + "2" * 64,
        ).to_dict()

        self.assertEqual(
            report["observed"]["command_fingerprint"],
            fingerprint_command(("python", "main.py")),
        )
        self.assertEqual(
            report["observed"]["runtime_fingerprint"],
            fingerprint_runtime("python@sha256:" + "1" * 64),
        )
        self.assertEqual(
            report["observed"]["project_manifest_fingerprint"],
            "sha256:" + "2" * 64,
        )

    def test_load_policy_rejects_unknown_fields_and_boolean_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unknown = root / "unknown.json"
            unknown.write_text('{"unknown": true}', encoding="utf-8")
            boolean = root / "boolean.json"
            boolean.write_text(
                '{"schema_version": "burhan.gate-policy/v1", "minimum_confidence": true}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "unknown policy fields"):
                load_policy(unknown)
            with self.assertRaisesRegex(ValueError, "minimum_confidence"):
                load_policy(boolean)

    def test_load_policy_rejects_schema_downgrades_and_duplicate_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_schema = root / "old.json"
            old_schema.write_text('{"schema_version": "v0"}', encoding="utf-8")
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                json.dumps(
                    {
                        "schema_version": "burhan.gate-policy/v1",
                        "required_checks": ["original_unchanged", "original_unchanged"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_policy(old_schema)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_policy(duplicate)

    def test_load_policy_rejects_duplicate_json_member_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate-member.json"
            path.write_text(
                '{"schema_version":"burhan.gate-policy/v1",'
                '"minimum_grade":"V2","minimum_grade":"V1"}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate policy field"):
                load_policy(path)

    def test_load_policy_rejects_oversized_files_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "large.json"
            policy_path.write_text(" " * 65_537, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "64 KiB"):
                load_policy(policy_path)


class CiGateCliTests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "project"
        project.mkdir()
        (project / "main.py").write_text(
            "def message():\n    return 'ok'\n\nprint(mesage())\n",
            encoding="utf-8",
        )
        return project

    def test_ci_gate_writes_a_sanitized_v1_report_when_policy_allows_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "burhan.gate-policy/v1",
                        "minimum_grade": "V1",
                        "minimum_confidence": 0.5,
                        "allowed_backends": ["local"],
                        "required_checks": list(GENERAL_CHECKS),
                    }
                ),
                encoding="utf-8",
            )
            report_path = root / "gate-report.json"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "ci-gate",
                        "--project",
                        str(project),
                        "--goal",
                        "أثبت الإصلاح",
                        "--error",
                        'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                        "--trust-local-tests",
                        "--test-program",
                        "python",
                        "--test-arg",
                        "main.py",
                        "--backend",
                        "local",
                        "--policy",
                        str(policy_path),
                        "--report",
                        str(report_path),
                        "--json",
                    ]
                )

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            printed = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, printed)
        self.assertEqual(payload["decision"], "pass")
        self.assertEqual(payload["observed"]["grade"], "V1")
        self.assertNotIn("diff", json.dumps(payload))
        self.assertNotIn("stdout", json.dumps(payload))

    def test_ci_gate_returns_one_and_writes_report_when_policy_denies_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            report_path = root / "denied.json"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "ci-gate",
                        "--project",
                        str(project),
                        "--goal",
                        "أثبت الإصلاح",
                        "--error",
                        'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                        "--trust-local-tests",
                        "--test-arg",
                        "main.py",
                        "--backend",
                        "local",
                        "--report",
                        str(report_path),
                        "--json",
                    ]
                )

            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["decision"], "fail")
        self.assertIn("grade_below_minimum", {item["code"] for item in payload["violations"]})

    def test_ci_gate_rejects_an_incomplete_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            (project / "oversized.py").write_text("x" * 1_000_001, encoding="utf-8")
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "burhan.gate-policy/v1",
                        "minimum_grade": "V1",
                        "minimum_confidence": 0.5,
                        "allowed_backends": ["local"],
                        "required_checks": list(GENERAL_CHECKS),
                    }
                ),
                encoding="utf-8",
            )
            report_path = root / "incomplete.json"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "ci-gate",
                        "--project",
                        str(project),
                        "--goal",
                        "أثبت الإصلاح",
                        "--error",
                        'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                        "--trust-local-tests",
                        "--test-arg",
                        "main.py",
                        "--backend",
                        "local",
                        "--policy",
                        str(policy_path),
                        "--report",
                        str(report_path),
                    ]
                )

            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertIn("scan_incomplete", {item["code"] for item in payload["violations"]})

    def test_ci_gate_rejects_untrusted_local_test_execution(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = main(
                [
                    "ci-gate",
                    "--project",
                    ".",
                    "--goal",
                    "أثبت الإصلاح",
                    "--error",
                    "NameError: name 'x' is not defined",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("--trust-local-tests", stderr.getvalue())

    def test_ci_gate_bounds_direct_goal_and_error_text(self) -> None:
        for option, value in (
            ("--goal", "x" * 16_385),
            ("--error", "x" * 1_000_001),
        ):
            with self.subTest(option=option):
                arguments = [
                    "ci-gate",
                    "--project",
                    ".",
                    "--goal",
                    "goal",
                    "--error",
                    "NameError: name 'x' is not defined",
                    "--trust-local-tests",
                ]
                index = arguments.index(option)
                arguments[index + 1] = value
                stderr = io.StringIO()

                with redirect_stderr(stderr):
                    exit_code = main(arguments)

                self.assertEqual(exit_code, 2)
                self.assertNotIn(value, stderr.getvalue())

    def test_ci_gate_writes_a_failure_report_when_proof_cannot_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            report_path = root / "failure.json"
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch("burhan.cli.ProofRunner.prove", side_effect=ProofRejected("SECRET_FAILURE")):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "ci-gate",
                            "--project",
                            str(project),
                            "--goal",
                            "أثبت الإصلاح",
                            "--error",
                            'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                            "--trust-local-tests",
                            "--backend",
                            "local",
                            "--report",
                            str(report_path),
                            "--json",
                        ]
                    )

            payload = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["decision"], "fail")
        self.assertEqual(payload["violations"][0]["code"], "proof_execution_failed")
        self.assertNotIn("SECRET_FAILURE", json.dumps(payload))
        self.assertNotIn("SECRET_FAILURE", stderr.getvalue())
        self.assertIn("proof_rejected", stderr.getvalue())

    def test_ci_gate_sanitizes_unexpected_internal_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            stderr = io.StringIO()

            with patch("burhan.cli.ProofRunner.prove", side_effect=SyntaxError("SECRET_SOURCE")):
                with redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "ci-gate",
                            "--project",
                            str(project),
                            "--goal",
                            "أثبت الإصلاح",
                            "--error",
                            'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                            "--trust-local-tests",
                            "--backend",
                            "local",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertIn("internal_error", stderr.getvalue())
        self.assertNotIn("SECRET_SOURCE", stderr.getvalue())

    def test_ci_gate_returns_two_for_invalid_docker_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = main(
                    [
                        "ci-gate",
                        "--project",
                        str(project),
                        "--goal",
                        "أثبت الإصلاح",
                        "--error",
                        'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                        "--trust-local-tests",
                        "--docker-image",
                        "python:latest",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("proof_configuration_error", stderr.getvalue())

    def test_ci_gate_rejects_nan_timeout_as_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = main(
                    [
                        "ci-gate",
                        "--project",
                        str(project),
                        "--goal",
                        "أثبت الإصلاح",
                        "--error",
                        'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                        "--trust-local-tests",
                        "--backend",
                        "local",
                        "--timeout",
                        "nan",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("proof_configuration_error", stderr.getvalue())

    def test_ci_gate_returns_two_when_docker_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            stderr = io.StringIO()

            with patch("burhan.patcher.shutil.which", return_value=None):
                with redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "ci-gate",
                            "--project",
                            str(project),
                            "--goal",
                            "أثبت الإصلاح",
                            "--error",
                            'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                            "--trust-local-tests",
                        ]
                    )

        self.assertEqual(exit_code, 2)
        self.assertIn("proof_infrastructure_error", stderr.getvalue())

    def test_ci_gate_never_overwrites_an_existing_report_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "report.json"
            target.write_text("sentinel", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = main(
                    [
                        "ci-gate",
                        "--project",
                        str(root),
                        "--goal",
                        "أثبت الإصلاح",
                        "--error",
                        "NameError: name 'x' is not defined",
                        "--trust-local-tests",
                        "--report",
                        str(target),
                    ]
                )

            unchanged = target.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 2)
        self.assertEqual(unchanged, "sentinel")

    def test_ci_gate_rejects_a_symlink_report_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            target = root / "report.json"
            try:
                os.symlink(sentinel, target)
            except OSError as error:
                self.skipTest(f"symlinks unavailable: {error}")

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(
                    [
                        "ci-gate",
                        "--project",
                        str(root),
                        "--goal",
                        "أثبت الإصلاح",
                        "--error",
                        "NameError: name 'x' is not defined",
                        "--trust-local-tests",
                        "--report",
                        str(target),
                    ]
                )

            unchanged = sentinel.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 2)
        self.assertEqual(unchanged, "unchanged")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from burhan.model import Hypothesis
from burhan.patcher import CommandRun, ProofRunner


BROKEN_SCRIPT = """\
def greet(name):
    return f"Hi {name}"

print(grete("Ada"))
"""

PINNED_PYTHON_IMAGE = (
    "python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)


def matching_failure() -> CommandRun:
    return CommandRun(
        1,
        False,
        10.0,
        "",
        "Traceback (most recent call last):\n"
        '  File "/workspace/app.py", line 4, in <module>\n'
        "NameError: name 'grete' is not defined\n",
        False,
    )


def undefined_name_hypothesis(location: str = "app.py:4") -> Hypothesis:
    return Hypothesis(
        kind="undefined_name",
        target="grete",
        explanation="The called name is misspelled.",
        location=location,
        energy=1.0,
        confidence=0.99,
        suggested_replacement="greet",
    )


class ProofRunnerTests(unittest.TestCase):
    def test_docker_fail_to_pass_is_graded_v2(self) -> None:
        failed = matching_failure()
        passed = CommandRun(0, False, 8.0, "Hi Ada\n", "", False)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "app.py"
            source.write_text(BROKEN_SCRIPT, encoding="utf-8")

            with patch.object(
                ProofRunner,
                "_run_docker",
                side_effect=(failed, passed),
            ):
                result = ProofRunner().prove(
                    project,
                    undefined_name_hypothesis(),
                    backend="docker",
                    docker_image=PINNED_PYTHON_IMAGE,
                    timeout_seconds=5,
                )

        self.assertEqual(result.verification.grade, "V2")
        self.assertEqual(result.backend, "docker")
        self.assertEqual(result.runtime, PINNED_PYTHON_IMAGE)
        self.assertIn("network_disabled", result.verification.checks)
        self.assertIn("read_only_container", result.verification.checks)

    def test_prove_returns_v1_only_after_fail_to_pass_without_writing_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "app.py"
            source.write_text(BROKEN_SCRIPT, encoding="utf-8")

            result = ProofRunner().prove(
                project,
                undefined_name_hypothesis(),
                timeout_seconds=5,
            )

            self.assertTrue(result.verified)
            self.assertEqual(result.verification.grade, "V1")
            self.assertNotEqual(result.before.exit_code, 0)
            self.assertEqual(result.after.exit_code, 0)
            self.assertTrue(result.patch.applied)
            self.assertTrue(result.original_unchanged)
            self.assertEqual(result.command, ("python", "app.py"))
            self.assertIn("test_failed_before_patch", result.verification.checks)
            self.assertIn("test_passed_after_patch", result.verification.checks)
            self.assertEqual(source.read_text(encoding="utf-8"), BROKEN_SCRIPT)

    def test_prove_rejects_a_test_that_already_passes_before_the_patch(self) -> None:
        source_text = """\
def unused():
    return grete("Ada")

print("healthy")
"""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "app.py"
            source.write_text(source_text, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "test already passes before patch"):
                ProofRunner().prove(
                    project,
                    undefined_name_hypothesis(location="app.py:2"),
                    timeout_seconds=5,
                )

            self.assertEqual(source.read_text(encoding="utf-8"), source_text)

    def test_prove_rejects_failure_unrelated_to_the_hypothesis(self) -> None:
        unrelated = CommandRun(1, False, 5.0, "", "AssertionError: unrelated", False)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            with patch.object(ProofRunner, "_run", return_value=unrelated):
                with self.assertRaisesRegex(ValueError, "does not match analyzed error"):
                    ProofRunner().prove(project, undefined_name_hypothesis(), timeout_seconds=5)

    def test_prove_rejects_matching_name_reported_on_a_different_line(self) -> None:
        wrong_line = CommandRun(
            1,
            False,
            5.0,
            "",
            '  File "/workspace/app.py", line 99, in <module>\n'
            "NameError: name 'grete' is not defined\n",
            False,
        )
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            with patch.object(ProofRunner, "_run", return_value=wrong_line):
                with self.assertRaisesRegex(ValueError, "does not match analyzed error"):
                    ProofRunner().prove(project, undefined_name_hypothesis(), timeout_seconds=5)

    def test_unbound_baseline_rejects_a_longer_name_with_the_same_prefix(self) -> None:
        hypothesis = Hypothesis(
            kind="unbound_local_variable",
            target="foo",
            explanation="The local name is wrong.",
            location="app.py:1",
            energy=1.0,
            confidence=0.99,
            suggested_replacement="bar",
        )
        failure = CommandRun(
            1,
            False,
            1.0,
            "",
            '  File "app.py", line 1, in <module>\n'
            "UnboundLocalError: cannot access local variable 'foobar' "
            "where it is not associated with a value\n",
            False,
        )

        self.assertFalse(
            ProofRunner._baseline_matches_hypothesis(failure, hypothesis)
        )

    def test_unbound_baseline_accepts_exact_old_and_modern_messages(self) -> None:
        hypothesis = Hypothesis(
            kind="unbound_local_variable",
            target="result",
            explanation="The local name is unbound.",
            location="app.py:4",
            energy=1.0,
            confidence=0.99,
            suggested_replacement="resolved",
        )
        messages = (
            "UnboundLocalError: local variable 'result' referenced before assignment",
            (
                "UnboundLocalError: cannot access local variable 'result' "
                "where it is not associated with a value"
            ),
        )

        for message in messages:
            with self.subTest(message=message):
                failure = CommandRun(
                    1,
                    False,
                    1.0,
                    "",
                    '  File "app.py", line 4, in compute\n' + message,
                    False,
                )
                self.assertTrue(
                    ProofRunner._baseline_matches_hypothesis(failure, hypothesis)
                )

    def test_prove_rejects_same_basename_from_a_different_directory(self) -> None:
        wrong_file = CommandRun(
            1,
            False,
            5.0,
            "",
            '  File "/workspace/other/app.py", line 4, in <module>\n'
            "NameError: name 'grete' is not defined\n",
            False,
        )
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "pkg").mkdir()
            (project / "pkg" / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            with patch.object(ProofRunner, "_run", return_value=wrong_file):
                with self.assertRaisesRegex(ValueError, "does not match analyzed error"):
                    ProofRunner().prove(
                        project,
                        undefined_name_hypothesis(location="pkg/app.py:4"),
                        timeout_seconds=5,
                    )

    def test_docker_v2_requires_an_image_pinned_by_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "pinned by sha256 digest"):
                ProofRunner().prove(
                    project,
                    undefined_name_hypothesis(),
                    backend="docker",
                    docker_image="python:3.12-slim",
                    timeout_seconds=5,
                )

    def test_copy_excludes_common_secret_files(self) -> None:
        secret_names = (
            "credentials.json",
            "secrets.yaml",
            "service-account.json",
            "private.pem",
            "signing.key",
            "id_rsa",
            ".env.production",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "copy"
            source.mkdir()
            (source / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")
            for name in secret_names:
                (source / name).write_text("never-copy-me", encoding="utf-8")

            ProofRunner._copy_project(source, destination)

            self.assertTrue((destination / "app.py").exists())
            for name in secret_names:
                with self.subTest(name=name):
                    self.assertFalse((destination / name).exists())

    def test_prove_accepts_pytest_as_a_test_program(self) -> None:
        module_text = """\
def greet(name):
    return f"Hi {name}"

def run():
    return grete("Ada")
"""
        test_text = """\
from app import run


def test_run():
    assert run() == "Hi Ada"
"""
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "app.py"
            source.write_text(module_text, encoding="utf-8")
            (project / "test_app.py").write_text(test_text, encoding="utf-8")

            result = ProofRunner().prove(
                project,
                undefined_name_hypothesis(location="app.py:5"),
                test_program="pytest",
                test_args=("-q", "test_app.py"),
                timeout_seconds=10,
            )

            self.assertEqual(result.verification.grade, "V1")
            self.assertEqual(result.command, ("pytest", "-q", "test_app.py"))
            self.assertEqual(source.read_text(encoding="utf-8"), module_text)

    def test_prove_rejects_programs_outside_the_python_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            for program in ("node", "powershell", "python.exe"):
                with self.subTest(program=program):
                    with self.assertRaisesRegex(ValueError, "unsupported test program"):
                        ProofRunner().prove(
                            project,
                            undefined_name_hypothesis(),
                            test_program=program,
                            timeout_seconds=5,
                        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
import os
import stat
from pathlib import Path
from unittest.mock import patch

from burhan.model import Hypothesis
from burhan.patcher import CommandRun, ProofConfigurationError, ProofRejected, ProofRunner


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
    def test_original_manifest_has_unambiguous_record_framing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            first = project / "a"
            second = project / "b"
            first.write_bytes(b"x")
            second.write_bytes(b"y")
            before = ProofRunner._project_manifest(project)

            second.unlink()
            first.write_bytes(b"x\0F\0b\0y")
            after = ProofRunner._project_manifest(project)

        self.assertNotEqual(before, after)

    def test_original_manifest_binds_windows_junction_target(self) -> None:
        class ReparseStat:
            st_mode = 0o40755

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def synthetic_walk(*args: object, **kwargs: object):
                del args, kwargs
                return iter(((root, (), ("junction",)),))

            with (
                patch("burhan.patcher.bounded_walk", side_effect=synthetic_walk),
                patch("burhan.patcher.is_reparse_path", return_value=True, create=True),
                patch.object(Path, "lstat", return_value=ReparseStat()),
                patch("burhan.patcher.os.readlink", return_value="target-one"),
            ):
                before = ProofRunner._project_manifest(root)
            with (
                patch("burhan.patcher.bounded_walk", side_effect=synthetic_walk),
                patch("burhan.patcher.is_reparse_path", return_value=True, create=True),
                patch.object(Path, "lstat", return_value=ReparseStat()),
                patch("burhan.patcher.os.readlink", return_value="target-two"),
            ):
                after = ProofRunner._project_manifest(root)

        self.assertNotEqual(before, after)

    def test_original_manifest_detects_permission_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "app.py"
            source.write_text(BROKEN_SCRIPT, encoding="utf-8")
            before = ProofRunner._project_manifest(project)
            os.chmod(source, stat.S_IREAD)
            after = ProofRunner._project_manifest(project)
            os.chmod(source, stat.S_IREAD | stat.S_IWRITE)

        self.assertNotEqual(before, after)

    def test_local_proof_refuses_secrets_it_cannot_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")
            (project / ".env").write_text("TOKEN=secret", encoding="utf-8")

            with self.assertRaisesRegex(ProofConfigurationError, "secret files"):
                ProofRunner().prove(
                    project,
                    undefined_name_hypothesis(),
                    timeout_seconds=5,
                )

    def test_original_manifest_does_not_open_secret_files(self) -> None:
        original_open = Path.open

        def guarded_open(path: Path, *args: object, **kwargs: object):
            if path.name == ".env":
                raise AssertionError("secret contents must not be read")
            return original_open(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".env").write_text("TOKEN=secret", encoding="utf-8")
            with patch.object(Path, "open", guarded_open):
                fingerprint = ProofRunner._project_manifest(project)

        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")

    def test_prove_rejects_target_outside_scanner_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            hidden = project / ".hidden"
            hidden.mkdir()
            (hidden / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            with self.assertRaisesRegex(ProofConfigurationError, "scan scope"):
                ProofRunner().prove(
                    project,
                    undefined_name_hypothesis(location=".hidden/app.py:4"),
                    timeout_seconds=5,
                )

    def test_prove_rejects_when_any_original_project_file_changes(self) -> None:
        failed = matching_failure()
        passed = CommandRun(0, False, 8.0, "Hi Ada\n", "", False)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")
            sentinel = project / "sentinel.txt"
            sentinel.write_text("original", encoding="utf-8")
            calls = 0

            def mutate_original(*args: object, **kwargs: object) -> CommandRun:
                nonlocal calls
                del args, kwargs
                calls += 1
                if calls == 1:
                    sentinel.write_text("changed", encoding="utf-8")
                    return failed
                return passed

            with patch.object(ProofRunner, "_run", side_effect=mutate_original):
                with self.assertRaisesRegex(ProofRejected, "original project changed"):
                    ProofRunner().prove(
                        project,
                        undefined_name_hypothesis(),
                        timeout_seconds=5,
                    )

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

            with self.assertRaisesRegex(ValueError, "pinned Docker image"):
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

    def test_copy_prunes_ignored_directories_before_counting_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "copy"
            (source / "src").mkdir(parents=True)
            (source / "src" / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")
            ignored = source / "node_modules" / "a" / "b" / "c"
            ignored.mkdir(parents=True)
            (ignored / "payload.js").write_text("ignored", encoding="utf-8")

            with patch.object(ProofRunner, "_MAX_DIRECTORIES", 2):
                ProofRunner._copy_project(source, destination)

            self.assertTrue((destination / "src" / "app.py").is_file())
            self.assertFalse((destination / "node_modules").exists())

    def test_copy_rejects_directory_depth_over_the_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "copy"
            deep = source / "one" / "two" / "three"
            deep.mkdir(parents=True)
            (deep / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            with patch.object(ProofRunner, "_MAX_DIRECTORY_DEPTH", 2):
                with self.assertRaisesRegex(ValueError, "directory depth"):
                    ProofRunner._copy_project(source, destination)

    def test_copy_rejects_too_many_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "copy"
            source.mkdir()
            for name in ("one", "two", "three"):
                (source / name).mkdir()

            with patch.object(ProofRunner, "_MAX_DIRECTORIES", 2):
                with self.assertRaisesRegex(ValueError, "directory count"):
                    ProofRunner._copy_project(source, destination)

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

    def test_command_rejects_excessive_argument_count_and_total_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "count or total-size"):
            ProofRunner._command("python", tuple("x" for _ in range(65)))
        with self.assertRaisesRegex(ValueError, "count or total-size"):
            ProofRunner._command("python", tuple("x" * 4_000 for _ in range(5)))

    def test_local_pytest_requires_an_installed_runtime(self) -> None:
        with patch("burhan.patcher.importlib.util.find_spec", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pytest is not installed"):
                ProofRunner().prove(
                    Path("."),
                    undefined_name_hypothesis(),
                    test_program="pytest",
                    backend="local",
                )

    def test_docker_image_cannot_inject_cli_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")

            with self.assertRaisesRegex(ProofConfigurationError, "pinned Docker image"):
                ProofRunner().prove(
                    project,
                    undefined_name_hypothesis(),
                    backend="docker",
                    docker_image="--label=unsafe@sha256:" + "a" * 64,
                    timeout_seconds=5,
                )

    def test_prove_rejects_project_changed_after_analysis_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "app.py").write_text(BROKEN_SCRIPT, encoding="utf-8")
            expected = ProofRunner.fingerprint_project(project, backend="local")
            (project / "fixture.txt").write_text("changed", encoding="utf-8")

            with self.assertRaisesRegex(ProofRejected, "analysis snapshot"):
                ProofRunner().prove(
                    project,
                    undefined_name_hypothesis(),
                    expected_project_fingerprint=expected,
                    timeout_seconds=5,
                )

    def test_copy_and_manifest_bound_all_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "copy"
            (root / "entry-one").write_text("one", encoding="utf-8")
            (root / "entry-two").write_text("two", encoding="utf-8")

            with (
                patch.object(ProofRunner, "_MAX_ENTRIES", 1, create=True),
                self.assertRaisesRegex(ProofConfigurationError, "entry count"),
            ):
                ProofRunner._copy_project(root, destination)

            with (
                patch.object(ProofRunner, "_MAX_MANIFEST_ENTRIES", 1, create=True),
                self.assertRaisesRegex(ProofConfigurationError, "entry count"),
            ):
                ProofRunner._project_manifest(root)

    def test_docker_reserved_exit_codes_are_infrastructure_failures(self) -> None:
        failed = CommandRun(
            exit_code=125,
            timed_out=False,
            duration_ms=1,
            stdout="",
            stderr="daemon failure",
            output_truncated=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("burhan.patcher.shutil.which", return_value="docker"):
                with patch.object(ProofRunner, "_run", return_value=failed):
                    with self.assertRaisesRegex(RuntimeError, "Docker could not start"):
                        ProofRunner._run_docker(
                            ("python", "app.py"),
                            root,
                            image=PINNED_PYTHON_IMAGE,
                            timeout_seconds=5,
                        )

    def test_docker_cleanup_timeout_is_an_infrastructure_failure(self) -> None:
        timed_out = CommandRun(
            exit_code=None,
            timed_out=True,
            duration_ms=5,
            stdout="",
            stderr="",
            output_truncated=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("burhan.patcher.shutil.which", return_value="docker"):
                with patch.object(ProofRunner, "_run", return_value=timed_out):
                    with patch(
                        "burhan.patcher.subprocess.run",
                        side_effect=TimeoutError("SECRET_CLEANUP"),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                            ProofRunner._run_docker(
                                ("python", "app.py"),
                                root,
                                image=PINNED_PYTHON_IMAGE,
                                timeout_seconds=5,
                            )


if __name__ == "__main__":
    unittest.main()

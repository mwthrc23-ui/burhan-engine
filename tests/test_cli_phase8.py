"""Tests for Phase 8: CLI improvements (burhan doctor, --explain)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run CLI and return (exit_code, stdout, stderr)."""
    from burhan.cli import main
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


# ---------------------------------------------------------------------------
# burhan doctor command
# ---------------------------------------------------------------------------

class DoctorCommandTests(unittest.TestCase):
    def test_doctor_returns_0_or_1(self) -> None:
        code, _, _ = _run(["doctor"])
        self.assertIn(code, (0, 1))

    def test_doctor_json_output_is_valid_json(self) -> None:
        code, stdout, _ = _run(["doctor", "--json"])
        payload = json.loads(stdout)
        self.assertIn("status", payload)
        self.assertIn("checks", payload)

    def test_doctor_json_has_burhan_version(self) -> None:
        _, stdout, _ = _run(["doctor", "--json"])
        payload = json.loads(stdout)
        self.assertIn("burhan_version", payload["checks"])

    def test_doctor_json_has_python_version(self) -> None:
        _, stdout, _ = _run(["doctor", "--json"])
        payload = json.loads(stdout)
        self.assertIn("python_version", payload["checks"])

    def test_doctor_json_has_docker_available(self) -> None:
        _, stdout, _ = _run(["doctor", "--json"])
        payload = json.loads(stdout)
        self.assertIn("docker_available", payload["checks"])

    def test_doctor_json_has_image_pinned_check(self) -> None:
        _, stdout, _ = _run(["doctor", "--json"])
        payload = json.loads(stdout)
        self.assertIn("default_image_pinned", payload["checks"])

    def test_doctor_text_output_contains_version(self) -> None:
        _, stdout, _ = _run(["doctor"])
        self.assertIn("بُرهان", stdout)

    def test_doctor_text_output_contains_python(self) -> None:
        _, stdout, _ = _run(["doctor"])
        self.assertIn("Python", stdout)


# ---------------------------------------------------------------------------
# --explain flag
# ---------------------------------------------------------------------------

class ExplainFlagTests(unittest.TestCase):
    def _run_analyze_explain(self, tmp: str) -> tuple[int, str]:
        root = Path(tmp)
        (root / "app.py").write_text("def greet(): pass\n", encoding="utf-8")
        err_file = root / "err.txt"
        err_file.write_text("NameError: name 'foo' is not defined", encoding="utf-8")
        code, stdout, _ = _run([
            "analyze",
            "--project", str(root),
            "--goal", "شخّص الخطأ",
            "--error-file", str(err_file),
            "--explain",
        ])
        return code, stdout

    def test_explain_shows_structured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout = self._run_analyze_explain(tmp)
            self.assertEqual(code, 0)
            self.assertIn("شرح التشخيص", stdout)

    def test_explain_shows_what_happened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, stdout = self._run_analyze_explain(tmp)
            self.assertIn("ماذا حدث", stdout)

    def test_explain_shows_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, stdout = self._run_analyze_explain(tmp)
            self.assertIn("الأدلة", stdout)

    def test_explain_shows_residual_risks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, stdout = self._run_analyze_explain(tmp)
            self.assertIn("المخاطر المتبقية", stdout)

    def test_no_explain_flag_no_structured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def greet(): pass\n", encoding="utf-8")
            err_file = root / "err.txt"
            err_file.write_text("NameError: name 'foo' is not defined", encoding="utf-8")
            _, stdout, _ = _run([
                "analyze",
                "--project", str(root),
                "--goal", "شخّص",
                "--error-file", str(err_file),
            ])
            self.assertNotIn("شرح التشخيص", stdout)

    def test_explain_and_json_together_json_takes_precedence(self) -> None:
        """When --json is used, JSON output should appear (--explain is text-mode)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def greet(): pass\n", encoding="utf-8")
            err_file = root / "err.txt"
            err_file.write_text("NameError: name 'foo' is not defined", encoding="utf-8")
            code, stdout, _ = _run([
                "analyze",
                "--project", str(root),
                "--goal", "شخّص",
                "--error-file", str(err_file),
                "--json",
                "--explain",
            ])
            self.assertEqual(code, 0)
            # JSON mode: output should be valid JSON
            payload = json.loads(stdout)
            self.assertIn("hypotheses", payload)


# ---------------------------------------------------------------------------
# Existing CLI commands still work (backward compatibility)
# ---------------------------------------------------------------------------

class BackwardCompatibilityTests(unittest.TestCase):
    def test_analyze_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            err = root / "e.txt"
            err.write_text("NameError: name 'foo' is not defined", encoding="utf-8")
            code, _, _ = _run(["analyze", "--project", str(root), "--goal", "test", "--error-file", str(err)])
            self.assertEqual(code, 0)

    def test_analyze_json_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            err = root / "e.txt"
            err.write_text("NameError: name 'foo' is not defined", encoding="utf-8")
            code, stdout, _ = _run([
                "analyze", "--project", str(root), "--goal", "test",
                "--error-file", str(err), "--json",
            ])
            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertIn("hypotheses", payload)

    def test_version_flag(self) -> None:
        _, stdout, stderr = _run(["--version"])
        combined = stdout + stderr
        self.assertIn("0.7.1", combined)

    def test_memory_promote_still_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = _run([
                "memory-promote",
                "--database", str(Path(tmp) / "mem.db"),
                "--episode", str(Path(tmp) / "ep.json"),
                "--proof", str(Path(tmp) / "proof.json"),
                "--human-review-note", "test",
            ])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

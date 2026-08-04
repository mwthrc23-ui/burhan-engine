from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from burhan.analyzer import BurhanAnalyzer
from burhan.model import Hypothesis
from burhan.patcher import CommandRun, PatchEngine, ProofRunner


def hypothesis(
    kind: str,
    target: str,
    location: str | None,
    replacement: str | None,
) -> Hypothesis:
    return Hypothesis(
        kind=kind,
        target=target,
        explanation="conservative typo repair",
        location=location,
        energy=0.1,
        confidence=0.9,
        suggested_replacement=replacement,
    )


class AnalyzerRepairCandidateTests(unittest.TestCase):
    def test_attribute_error_gets_a_unique_snapshot_replacement(self) -> None:
        source = """\
class ApiClient:
    def send_message(self, payload):
        return payload

def deliver(api, payload):
    return api.send_mesage(payload)
"""
        error = """\
Traceback (most recent call last):
  File "client.py", line 6, in deliver
    return api.send_mesage(payload)
AttributeError: 'ApiClient' object has no attribute 'send_mesage'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "client.py").write_text(source, encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "أصلح خطأ الخاصية", error)

        self.assertEqual(result.primary.kind, "attribute_error")
        self.assertEqual(result.primary.suggested_replacement, "send_message")

    def test_import_name_gets_a_replacement_only_from_the_local_module(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 1, in <module>
    from helpers import format_nam
ImportError: cannot import name 'format_nam' from 'helpers'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "helpers.py").write_text(
                "def format_name(value):\n    return value\n", encoding="utf-8"
            )
            (root / "other.py").write_text(
                "def format_names(value):\n    return value\n", encoding="utf-8"
            )
            (root / "app.py").write_text(
                "from helpers import format_nam\n", encoding="utf-8"
            )

            result = BurhanAnalyzer().analyze(root, "أصلح اسم الاستيراد", error)

        self.assertEqual(result.primary.kind, "missing_import_name")
        self.assertEqual(result.primary.suggested_replacement, "format_name")

    def test_missing_module_gets_a_unique_local_module_replacement(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 1, in <module>
    import helpres
ModuleNotFoundError: No module named 'helpres'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "app.py").write_text("import helpres\n", encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "أصلح اسم الوحدة", error)

        self.assertEqual(result.primary.kind, "missing_module")
        self.assertEqual(result.primary.suggested_replacement, "helpers")

    def test_src_layout_module_candidates_use_the_importable_name(self) -> None:
        error = """\
Traceback (most recent call last):
  File "src/app.py", line 1, in <module>
    import helpres
ModuleNotFoundError: No module named 'helpres'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (source / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "app.py").write_text("import helpres\n", encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "أصلح اسم الوحدة", error)

        self.assertEqual(result.primary.suggested_replacement, "helpers")

    def test_src_layout_import_name_uses_the_local_module_exports(self) -> None:
        error = """\
Traceback (most recent call last):
  File "src/app.py", line 1, in <module>
    from helpers import format_nam
ImportError: cannot import name 'format_nam' from 'helpers'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (source / "helpers.py").write_text(
                "def format_name(value):\n    return value\n", encoding="utf-8"
            )
            (source / "app.py").write_text(
                "from helpers import format_nam\n", encoding="utf-8"
            )

            result = BurhanAnalyzer().analyze(root, "أصلح اسم الاستيراد", error)

        self.assertEqual(result.primary.suggested_replacement, "format_name")

    def test_nested_symbol_is_not_treated_as_an_importable_module_name(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 1, in <module>
    from helpers import format_nam
ImportError: cannot import name 'format_nam' from 'helpers'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "helpers.py").write_text(
                "def factory():\n"
                "    def format_name(value):\n"
                "        return value\n"
                "    return format_name\n",
                encoding="utf-8",
            )
            (root / "app.py").write_text(
                "from helpers import format_nam\n", encoding="utf-8"
            )

            result = BurhanAnalyzer().analyze(root, "شخّص الاستيراد", error)

        self.assertIsNone(result.primary.suggested_replacement)

    def test_key_error_gets_a_unique_literal_key_replacement(self) -> None:
        source = """\
data = {"user_name": "Ada"}
value = data["user_nam"]
"""
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in <module>
    value = data["user_nam"]
KeyError: 'user_nam'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(source, encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "أصلح مفتاح القاموس", error)

        self.assertEqual(result.primary.kind, "missing_key")
        self.assertEqual(result.primary.suggested_replacement, "user_name")

    def test_ambiguous_snapshot_candidates_do_not_produce_a_replacement(self) -> None:
        source = """\
class ApiClient:
    def send_message(self): pass
    def send_messages(self): pass

def deliver(api):
    return api.send_mesage()
"""
        error = """\
Traceback (most recent call last):
  File "client.py", line 6, in deliver
    return api.send_mesage()
AttributeError: 'ApiClient' object has no attribute 'send_mesage'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "client.py").write_text(source, encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "شخّص فقط", error)

        self.assertIsNone(result.primary.suggested_replacement)

    def test_javascript_reference_error_gets_a_unique_identifier_replacement(self) -> None:
        error = """\
ReferenceError: grete is not defined
    at app.js:2:15
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.js").write_text(
                "function greet(name) { return `Hi ${name}`; }\n"
                "const value = grete('Ada');\n",
                encoding="utf-8",
            )

            result = BurhanAnalyzer().analyze(root, "أصلح خطأ JavaScript", error)

        self.assertEqual(result.primary.kind, "undefined_name")
        self.assertEqual(result.primary.location, "app.js:2:15")
        self.assertEqual(result.primary.suggested_replacement, "greet")

    def test_javascript_reference_error_uses_the_first_throwing_stack_frame(self) -> None:
        error = """\
ReferenceError: grete is not defined
    at app.js:2:15
    at run (runner.js:9:3)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.js").write_text(
                "function greet(name) { return name; }\n"
                "const value = grete('Ada');\n",
                encoding="utf-8",
            )

            result = BurhanAnalyzer().analyze(root, "شخّص JavaScript", error)

        self.assertEqual(result.primary.location, "app.js:2:15")


class ConservativePatchEngineTests(unittest.TestCase):
    def test_repairs_an_attribute_name_only_in_attribute_context(self) -> None:
        source = "obj.send_mesage(payload)\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "client.py"
            path.write_text(source, encoding="utf-8")

            result = PatchEngine().repair(
                root,
                hypothesis("attribute_error", "send_mesage", "client.py:1", "send_message"),
                apply=True,
            )

            self.assertTrue(result.applied)
            self.assertEqual(path.read_text(encoding="utf-8"), "obj.send_message(payload)\n")

    def test_repairs_an_imported_name_on_the_reported_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "app.py"
            path.write_text("from helpers import format_nam\n", encoding="utf-8")

            PatchEngine().repair(
                root,
                hypothesis(
                    "missing_import_name",
                    "format_nam",
                    "app.py:1",
                    "format_name",
                ),
                apply=True,
            )

            self.assertEqual(
                path.read_text(encoding="utf-8"), "from helpers import format_name\n"
            )

    def test_repairs_an_imported_local_module_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
            path = root / "app.py"
            path.write_text("import helpres\n", encoding="utf-8")

            PatchEngine().repair(
                root,
                hypothesis("missing_module", "helpres", "app.py:1", "helpers"),
                apply=True,
            )

            self.assertEqual(path.read_text(encoding="utf-8"), "import helpers\n")

    def test_repairs_only_a_literal_key_subscript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "app.py"
            path.write_text('value = data["user_nam"]\n', encoding="utf-8")

            PatchEngine().repair(
                root,
                hypothesis("missing_key", "user_nam", "app.py:1", "user_name"),
                apply=True,
            )

            self.assertEqual(path.read_text(encoding="utf-8"), 'value = data["user_name"]\n')

    def test_repairs_a_typescript_identifier_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "app.ts"
            path.write_text("const answer = grete('Ada');\n", encoding="utf-8")

            result = PatchEngine().repair(
                root,
                hypothesis("undefined_name", "grete", "app.ts:1:16", "greet"),
                apply=True,
            )

            self.assertEqual(path.read_text(encoding="utf-8"), "const answer = greet('Ada');\n")
            self.assertIn("javascript_identifier_token", result.verification.checks)

    def test_refuses_a_kind_when_the_target_is_not_in_its_required_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("send_mesage(payload)\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "required Python syntax context"):
                PatchEngine().repair(
                    root,
                    hypothesis(
                        "attribute_error", "send_mesage", "app.py:1", "send_message"
                    ),
                )

    def test_refuses_a_nonliteral_key_subscript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("value = data[user_nam]\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "required Python syntax context"):
                PatchEngine().repair(
                    root,
                    hypothesis("missing_key", "user_nam", "app.py:1", "user_name"),
                )

    def test_refuses_a_nonlocal_module_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("import reqeusts\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "local module"):
                PatchEngine().repair(
                    root,
                    hypothesis("missing_module", "reqeusts", "app.py:1", "requests"),
                )

    def test_refuses_missing_location_or_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("print(bad)\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "known replacement"):
                PatchEngine().repair(
                    root, hypothesis("undefined_name", "bad", "app.py:1", None)
                )
            with self.assertRaisesRegex(ValueError, "no source location"):
                PatchEngine().repair(
                    root, hypothesis("undefined_name", "bad", None, "good")
                )

    def test_refuses_multiline_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("print(bad)\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "single-line"):
                PatchEngine().repair(
                    root,
                    hypothesis("undefined_name", "bad", "app.py:1", "good\nprint('x')"),
                )

    def test_refuses_a_key_replacement_that_changes_python_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                'value = data["user_nam"]\n', encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "literal key"):
                PatchEngine().repair(
                    root,
                    hypothesis(
                        "missing_key",
                        "user_nam",
                        "app.py:1",
                        'user_name"] or dangerous() or data["x',
                    ),
                )

    def test_refuses_nonidentifier_replacements_for_python_name_repairs(self) -> None:
        cases = (
            (
                "undefined_name",
                "bad",
                "good + dangerous()",
                "print(bad)\n",
            ),
            (
                "attribute_error",
                "bad",
                "good()",
                "obj.bad()\n",
            ),
            (
                "missing_import_name",
                "bad",
                "good as alias",
                "from helpers import bad\n",
            ),
        )
        for kind, target, replacement, source in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "app.py").write_text(source, encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "identifier"):
                    PatchEngine().repair(
                        root,
                        hypothesis(kind, target, "app.py:1", replacement),
                    )

    def test_relative_module_repair_refuses_a_module_outside_its_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "app.py").write_text(
                "from .helpres import VALUE\n", encoding="utf-8"
            )
            (root / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "local module"):
                PatchEngine().repair(
                    root,
                    hypothesis(
                        "missing_module", "helpres", "pkg/app.py:1", "helpers"
                    ),
                )

    def test_relative_module_repair_resolves_inside_its_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
            path = package / "app.py"
            path.write_text("from .helpres import VALUE\n", encoding="utf-8")

            PatchEngine().repair(
                root,
                hypothesis("missing_module", "helpres", "pkg/app.py:1", "helpers"),
                apply=True,
            )

            self.assertEqual(
                path.read_text(encoding="utf-8"), "from .helpers import VALUE\n"
            )

    def test_refuses_a_typescript_string_match_instead_of_an_identifier_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.ts").write_text("const text = 'grete';\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "identifier token"):
                PatchEngine().repair(
                    root,
                    hypothesis("undefined_name", "grete", "app.ts:1:15", "greet"),
                )

    def test_refuses_a_typescript_regex_match_instead_of_an_identifier_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.ts").write_text("const pattern = /grete/;\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "identifier token"):
                PatchEngine().repair(
                    root,
                    hypothesis("undefined_name", "grete", "app.ts:1:18", "greet"),
                )

    def test_refuses_an_identifier_inside_a_multiline_javascript_comment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.js").write_text(
                "/* comment starts\nundefined grete here\n*/\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "identifier token"):
                PatchEngine().repair(
                    root,
                    hypothesis("undefined_name", "grete", "app.js:2:11", "greet"),
                )

    def test_refuses_a_reparse_source_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("print(bad)\n", encoding="utf-8")

            with (
                patch("burhan.patcher.is_reparse_path", return_value=True),
                self.assertRaisesRegex(ValueError, "link or reparse"),
            ):
                PatchEngine().repair(
                    root,
                    hypothesis("undefined_name", "bad", "app.py:1", "good"),
                )


class ConservativeProofRunnerTests(unittest.TestCase):
    def test_javascript_baseline_rejects_a_location_from_a_later_stack_frame(self) -> None:
        failure = CommandRun(
            exit_code=1,
            timed_out=False,
            duration_ms=1.0,
            stdout="",
            stderr=(
                "ReferenceError: grete is not defined\n"
                "    at other.js:3:7\n"
                "    at app.js:1:16\n"
            ),
            output_truncated=False,
        )

        self.assertFalse(
            ProofRunner._baseline_matches_hypothesis(
                failure,
                hypothesis("undefined_name", "grete", "app.js:1:16", "greet"),
            )
        )

    def test_python_repairs_require_matching_failure_then_pass(self) -> None:
        cases = (
            (
                "attribute_error",
                "send_mesage",
                "send_message",
                "app.py:4",
                {
                    "app.py": (
                        "class ApiClient:\n"
                        "    def send_message(self, value): return value\n"
                        "api = ApiClient()\n"
                        "print(api.send_mesage('Ada'))\n"
                    )
                },
            ),
            (
                "missing_import_name",
                "format_nam",
                "format_name",
                "app.py:1",
                {
                    "helpers.py": "def format_name(value): return value\n",
                    "app.py": "from helpers import format_nam\n",
                },
            ),
            (
                "missing_module",
                "helpres",
                "helpers",
                "app.py:1",
                {
                    "helpers.py": "VALUE = 1\n",
                    "app.py": "import helpres\n",
                },
            ),
            (
                "missing_key",
                "user_nam",
                "user_name",
                "app.py:2",
                {
                    "app.py": (
                        'data = {"user_name": "Ada"}\n'
                        'print(data["user_nam"])\n'
                    )
                },
            ),
        )
        for kind, target, replacement, location, files in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for relative_path, content in files.items():
                    (root / relative_path).write_text(content, encoding="utf-8")

                result = ProofRunner().prove(
                    root,
                    hypothesis(kind, target, location, replacement),
                    test_program="python",
                    test_args=("app.py",),
                )

                self.assertTrue(result.verified)
                self.assertTrue(result.original_unchanged)

    def test_typescript_repair_can_be_proved_by_a_matching_diagnostic(self) -> None:
        checker = """\
from pathlib import Path
import sys

if "grete" in Path("app.ts").read_text(encoding="utf-8"):
    print("app.ts(1,16): error TS2304: Cannot find name 'grete'.", file=sys.stderr)
    raise SystemExit(1)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.ts").write_text(
                "const answer = grete('Ada');\n", encoding="utf-8"
            )
            (root / "check.py").write_text(checker, encoding="utf-8")

            result = ProofRunner().prove(
                root,
                hypothesis("undefined_name", "grete", "app.ts:1:16", "greet"),
                test_program="python",
                test_args=("check.py",),
            )

        self.assertTrue(result.verified)
        self.assertTrue(result.original_unchanged)

    def test_javascript_repair_can_be_proved_by_a_matching_reference_error(self) -> None:
        checker = """\
from pathlib import Path
import sys

if "grete" in Path("app.js").read_text(encoding="utf-8"):
    print("ReferenceError: grete is not defined", file=sys.stderr)
    print("    at app.js:1:16", file=sys.stderr)
    raise SystemExit(1)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.js").write_text(
                "const answer = grete('Ada');\n", encoding="utf-8"
            )
            (root / "check.py").write_text(checker, encoding="utf-8")

            result = ProofRunner().prove(
                root,
                hypothesis("undefined_name", "grete", "app.js:1:16", "greet"),
                test_program="python",
                test_args=("check.py",),
            )

        self.assertTrue(result.verified)
        self.assertTrue(result.original_unchanged)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from burhan.analyzer import BurhanAnalyzer
from burhan.model import Hypothesis
from burhan.patcher import PatchEngine


ERROR = """\
Traceback (most recent call last):
  File "app.py", line 5, in run
    return grete("Ada")
NameError: name 'grete' is not defined
"""

SOURCE = """\
def greet(name):
    return f"Hi {name}"

def run():
    return grete("Ada")
"""


class PatchEngineTests(unittest.TestCase):
    def test_preview_creates_a_v0_unified_diff_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "app.py"
            source_path.write_text(SOURCE, encoding="utf-8")
            analysis = BurhanAnalyzer().analyze(root, "أصلح الخطأ بأقل تعديل", ERROR)

            result = PatchEngine().repair(root, analysis.primary)

            self.assertFalse(result.applied)
            self.assertEqual(result.verification.grade, "V0")
            self.assertEqual(result.changed_files, ("app.py",))
            self.assertIn('-    return grete("Ada")', result.diff)
            self.assertIn('+    return greet("Ada")', result.diff)
            self.assertEqual(source_path.read_text(encoding="utf-8"), SOURCE)

    def test_apply_writes_only_the_reported_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "app.py"
            source_path.write_text(SOURCE, encoding="utf-8")
            analysis = BurhanAnalyzer().analyze(root, "أصلح الخطأ بأقل تعديل", ERROR)

            result = PatchEngine().repair(root, analysis.primary, apply=True)

            updated = source_path.read_text(encoding="utf-8")
            self.assertTrue(result.applied)
            self.assertIn('return greet("Ada")', updated)
            self.assertNotIn('return grete("Ada")', updated)
            self.assertIn("def greet(name):", updated)

    def test_repair_refuses_a_path_outside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hypothesis = Hypothesis(
                kind="undefined_name",
                target="bad",
                explanation="unsafe",
                location="../outside.py:1",
                energy=1.0,
                confidence=0.9,
                suggested_replacement="good",
            )

            with self.assertRaisesRegex(ValueError, "outside project"):
                PatchEngine().repair(root, hypothesis)

    def test_repair_refuses_ambiguous_multiple_occurrences_on_the_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("value = bad + bad\n", encoding="utf-8")
            hypothesis = Hypothesis(
                kind="undefined_name",
                target="bad",
                explanation="ambiguous",
                location="app.py:1",
                energy=1.0,
                confidence=0.9,
                suggested_replacement="good",
            )

            with self.assertRaisesRegex(ValueError, "exactly once"):
                PatchEngine().repair(root, hypothesis)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from burhan.analyzer import BurhanAnalyzer
from burhan.model import NodeKind


class BurhanAnalyzerTests(unittest.TestCase):
    def test_python_name_error_links_trace_to_symbol_and_suggests_close_match(self) -> None:
        source = """\
def greet(name):
    return f"Hi {name}"

def run():
    return grete("Ada")
"""
        error = """\
Traceback (most recent call last):
  File "app.py", line 5, in run
    return grete("Ada")
NameError: name 'grete' is not defined
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(source, encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ بأقل تعديل", error)

        self.assertLess(result.elapsed_ms, 1000)
        self.assertEqual(result.primary.kind, "undefined_name")
        self.assertEqual(result.primary.target, "grete")
        self.assertEqual(result.primary.suggested_replacement, "greet")
        self.assertEqual(result.primary.location, "app.py:5")
        self.assertTrue(any("NameError" in evidence.summary for evidence in result.primary.evidence))
        self.assertTrue(any(node.label == "greet" for node in result.state.nodes))
        self.assertTrue(result.case_id.startswith("case-"))
        self.assertEqual(result.provenance.engine_version, "0.5.0")
        self.assertTrue(result.provenance.input_fingerprint.startswith("sha256:"))
        self.assertTrue(result.residual_risks)

    def test_goal_constraint_is_kept_as_an_explicit_bir_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")

            result = BurhanAnalyzer().analyze(
                root,
                "أصلح الخطأ ولا تغيّر الواجهة العامة",
                "something unusual happened",
            )

        constraints = [node for node in result.state.nodes if node.kind is NodeKind.CONSTRAINT]
        self.assertEqual(len(constraints), 1)
        self.assertIn("لا تغيّر الواجهة العامة", constraints[0].label)

    def test_typescript_diagnostic_becomes_a_type_mismatch_hypothesis(self) -> None:
        error = "src/index.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (source / "index.ts").write_text(
                "let answer: number;\nanswer = 42;\nanswer = 'forty-two';\n",
                encoding="utf-8",
            )

            result = BurhanAnalyzer().analyze(root, "أصلح تعارض النوع", error)

        self.assertEqual(result.primary.kind, "type_mismatch")
        self.assertEqual(result.primary.location, "src/index.ts:3:7")
        self.assertIn("TS2322", result.primary.explanation)
        self.assertGreaterEqual(result.confidence, 0.5)

    def test_typescript_unknown_name_suggests_closest_symbol(self) -> None:
        error = "src/index.ts(3,7): error TS2304: Cannot find name 'grete'."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (source / "index.ts").write_text(
                "function greet(name: string) { return `Hi ${name}`; }\n"
                "const value = grete('Ada');\n",
                encoding="utf-8",
            )

            result = BurhanAnalyzer().analyze(root, "شخّص خطأ TypeScript", error)

        self.assertEqual(result.primary.kind, "undefined_name")
        self.assertEqual(result.primary.target, "grete")
        self.assertEqual(result.primary.suggested_replacement, "greet")
        self.assertEqual(result.primary.location, "src/index.ts:3:7")

    def test_typescript_missing_property_maps_to_missing_property_hypothesis(self) -> None:
        error = (
            "src/client.ts(8,15): error TS2339: "
            "Property 'send' does not exist on type 'ApiClient'."
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            source.mkdir()
            (source / "client.ts").write_text(
                "class ApiClient { sendMessage(payload: string) { return payload; } }\n",
                encoding="utf-8",
            )

            result = BurhanAnalyzer().analyze(root, "شخّص خطأ TypeScript", error)

        self.assertEqual(result.primary.kind, "missing_property")
        self.assertEqual(result.primary.target, "send")
        self.assertEqual(result.primary.location, "src/client.ts:8:15")
        self.assertIn("ApiClient", result.primary.explanation)
        self.assertTrue(result.questions)

    def test_python_attribute_error_extracts_the_missing_attribute(self) -> None:
        error = """\
Traceback (most recent call last):
  File "client.py", line 8, in deliver
    api.send(payload)
AttributeError: 'ApiClient' object has no attribute 'send'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "client.py").write_text(
                "def deliver(api, payload):\n    return api.send(payload)\n",
                encoding="utf-8",
            )

            result = BurhanAnalyzer().analyze(root, "شخّص عطل API", error)

        self.assertEqual(result.primary.kind, "attribute_error")
        self.assertEqual(result.primary.target, "send")
        self.assertEqual(result.primary.location, "client.py:8")
        self.assertIn("ApiClient", result.primary.explanation)

    def test_unknown_error_returns_an_evidence_gap_instead_of_inventing_a_cause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "افهم المشكلة", "something unusual happened")

        self.assertEqual(result.primary.kind, "insufficient_evidence")
        self.assertLess(result.confidence, 0.5)
        self.assertTrue(result.questions)

    def test_analysis_result_includes_code_tree(self) -> None:
        source = """\
class Processor:
    def run(self): pass
    def stop(self): pass

def main(): pass
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "proc.py").write_text(source, encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "افهم المشكلة", "something unusual happened")

        self.assertIsNotNone(result.code_tree)
        assert result.code_tree is not None
        # Root is a directory
        self.assertEqual(result.code_tree.kind, "directory")
        # proc.py is a child of root
        file_names = {node.name for node in result.code_tree.children}
        self.assertIn("proc.py", file_names)
        file_node = next(n for n in result.code_tree.children if n.name == "proc.py")
        top_names = {n.name for n in file_node.children}
        self.assertIn("Processor", top_names)
        self.assertIn("main", top_names)
        # Methods are nested inside the class, not at file level
        self.assertNotIn("run", top_names)
        class_node = next(n for n in file_node.children if n.name == "Processor")
        method_names = {m.name for m in class_node.children}
        self.assertIn("run", method_names)
        self.assertIn("stop", method_names)

    def test_analysis_result_to_dict_includes_code_tree(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def run(): pass\n", encoding="utf-8")

            result = BurhanAnalyzer().analyze(root, "افهم المشكلة", "something unusual happened")

        payload = json.dumps(result.to_dict(), ensure_ascii=False)
        self.assertIn("code_tree", payload)
        self.assertIn("app.py", payload)


if __name__ == "__main__":
    unittest.main()

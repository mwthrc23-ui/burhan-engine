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
        self.assertEqual(result.provenance.engine_version, "0.6.1")
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

    # ------------------------------------------------------------------
    # New error types
    # ------------------------------------------------------------------

    def test_unbound_local_error_diagnosed_with_close_match(self) -> None:
        source = """\
def compute(x):
    if x > 0:
        result = x * 2
    return reslt
"""
        error = """\
Traceback (most recent call last):
  File "calc.py", line 4, in compute
    return reslt
UnboundLocalError: local variable 'reslt' referenced before assignment
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "calc.py").write_text(source, encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "unbound_local_variable")
        self.assertEqual(result.primary.target, "reslt")
        self.assertIsNotNone(result.primary.suggested_replacement)
        self.assertEqual(result.primary.location, "calc.py:4")
        self.assertTrue(result.questions)

    def test_unbound_local_error_python312_message_diagnosed(self) -> None:
        source = """\
def compute(x):
    if x > 0:
        result = x * 2
    return result
"""
        error = """\
Traceback (most recent call last):
  File "calc.py", line 4, in compute
    return result
UnboundLocalError: cannot access local variable 'result' where it is not associated with a value
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "calc.py").write_text(source, encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "unbound_local_variable")
        self.assertEqual(result.primary.target, "result")
        self.assertEqual(result.primary.location, "calc.py:4")
        self.assertTrue(result.questions)

    def test_import_error_cannot_import_name_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 1, in <module>
    from os.path import nonexistent_function
ImportError: cannot import name 'nonexistent_function' from 'os.path'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                "from os.path import nonexistent_function\n", encoding="utf-8"
            )
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "missing_import_name")
        self.assertEqual(result.primary.target, "nonexistent_function")
        self.assertIn("os.path", result.primary.explanation)
        self.assertTrue(result.questions)

    def test_type_error_wrong_arg_count_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 3, in <module>
    greet("Alice", "extra")
TypeError: greet() takes 1 positional argument but 2 were given
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def greet(name): pass\ngreet('Alice', 'extra')\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "wrong_argument_count")
        self.assertEqual(result.primary.target, "greet")
        self.assertIn("greet", result.primary.explanation)
        self.assertIn("1", result.primary.explanation)
        self.assertTrue(result.questions)

    def test_type_error_not_callable_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in <module>
    result = count(10)
TypeError: 'int' object is not callable
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("count = 5\nresult = count(10)\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "not_callable")
        self.assertIn("int", result.primary.explanation)
        self.assertTrue(result.questions)

    def test_type_error_bad_operand_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in <module>
    result = "hello" + 5
TypeError: unsupported operand type(s) for +: 'str' and 'int'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text('result = "hello" + 5\n', encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "unsupported_operand")
        self.assertIn("str", result.primary.explanation)
        self.assertIn("int", result.primary.explanation)
        self.assertTrue(result.questions)

    def test_generic_type_error_diagnosed(self) -> None:
        error = "TypeError: must be str, not int"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "type_error")
        self.assertIn("str", result.primary.explanation)

    def test_value_error_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in <module>
    x = int("hello")
ValueError: invalid literal for int() with base 10: 'hello'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("x = int('hello')\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "value_error")
        self.assertIn("hello", result.primary.explanation)
        self.assertTrue(result.questions)

    def test_index_error_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in <module>
    x = items[10]
IndexError: list index out of range
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("items = [1, 2]\nx = items[10]\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "index_out_of_range")
        self.assertTrue(result.questions)

    def test_key_error_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in <module>
    val = data['missing_key']
KeyError: 'missing_key'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("data = {}\nval = data['missing_key']\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "missing_key")
        self.assertIn("missing_key", result.primary.target)
        self.assertTrue(result.questions)

    def test_zero_division_error_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in <module>
    result = 10 / 0
ZeroDivisionError: division by zero
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("result = 10 / 0\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "zero_division")
        self.assertTrue(result.residual_risks)

    def test_recursion_error_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in recurse
    return recurse(n - 1)
  ...
RecursionError: maximum recursion depth exceeded
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def recurse(n):\n    return recurse(n - 1)\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "infinite_recursion")
        self.assertTrue(result.questions)

    def test_file_not_found_error_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in load
    open('/data/config.json')
FileNotFoundError: [Errno 2] No such file or directory: '/data/config.json'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("open('/data/config.json')\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "file_not_found")
        self.assertIn("/data/config.json", result.primary.target)
        self.assertTrue(result.questions)

    def test_file_not_found_error_windows_format_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 2, in load
    open('C:\\data\\config.json')
FileNotFoundError: [WinError 2] The system cannot find the file specified: 'C:\\data\\config.json'
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("open('C:\\\\data\\\\config.json')\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "file_not_found")
        self.assertIn("config.json", result.primary.target)
        self.assertTrue(result.questions)

    def test_os_error_without_path_diagnosed(self) -> None:
        error = """\
Traceback (most recent call last):
  File "app.py", line 3, in connect
    sock.connect(('localhost', 80))
OSError: [Errno 111] Connection refused
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("import socket\nsock = socket.socket()\nsock.connect(('localhost', 80))\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(result.primary.kind, "os_error")
        self.assertIn("111", result.primary.explanation)
        self.assertTrue(result.questions)

    def test_typescript_arg_type_mismatch_ts2345(self) -> None:
        error = (
            "src/app.ts(5,10): error TS2345: "
            "Argument of type 'string' is not assignable to parameter of type 'number'."
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (src / "app.ts").write_text("function add(a: number) { return a + 1; }\nadd('five');\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح خطأ TypeScript", error)

        self.assertEqual(result.primary.kind, "argument_type_mismatch")
        self.assertIn("string", result.primary.explanation)
        self.assertIn("number", result.primary.explanation)
        self.assertEqual(result.primary.location, "src/app.ts:5:10")
        self.assertTrue(result.questions)

    def test_typescript_wrong_arg_count_ts2554(self) -> None:
        error = (
            "src/app.ts(3,1): error TS2554: Expected 2 arguments, but got 3."
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (src / "app.ts").write_text("function add(a: number, b: number) { return a + b; }\nadd(1,2,3);\n", encoding="utf-8")
            result = BurhanAnalyzer().analyze(root, "أصلح خطأ TypeScript", error)

        self.assertEqual(result.primary.kind, "wrong_argument_count")
        self.assertIn("2", result.primary.explanation)
        self.assertIn("3", result.primary.explanation)
        self.assertEqual(result.primary.location, "src/app.ts:3:1")
        self.assertTrue(result.questions)

    def test_unbound_local_variable_repair_renames_like_name_error(self) -> None:
        source = """\
def compute(x):
    if x > 0:
        result = x * 2
    return reslt
"""
        error = """\
Traceback (most recent call last):
  File "calc.py", line 4, in compute
    return reslt
UnboundLocalError: local variable 'reslt' referenced before assignment
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "calc.py").write_text(source, encoding="utf-8")
            analysis = BurhanAnalyzer().analyze(root, "أصلح الخطأ", error)

        self.assertEqual(analysis.primary.kind, "unbound_local_variable")
        self.assertIsNotNone(analysis.primary.suggested_replacement)

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

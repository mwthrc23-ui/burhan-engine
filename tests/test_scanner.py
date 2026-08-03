from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from burhan.scanner import ProjectScanner, ScanLimits, build_code_tree


class ProjectScannerTests(unittest.TestCase):
    def test_scanner_reads_supported_source_and_skips_secrets_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("print('safe')\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=do-not-read\n", encoding="utf-8")
            (root / "secrets.yaml").write_text("token: also-do-not-read\n", encoding="utf-8")
            node_modules = root / "node_modules"
            node_modules.mkdir()
            (node_modules / "index.ts").write_text("const secret = 1;\n", encoding="utf-8")

            snapshot = ProjectScanner().scan(root)

        self.assertEqual([item.relative_path for item in snapshot.files], ["app.py"])
        self.assertNotIn("do-not-read", snapshot.combined_text)
        self.assertEqual(snapshot.skipped_secret_files, 2)

    def test_scanner_enforces_file_count_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(3):
                (root / f"file_{index}.py").write_text(f"value = {index}\n", encoding="utf-8")

            snapshot = ProjectScanner(ScanLimits(max_files=2)).scan(root)

        self.assertEqual(len(snapshot.files), 2)
        self.assertTrue(snapshot.truncated)


class CodeTreeTests(unittest.TestCase):
    def test_flat_project_produces_root_with_file_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
            (root / "utils.py").write_text("X = 1\n", encoding="utf-8")

            snapshot = ProjectScanner().scan(root)
            tree = build_code_tree(snapshot)

        self.assertEqual(tree.kind, "directory")
        file_names = {child.name for child in tree.children}
        self.assertIn("app.py", file_names)
        self.assertIn("utils.py", file_names)

    def test_python_symbols_appear_as_children_of_file_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                "class MyClass:\n    pass\n\ndef helper():\n    pass\n",
                encoding="utf-8",
            )

            snapshot = ProjectScanner().scan(root)
            tree = build_code_tree(snapshot)

        file_node = next(c for c in tree.children if c.name == "app.py")
        symbol_names = {child.name for child in file_node.children}
        self.assertIn("MyClass", symbol_names)
        self.assertIn("helper", symbol_names)

    def test_subdirectory_becomes_nested_directory_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (src / "module.py").write_text("def run(): pass\n", encoding="utf-8")

            snapshot = ProjectScanner().scan(root)
            tree = build_code_tree(snapshot)

        dir_names = {child.name for child in tree.children}
        self.assertIn("src", dir_names)
        src_node = next(c for c in tree.children if c.name == "src")
        self.assertEqual(src_node.kind, "directory")
        file_names = {child.name for child in src_node.children}
        self.assertIn("module.py", file_names)

    def test_to_dict_is_json_serializable(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def run(): pass\n", encoding="utf-8")

            snapshot = ProjectScanner().scan(root)
            tree = build_code_tree(snapshot)

        payload = json.dumps(tree.to_dict())
        self.assertIn("app.py", payload)

    def test_empty_project_returns_root_directory_with_no_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = ProjectScanner().scan(root)
            tree = build_code_tree(snapshot)

        self.assertEqual(tree.kind, "directory")
        self.assertEqual(tree.children, ())


if __name__ == "__main__":
    unittest.main()

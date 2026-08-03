from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from burhan.scanner import (
    ProjectScanner,
    ScanLimits,
    TraversalLimitError,
    bounded_walk,
    build_code_tree,
    is_reparse_path,
)


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

    def test_oversized_and_invalid_utf8_files_make_snapshot_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.py").write_text("x" * 20, encoding="utf-8")
            (root / "invalid.py").write_bytes(b"\xff\xfe")

            snapshot = ProjectScanner(ScanLimits(max_file_bytes=10)).scan(root)

        self.assertEqual(snapshot.skipped_oversized_files, 1)
        self.assertEqual(snapshot.skipped_unreadable_files, 1)
        self.assertTrue(snapshot.incomplete)

    def test_scanner_enforces_directory_count_and_depth_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("one", "two", "three"):
                (root / name).mkdir()

            count_limited = ProjectScanner(ScanLimits(max_directories=2)).scan(root)

        self.assertTrue(count_limited.incomplete)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one" / "two" / "three").mkdir(parents=True)

            depth_limited = ProjectScanner(ScanLimits(max_directory_depth=2)).scan(root)

        self.assertTrue(depth_limited.incomplete)

    def test_walk_errors_make_snapshot_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with patch("burhan.scanner.os.scandir", side_effect=OSError("private path")):
                snapshot = ProjectScanner().scan(root)

        self.assertEqual(snapshot.skipped_unreadable_files, 1)
        self.assertTrue(snapshot.incomplete)

    def test_scanner_bounds_symlink_and_other_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "entry-one").write_text("one", encoding="utf-8")
            (root / "entry-two").write_text("two", encoding="utf-8")

            snapshot = ProjectScanner(ScanLimits(max_entries=1)).scan(root)

        self.assertTrue(snapshot.incomplete)
        self.assertEqual(snapshot.files, ())

    def test_bounded_walk_stops_streaming_after_limit_plus_one(self) -> None:
        consumed = 0

        class FakeEntry:
            def __init__(self, index: int) -> None:
                self.name = f"entry-{index}"

            def is_dir(self, *, follow_symlinks: bool) -> bool:
                del follow_symlinks
                return False

            def is_junction(self) -> bool:
                return False

            def stat(self, *, follow_symlinks: bool):
                del follow_symlinks

                class EntryStat:
                    st_file_attributes = 0

                return EntryStat()

        class FakeScandir:
            def __enter__(self):
                def entries():
                    nonlocal consumed
                    for index in range(100):
                        consumed += 1
                        yield FakeEntry(index)

                return entries()

            def __exit__(self, *args: object) -> None:
                del args

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("burhan.scanner.os.scandir", return_value=FakeScandir()):
                with self.assertRaises(TraversalLimitError):
                    tuple(
                        bounded_walk(
                            root,
                            max_entries=2,
                            max_directories=2,
                            max_depth=2,
                        )
                    )

        self.assertEqual(consumed, 3)

    def test_bounded_walk_never_descends_into_windows_reparse_directories(self) -> None:
        class ReparseStat:
            st_file_attributes = 0x400

        class ReparseEntry:
            name = "junction"

            def is_dir(self, *, follow_symlinks: bool) -> bool:
                del follow_symlinks
                return True

            def stat(self, *, follow_symlinks: bool) -> ReparseStat:
                del follow_symlinks
                return ReparseStat()

        class FakeScandir:
            def __enter__(self):
                return iter((ReparseEntry(),))

            def __exit__(self, *args: object) -> None:
                del args

        calls = 0

        def scandir_once(path: Path) -> FakeScandir:
            nonlocal calls
            del path
            calls += 1
            if calls > 1:
                raise AssertionError("reparse directory was traversed")
            return FakeScandir()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("burhan.scanner.os.scandir", side_effect=scandir_once):
                walked = tuple(
                    bounded_walk(
                        root,
                        max_entries=2,
                        max_directories=2,
                        max_depth=2,
                    )
                )

        self.assertEqual(walked, ((root, (), ("junction",)),))
        self.assertEqual(calls, 1)

    def test_path_reparse_detection_supports_python_311(self) -> None:
        class ReparseStat:
            st_file_attributes = 0x400

        path = Path("junction")
        with (
            patch.object(Path, "is_junction", return_value=False, create=True),
            patch.object(Path, "lstat", return_value=ReparseStat()),
        ):
            self.assertTrue(is_reparse_path(path))


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

    def test_class_methods_are_nested_inside_class_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text(
                "class Service:\n"
                "    def start(self): pass\n"
                "    def stop(self): pass\n"
                "\n"
                "def standalone(): pass\n",
                encoding="utf-8",
            )

            snapshot = ProjectScanner().scan(root)
            tree = build_code_tree(snapshot)

        file_node = next(c for c in tree.children if c.name == "service.py")
        top_level_names = {child.name for child in file_node.children}
        self.assertIn("Service", top_level_names)
        self.assertIn("standalone", top_level_names)

        class_node = next(c for c in file_node.children if c.name == "Service")
        self.assertEqual(class_node.kind, "class")
        method_names = {m.name for m in class_node.children}
        self.assertIn("start", method_names)
        self.assertIn("stop", method_names)

    def test_methods_not_duplicated_at_file_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model.py").write_text(
                "class Model:\n"
                "    def save(self): pass\n",
                encoding="utf-8",
            )

            snapshot = ProjectScanner().scan(root)
            tree = build_code_tree(snapshot)

        file_node = next(c for c in tree.children if c.name == "model.py")
        file_level_names = [child.name for child in file_node.children]
        # "save" must not appear at the file level—only inside Model
        self.assertNotIn("save", file_level_names)
        self.assertIn("Model", file_level_names)

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

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from burhan.scanner import ProjectScanner, ScanLimits


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


if __name__ == "__main__":
    unittest.main()

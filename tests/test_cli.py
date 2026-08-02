from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from burhan.cli import main


class CliTests(unittest.TestCase):
    def test_main_without_command_prints_help_hint(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = main([])

        self.assertEqual(exit_code, 2)
        self.assertIn("burhan analyze --help", stderr.getvalue())

    def test_version_flag_prints_engine_version(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--version"])

        self.assertEqual(exit_code, 0)
        self.assertIn("burhan 0.5.0", output.getvalue())

    def test_analyze_command_outputs_machine_readable_bir_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("print(mesage)\n", encoding="utf-8")
            error_file = root / "error.txt"
            error_file.write_text(
                "  File \"main.py\", line 1, in <module>\nNameError: name 'mesage' is not defined\n",
                encoding="utf-8",
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "analyze",
                        "--project",
                        str(root),
                        "--goal",
                        "شخّص الخطأ",
                        "--error-file",
                        str(error_file),
                        "--json",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["hypotheses"][0]["kind"], "undefined_name")
        self.assertIn("state", payload)
        self.assertIn("elapsed_ms", payload)
        self.assertIn("case_id", payload)
        self.assertIn("provenance", payload)
        self.assertIn("residual_risks", payload)

    def test_human_output_uses_windows_safe_ascii_for_replacement_arrow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text(
                "def message():\n    return 'ok'\n\nprint(mesage())\n",
                encoding="utf-8",
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "analyze",
                        "--project",
                        str(root),
                        "--goal",
                        "شخّص الخطأ",
                        "--error",
                        "  File \"main.py\", line 4, in <module>\nNameError: name 'mesage' is not defined",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("mesage -> message", output.getvalue())
        self.assertNotIn("→", output.getvalue())

    def test_repair_command_previews_patch_as_json_without_modifying_file(self) -> None:
        source_text = "def message():\n    return 'ok'\n\nprint(mesage())\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.py"
            source.write_text(source_text, encoding="utf-8")
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "repair",
                        "--project",
                        str(root),
                        "--goal",
                        "أصلح الخطأ بأقل تعديل",
                        "--error",
                        "  File \"main.py\", line 4, in <module>\nNameError: name 'mesage' is not defined",
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            unchanged = source.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["patch"]["applied"])
        self.assertEqual(payload["patch"]["verification"]["grade"], "V0")
        self.assertIn("mesage", payload["patch"]["diff"])
        self.assertEqual(unchanged, source_text)

    def test_repair_proof_command_reports_fail_to_pass_without_modifying_project(self) -> None:
        source_text = "def message():\n    return 'ok'\n\nprint(mesage())\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.py"
            source.write_text(source_text, encoding="utf-8")
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "repair-proof",
                        "--project",
                        str(root),
                        "--goal",
                        "أثبت الإصلاح دون تغيير الأصل",
                        "--error",
                        "  File \"main.py\", line 4, in <module>\n"
                        "NameError: name 'mesage' is not defined",
                        "--trust-local-tests",
                        "--test-program",
                        "python",
                        "--test-arg",
                        "main.py",
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            unchanged = source.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["proof"]["verified"])
        self.assertEqual(payload["proof"]["verification"]["grade"], "V1")
        self.assertNotEqual(payload["proof"]["before"]["exit_code"], 0)
        self.assertEqual(payload["proof"]["after"]["exit_code"], 0)
        self.assertTrue(payload["proof"]["original_unchanged"])
        self.assertEqual(unchanged, source_text)

    def test_repair_proof_reports_original_change_without_traceback(self) -> None:
        source_text = "def message():\n    return 'ok'\n\nprint(mesage())\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text(source_text, encoding="utf-8")
            stderr = io.StringIO()

            with patch("burhan.cli.ProofRunner.prove", side_effect=RuntimeError("original changed")):
                with redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "repair-proof",
                            "--project",
                            str(root),
                            "--goal",
                            "prove repair",
                            "--error",
                            'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                            "--trust-local-tests",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertIn("original changed", stderr.getvalue())

    def test_repair_proof_uses_pytest_image_for_docker_pytest_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("def message():\n    return 'ok'\n\nprint(mesage())\n", encoding="utf-8")
            proof_image = "burhan-pytest@sha256:1111111111111111111111111111111111111111111111111111111111111111"

            with patch("burhan.cli.PYTEST_DOCKER_IMAGE", proof_image):
                with patch("burhan.cli.ProofRunner.prove", side_effect=RuntimeError("stop")) as prove:
                    exit_code = main(
                        [
                            "repair-proof",
                            "--project",
                            str(root),
                            "--goal",
                            "prove repair",
                            "--error",
                            'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                            "--trust-local-tests",
                            "--backend",
                            "docker",
                            "--test-program",
                            "pytest",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertEqual(prove.call_args.kwargs["docker_image"], proof_image)

    def test_repair_proof_rejects_placeholder_pytest_docker_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("def message():\n    return 'ok'\n\nprint(mesage())\n", encoding="utf-8")
            stderr = io.StringIO()

            with redirect_stderr(stderr):
                exit_code = main(
                    [
                        "repair-proof",
                        "--project",
                        str(root),
                        "--goal",
                        "prove repair",
                        "--error",
                        'File "main.py", line 4\nNameError: name \'mesage\' is not defined',
                        "--trust-local-tests",
                        "--backend",
                        "docker",
                        "--test-program",
                        "pytest",
                    ]
                )

        self.assertEqual(exit_code, 1)
        self.assertIn("صورة pytest مثبتة", stderr.getvalue())

    def test_memory_add_and_search_commands_return_verified_match(self) -> None:
        from tests.test_memory import episode_payload

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "memory.sqlite3"
            episode = root / "episode.json"
            episode.write_text(
                json.dumps(
                    episode_payload(
                        episode_id="episode-send",
                        attribute="send",
                        replacement="send_message",
                        title="Client send API rename",
                    )
                ),
                encoding="utf-8",
            )
            add_output = io.StringIO()
            with redirect_stdout(add_output):
                add_code = main(
                    [
                        "memory-add",
                        "--database",
                        str(database),
                        "--episode",
                        str(episode),
                        "--json",
                    ]
                )
            search_output = io.StringIO()
            with redirect_stdout(search_output):
                search_code = main(
                    [
                        "memory-search",
                        "--database",
                        str(database),
                        "--error",
                        "AttributeError: 'ApiClient' object has no attribute 'send'",
                        "--language",
                        "python",
                        "--framework",
                        "pytest",
                        "--json",
                    ]
                )

        add_payload = json.loads(add_output.getvalue())
        search_payload = json.loads(search_output.getvalue())
        self.assertEqual(add_code, 0)
        self.assertEqual(search_code, 0)
        self.assertEqual(add_payload["stored"], "episode-send")
        self.assertEqual(search_payload["matches"][0]["episode"]["id"], "episode-send")
        self.assertEqual(
            search_payload["matches"][0]["episode"]["patch_pattern"]["to"],
            "send_message",
        )

    def test_analyze_can_attach_matches_from_repair_memory(self) -> None:
        from burhan.memory import RepairEpisode, RepairMemory
        from tests.test_memory import episode_payload

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "memory.sqlite3"
            RepairMemory(database).add(
                RepairEpisode.from_dict(
                    episode_payload(
                        episode_id="episode-send",
                        attribute="send",
                        replacement="send_message",
                        title="Client send API rename",
                    )
                )
            )
            (root / "client.py").write_text(
                "def deliver(api, payload):\n    return api.send(payload)\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "analyze",
                        "--project",
                        str(root),
                        "--goal",
                        "شخّص الخطأ وابحث في الذاكرة",
                        "--error",
                        "  File \"client.py\", line 2, in deliver\nAttributeError: 'ApiClient' object has no attribute 'send'",
                        "--memory",
                        str(database),
                        "--dependency",
                        "demo-client",
                        "--json",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["analysis"]["hypotheses"][0]["kind"], "attribute_error")
        self.assertEqual(payload["memory_matches"][0]["episode"]["id"], "episode-send")

    def test_source_import_and_search_return_a_patch_and_test_candidate(self) -> None:
        from tests.test_sources import _swebench_attribute_error_row

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "sources.sqlite3"
            fixture = root / "swebench.json"
            fixture.write_text(
                json.dumps({"rows": [{"row": _swebench_attribute_error_row()}]}),
                encoding="utf-8",
            )
            import_output = io.StringIO()
            with redirect_stdout(import_output):
                import_code = main(
                    [
                        "source-import-swebench",
                        "--database",
                        str(database),
                        "--input",
                        str(fixture),
                        "--json",
                    ]
                )
            search_output = io.StringIO()
            with redirect_stdout(search_output):
                search_code = main(
                    [
                        "source-search",
                        "--database",
                        str(database),
                        "--error",
                        "AttributeError: 'NoneType' object has no attribute 'to'",
                        "--json",
                    ]
                )

        imported = json.loads(import_output.getvalue())
        searched = json.loads(search_output.getvalue())
        self.assertEqual(import_code, 0)
        self.assertEqual(search_code, 0)
        self.assertEqual(imported["stored_raw"], 1)
        self.assertEqual(imported["attribute_error_candidates"], 1)
        self.assertEqual(searched["matches"][0]["record"]["attribute_name"], "to")
        self.assertIn("diff --git", searched["matches"][0]["record"]["solution_patch"])
        self.assertIn("test_return_annotation_none", searched["matches"][0]["record"]["test_patch"])
        self.assertEqual(
            searched["matches"][0]["proposal_status"],
            "source_candidate_not_locally_verified",
        )

    def test_bugsinpy_bundle_import_stays_unclassified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "sources.sqlite3"
            bundle = root / "bug"
            bundle.mkdir()
            (bundle / "bug.info").write_text(
                'buggy_commit_id="bad123"\nfixed_commit_id="fix456"\n',
                encoding="utf-8",
            )
            (bundle / "bug_patch.txt").write_text(
                "diff --git a/a.py b/a.py\n",
                encoding="utf-8",
            )
            (bundle / "run_test.sh").write_text(
                "pytest -q tests/test_a.py::test_bug\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "source-import-bugsinpy",
                        "--database",
                        str(database),
                        "--project",
                        "Demo",
                        "--bug",
                        "1",
                        "--bundle",
                        str(bundle),
                        "--json",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["stored_raw"], 1)
        self.assertEqual(payload["classification_status"], "unclassified")
        self.assertEqual(payload["promoted"], 0)

    def test_github_pull_request_import_is_raw_until_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "sources.sqlite3"
            fixture = root / "github-pr.json"
            fixture.write_text(
                json.dumps(
                    {
                        "issue": {
                            "title": "Fix AttributeError",
                            "body": "AttributeError: 'Client' object has no attribute 'send'",
                            "html_url": "https://github.com/example/project/pull/42",
                        },
                        "pull_request": {
                            "html_url": "https://github.com/example/project/pull/42",
                            "merge_commit_sha": "abc123",
                        },
                        "files": [
                            {"filename": "src/client.py", "patch": "@@ -1 +1 @@"},
                            {"filename": "tests/test_client.py", "patch": "+def test_send(): pass"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "source-import-github-pr",
                        "--database",
                        str(database),
                        "--repo",
                        "example/project",
                        "--pr",
                        "42",
                        "--input",
                        str(fixture),
                        "--json",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["source_id"], "github-pr:example/project:42")
        self.assertEqual(payload["classification_status"], "unclassified")
        self.assertEqual(payload["stored_raw"], 1)
        self.assertEqual(payload["promoted"], 0)

    def test_github_pr_import_rejects_invalid_repository_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "source-import-github-pr",
                        "--database",
                        str(Path(directory) / "sources.sqlite3"),
                        "--repo",
                        "bad/repo/name",
                        "--pr",
                        "1",
                        "--json",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

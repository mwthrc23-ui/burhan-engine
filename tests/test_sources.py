from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from burhan.memory import MemoryQuery, RepairMemory
from burhan.sources import (
    BugsInPySource,
    GitHubPullRequestSource,
    SafeJsonClient,
    SourceRecord,
    SourceStore,
    SweBenchVerifiedSource,
)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._stream = BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def _swebench_attribute_error_row() -> dict[str, object]:
    problem_statement = """A return annotation of None crashes quantity_input.

AttributeError: 'NoneType' object has no attribute 'to'
"""
    solution_patch = """diff --git a/astropy/units/decorators.py b/astropy/units/decorators.py
@@ -100,1 +100,1 @@
- if wrapped_signature.return_annotation is not inspect.Signature.empty:
+ if wrapped_signature.return_annotation not in (inspect.Signature.empty, None):
"""
    test_patch = """diff --git a/astropy/units/tests/test_quantity_annotations.py b/astropy/units/tests/test_quantity_annotations.py
@@ -10,0 +11,3 @@
+def test_return_annotation_none():
+    assert decorated() is None
"""
    return {
        "repo": "astropy/astropy",
        "instance_id": "astropy__astropy-7336",
        "base_commit": "0123456789abcdef",
        "problem_statement": problem_statement,
        "patch": solution_patch,
        "test_patch": test_patch,
        "FAIL_TO_PASS": json.dumps(
            ["astropy/units/tests/test_quantity_annotations.py::test_return_annotation_none"]
        ),
        "PASS_TO_PASS": json.dumps(
            ["astropy/units/tests/test_quantity_annotations.py::test_wraps_preserves_signature"]
        ),
        "version": "3.1",
        "created_at": "2018-07-09T00:00:00Z",
    }


class SweBenchVerifiedConversionTests(unittest.TestCase):
    def test_converter_preserves_description_fix_tests_and_provenance(self) -> None:
        row = _swebench_attribute_error_row()

        episode = SweBenchVerifiedSource.to_episode(row)
        payload = episode.to_dict()

        self.assertEqual(payload["problem_description"], row["problem_statement"])
        self.assertEqual(payload["solution_patch"], row["patch"])
        self.assertEqual(payload["test_patch"], row["test_patch"])
        self.assertEqual(
            payload["verification"]["fail_to_pass"],
            ["astropy/units/tests/test_quantity_annotations.py::test_return_annotation_none"],
        )
        self.assertEqual(
            payload["verification"]["pass_to_pass"],
            [
                "astropy/units/tests/test_quantity_annotations.py::"
                "test_wraps_preserves_signature"
            ],
        )
        self.assertEqual(payload["verification"]["grade"], "SOURCE_ATTESTED")
        self.assertEqual(payload["provenance"]["source_type"], "swe-bench-verified")
        self.assertEqual(payload["provenance"]["dataset_name"], "SWE-bench_Verified")
        self.assertEqual(
            payload["provenance"]["dataset_instance_id"], "astropy__astropy-7336"
        )
        self.assertEqual(payload["provenance"]["base_commit"], "0123456789abcdef")
        self.assertEqual(
            payload["provenance"]["repository_url"], "https://github.com/astropy/astropy"
        )

    def test_converter_marks_uncurated_root_cause_as_unknown(self) -> None:
        episode = SweBenchVerifiedSource.to_episode(_swebench_attribute_error_row())
        record = SweBenchVerifiedSource.to_record(_swebench_attribute_error_row())
        payload = episode.to_dict()

        self.assertEqual(payload["root_cause_status"], "unknown")
        self.assertIn("غير مصنف", payload["root_cause"])
        self.assertIsNone(record.root_cause)
        self.assertEqual(record.root_cause_status, "unknown")

    def test_unknown_cause_cannot_be_promoted_into_verified_memory(self) -> None:
        episode = SweBenchVerifiedSource.to_episode(_swebench_attribute_error_row())

        with tempfile.TemporaryDirectory() as directory:
            memory = RepairMemory(Path(directory) / "memory.sqlite3")
            with self.assertRaisesRegex(ValueError, "root cause|curated|source"):
                memory.add(episode)

            self.assertEqual(memory.count(), 0)


class BugsInPyConversionTests(unittest.TestCase):
    def test_rejects_dot_segments_in_project_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "project"):
            BugsInPySource.to_record(
                project="..",
                bug_id="1",
                files={
                    "bug.info": 'buggy_commit_id="bad"\n',
                    "bug_patch.txt": "diff --git a/a b/a\n",
                    "run_test.sh": "pytest test_a.py\n",
                },
            )

    def test_unclassified_bug_is_stored_raw_but_not_searchable_as_repair_episode(self) -> None:
        record = BugsInPySource.to_record(
            project="PySnooper",
            bug_id="1",
            files={
                "bug.info": (
                    'python_version="3.8.1"\n'
                    'buggy_commit_id="e21a111"\n'
                    'fixed_commit_id="56f2222"\n'
                    'test_file="tests/test_chinese.py"\n'
                ),
                "bug_patch.txt": "diff --git a/pysnooper/tracer.py b/pysnooper/tracer.py\n",
                "run_test.sh": "pytest -q -s tests/test_chinese.py::test_chinese\n",
            },
        )

        self.assertIsInstance(record, SourceRecord)
        self.assertEqual(record.source_id, "bugsinpy:PySnooper:1")
        self.assertEqual(record.classification_status, "unclassified")
        self.assertIsNone(record.problem_description)
        self.assertIsNone(record.root_cause)
        self.assertEqual(record.root_cause_status, "unknown")
        self.assertIn("diff --git", record.solution_patch)
        self.assertEqual(
            record.test_command, "pytest -q -s tests/test_chinese.py::test_chinese"
        )
        self.assertEqual(record.provenance["source_type"], "bugsinpy")
        self.assertEqual(record.provenance["buggy_commit"], "e21a111")
        self.assertEqual(record.provenance["fixed_commit"], "56f2222")

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "repair-memory.sqlite3"
            records = SourceStore(database)
            records.add(record)
            memory = RepairMemory(database)

            matches = memory.search(
                MemoryQuery(
                    error_text=(
                        "AttributeError: 'ApiClient' object has no attribute 'send'"
                    ),
                    language="python",
                    test_framework="pytest",
                )
            )

            self.assertEqual(records.count(), 1)
            self.assertEqual(memory.count(), 0)
        self.assertEqual(matches, ())


class GitHubPullRequestConversionTests(unittest.TestCase):
    def test_pr_without_verification_signal_is_stored_raw_not_searchable(self) -> None:
        record = GitHubPullRequestSource.to_record(
            repository="example/project",
            pull_number="42",
            issue={
                "title": "Fix AttributeError in client",
                "body": "AttributeError: 'ApiClient' object has no attribute 'send'",
                "html_url": "https://github.com/example/project/pull/42",
            },
            pull_request={
                "html_url": "https://github.com/example/project/pull/42",
                "merge_commit_sha": "abc123",
            },
            files=[
                {
                    "filename": "src/client.py",
                    "patch": "@@ -1 +1 @@\n-api.send(x)\n+api.send_message(x)",
                },
                {
                    "filename": "tests/test_client.py",
                    "patch": "@@ -1,0 +1,2 @@\n+def test_send_message():\n+    pass",
                },
            ],
        )

        self.assertEqual(record.source_id, "github-pr:example/project:42")
        self.assertEqual(record.classification_status, "unclassified")
        self.assertEqual(record.attribute_name, "send")
        self.assertIn("Fix AttributeError", record.problem_description or "")
        self.assertIn("diff --git a/src/client.py b/src/client.py", record.solution_patch)
        self.assertIn("test_send_message", record.test_patch)
        self.assertEqual(
            record.provenance["pull_request_url"],
            "https://github.com/example/project/pull/42",
        )

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "sources.sqlite3")
            store.add(record)

            matches = store.search(
                "AttributeError: 'ApiClient' object has no attribute 'send'"
            )

            self.assertEqual(matches, ())


class SourceStoreTests(unittest.TestCase):
    def test_rejects_an_oversized_source_record(self) -> None:
        record = SweBenchVerifiedSource.to_record(_swebench_attribute_error_row())
        oversized = replace(record, solution_patch="x" * 5_000_001)

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "sources.sqlite3")
            with self.assertRaisesRegex(ValueError, "patch.*limit|large"):
                store.add(oversized)

    def test_add_is_idempotent_for_the_same_source_record(self) -> None:
        record = SweBenchVerifiedSource.to_record(_swebench_attribute_error_row())

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "sources.sqlite3")

            first_inserted = store.add(record)
            second_inserted = store.add(record)

            self.assertTrue(first_inserted)
            self.assertFalse(second_inserted)
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.version_count(), 1)

    def test_changed_source_keeps_the_previous_payload_version(self) -> None:
        record = SweBenchVerifiedSource.to_record(_swebench_attribute_error_row())
        changed = replace(
            record,
            solution_patch=record.solution_patch + "\n# changed upstream",
            payload_sha256="changed-sha",
        )

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "sources.sqlite3")
            self.assertTrue(store.add(record))
            self.assertTrue(store.add(changed))

            self.assertEqual(store.count(), 1)
            self.assertEqual(store.version_count(), 2)

    def test_search_prefers_exact_attribute_even_after_many_other_candidates(self) -> None:
        base_record = SweBenchVerifiedSource.to_record(_swebench_attribute_error_row())

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "sources.sqlite3")
            for index in range(12):
                store.add(
                    replace(
                        base_record,
                        source_id=f"swebench:aa-{index:02d}",
                        attribute_name=f"other_{index}",
                        payload_sha256=f"sha-{index:02d}",
                    )
                )
            store.add(
                replace(
                    base_record,
                    source_id="swebench:zz-exact",
                    attribute_name="to",
                    payload_sha256="sha-exact",
                )
            )

            matches = store.search(
                "AttributeError: 'NoneType' object has no attribute 'to'",
                limit=1,
            )

            self.assertEqual(matches[0].record.source_id, "swebench:zz-exact")
            self.assertEqual(matches[0].score, 0.9)


class SafeJsonClientTests(unittest.TestCase):
    def test_rejects_host_outside_allowlist_before_opening_connection(self) -> None:
        opened: list[str] = []

        def opener(request: object, *, timeout: float) -> _FakeResponse:
            opened.append(str(request))
            return _FakeResponse(b"{}")

        client = SafeJsonClient(
            allowed_hosts={"api.github.com"},
            max_response_bytes=128,
            opener=opener,
        )

        with self.assertRaisesRegex(ValueError, "host.*allowed|allowlist"):
            client.get_json("https://evil.example/steal")

        self.assertEqual(opened, [])

    def test_rejects_response_larger_than_configured_limit(self) -> None:
        oversized_body = b"x" * 33

        def opener(_request: object, *, timeout: float) -> _FakeResponse:
            return _FakeResponse(oversized_body)

        client = SafeJsonClient(
            allowed_hosts={"datasets-server.huggingface.co"},
            max_response_bytes=32,
            opener=opener,
        )

        with self.assertRaisesRegex(ValueError, "response.*large|size limit"):
            client.get_json(
                "https://datasets-server.huggingface.co/rows?dataset=example"
            )

    def test_reads_json_from_an_allowed_https_host(self) -> None:
        body = json.dumps({"rows": [{"row": {"instance_id": "case-1"}}]}).encode()

        def opener(_request: object, *, timeout: float) -> _FakeResponse:
            self.assertGreater(timeout, 0)
            return _FakeResponse(body)

        client = SafeJsonClient(
            allowed_hosts={"datasets-server.huggingface.co"},
            max_response_bytes=1024,
            opener=opener,
        )

        payload = client.get_json(
            "https://datasets-server.huggingface.co/rows?dataset=example"
        )

        self.assertEqual(payload["rows"][0]["row"]["instance_id"], "case-1")


class ErrorKindClassificationTests(unittest.TestCase):
    def _make_swebench_row(self, problem_statement: str, fail_to_pass: list[str] | None = None) -> dict[str, object]:
        return {
            "repo": "example/project",
            "instance_id": "example__project-1",
            "base_commit": "abc123",
            "problem_statement": problem_statement,
            "patch": "diff --git a/x.py b/x.py\n",
            "test_patch": "diff --git a/test_x.py b/test_x.py\n",
            "FAIL_TO_PASS": json.dumps(fail_to_pass or ["tests/test_x.py::test_foo"]),
            "PASS_TO_PASS": json.dumps([]),
            "version": "1.0",
            "created_at": "2024-01-01T00:00:00Z",
        }

    def test_attribute_error_is_classified_correctly(self) -> None:
        row = self._make_swebench_row(
            "AttributeError: 'NoneType' object has no attribute 'read'"
        )
        record = SweBenchVerifiedSource.to_record(row)
        self.assertEqual(record.classification_status, "attribute_error_candidate")
        self.assertEqual(record.error_kind, "attribute_error")
        self.assertEqual(record.attribute_name, "read")

    def test_name_error_is_classified_correctly(self) -> None:
        row = self._make_swebench_row(
            "NameError: name 'my_func' is not defined when calling helper"
        )
        record = SweBenchVerifiedSource.to_record(row)
        self.assertEqual(record.classification_status, "name_error_candidate")
        self.assertEqual(record.error_kind, "name_error")
        self.assertEqual(record.attribute_name, "my_func")

    def test_module_error_is_classified_correctly(self) -> None:
        row = self._make_swebench_row(
            "ModuleNotFoundError: No module named 'pandas'"
        )
        record = SweBenchVerifiedSource.to_record(row)
        self.assertEqual(record.classification_status, "module_error_candidate")
        self.assertEqual(record.error_kind, "module_error")
        self.assertEqual(record.attribute_name, "pandas")

    def test_unrecognized_error_remains_unclassified(self) -> None:
        row = self._make_swebench_row(
            "Some unexpected issue happened in the code"
        )
        record = SweBenchVerifiedSource.to_record(row)
        self.assertEqual(record.classification_status, "unclassified")
        self.assertEqual(record.error_kind, "unknown")

    def test_error_kind_survives_round_trip_via_dict(self) -> None:
        row = self._make_swebench_row(
            "NameError: name 'calculate' is not defined"
        )
        record = SweBenchVerifiedSource.to_record(row)
        restored = SourceRecord.from_dict(record.to_dict())
        self.assertEqual(restored.error_kind, "name_error")
        self.assertEqual(restored.classification_status, "name_error_candidate")

    def test_name_error_candidate_is_searchable_by_symbol(self) -> None:
        row = self._make_swebench_row(
            "NameError: name 'calculate' is not defined"
        )
        record = SweBenchVerifiedSource.to_record(row)

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "store.sqlite3")
            store.add(record)

            matches = store.search("NameError: name 'calculate' is not defined")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].record.error_kind, "name_error")

    def test_search_isolates_name_error_from_unbound_local_error(self) -> None:
        name_row = {
            "repo": "example/project",
            "instance_id": "example__project-name",
            "base_commit": "abc123",
            "problem_statement": "NameError: name 'calculate' is not defined",
            "patch": "diff --git a/x.py b/x.py\n",
            "test_patch": "diff --git a/test_x.py b/test_x.py\n",
            "FAIL_TO_PASS": json.dumps(["tests/test_x.py::test_foo"]),
            "PASS_TO_PASS": json.dumps([]),
            "version": "1.0",
            "created_at": "2024-01-01T00:00:00Z",
        }
        unbound_row = {
            "repo": "example/project",
            "instance_id": "example__project-unbound",
            "base_commit": "abc123",
            "problem_statement": "UnboundLocalError: local variable 'calculate' referenced before assignment",
            "patch": "diff --git a/y.py b/y.py\n",
            "test_patch": "diff --git a/test_y.py b/test_y.py\n",
            "FAIL_TO_PASS": json.dumps(["tests/test_y.py::test_bar"]),
            "PASS_TO_PASS": json.dumps([]),
            "version": "1.0",
            "created_at": "2024-01-01T00:00:00Z",
        }
        name_record = SweBenchVerifiedSource.to_record(name_row)
        unbound_record = SweBenchVerifiedSource.to_record(unbound_row)

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "store.sqlite3")
            store.add(name_record)
            store.add(unbound_record)

            # searching for NameError should only return name_error kind
            name_matches = store.search("NameError: name 'calculate' is not defined")
            self.assertEqual(len(name_matches), 1)
            self.assertEqual(name_matches[0].record.error_kind, "name_error")

            # searching for UnboundLocalError should only return unbound_local_error kind
            unbound_matches = store.search(
                "UnboundLocalError: local variable 'calculate' referenced before assignment"
            )
            self.assertEqual(len(unbound_matches), 1)
            self.assertEqual(unbound_matches[0].record.error_kind, "unbound_local_error")

    def test_legacy_rows_are_backfilled_and_remain_searchable(self) -> None:
        row = self._make_swebench_row(
            "NameError: name 'calculate' is not defined"
        )
        record = SweBenchVerifiedSource.to_record(row)
        legacy_payload = record.to_dict()
        legacy_payload.pop("error_kind")

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "store.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE source_record_versions (
                        source_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        classification_status TEXT NOT NULL,
                        attribute_name TEXT,
                        payload_json TEXT NOT NULL,
                        imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (source_id, payload_sha256)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO source_record_versions (
                        source_id, payload_sha256, classification_status,
                        attribute_name, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.source_id,
                        record.payload_sha256,
                        record.classification_status,
                        record.attribute_name,
                        json.dumps(legacy_payload),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            store = SourceStore(database)
            matches = store.search("NameError: name 'calculate' is not defined")

            connection = sqlite3.connect(database)
            try:
                migrated_kind = connection.execute(
                    "SELECT error_kind FROM source_record_versions WHERE source_id = ?",
                    (record.source_id,),
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(migrated_kind, "name_error")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].record.source_id, record.source_id)
        self.assertEqual(matches[0].record.error_kind, "name_error")

    def test_legacy_import_row_is_reclassified_from_full_description(self) -> None:
        row = self._make_swebench_row(
            "ImportError: cannot import name 'alpha' from 'pkg.a'"
        )
        record = SweBenchVerifiedSource.to_record(row)
        legacy_payload = record.to_dict()
        legacy_payload.pop("error_kind")
        legacy_payload["classification_status"] = "module_error_candidate"
        legacy_payload["attribute_name"] = "cannot"
        legacy_payload["error_text"] = "ImportError: cannot"

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "store.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE source_record_versions (
                        source_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        classification_status TEXT NOT NULL,
                        attribute_name TEXT,
                        payload_json TEXT NOT NULL,
                        imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (source_id, payload_sha256)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO source_record_versions (
                        source_id, payload_sha256, classification_status,
                        attribute_name, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.source_id,
                        record.payload_sha256,
                        "module_error_candidate",
                        "cannot",
                        json.dumps(legacy_payload),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            store = SourceStore(database)
            matches = store.search(
                "ImportError: cannot import name 'alpha' from 'pkg.a'"
            )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].record.error_kind, "missing_import_name")
        self.assertEqual(matches[0].record.attribute_name, "alpha")

    def test_v3_import_row_with_old_module_kind_is_reclassified(self) -> None:
        row = self._make_swebench_row(
            "ImportError: cannot import name 'alpha' from 'pkg.a'"
        )
        record = SweBenchVerifiedSource.to_record(row)
        legacy_payload = record.to_dict() | {
            "classification_status": "module_error_candidate",
            "attribute_name": "cannot",
            "error_kind": "module_error",
            "error_text": "ImportError: cannot",
        }

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "store.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """
                    CREATE TABLE source_record_versions (
                        source_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        classification_status TEXT NOT NULL,
                        attribute_name TEXT,
                        error_kind TEXT NOT NULL DEFAULT 'unknown',
                        payload_json TEXT NOT NULL,
                        imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (source_id, payload_sha256)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO source_record_versions (
                        source_id, payload_sha256, classification_status,
                        attribute_name, error_kind, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.source_id,
                        record.payload_sha256,
                        "module_error_candidate",
                        "cannot",
                        "module_error",
                        json.dumps(legacy_payload),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            store = SourceStore(database)
            matches = store.search(
                "ImportError: cannot import name 'alpha' from 'pkg.a'"
            )

            connection = sqlite3.connect(database)
            try:
                migrated = connection.execute(
                    """
                    SELECT classification_status, attribute_name, error_kind
                    FROM source_record_versions
                    WHERE source_id = ?
                    """,
                    (record.source_id,),
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(
            migrated,
            ("module_error_candidate", "alpha", "missing_import_name"),
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].record.attribute_name, "alpha")
        self.assertEqual(matches[0].record.error_kind, "missing_import_name")

    def test_import_name_sources_are_isolated_by_missing_name(self) -> None:
        alpha_row = self._make_swebench_row(
            "ImportError: cannot import name 'alpha' from 'pkg.a'"
        )
        alpha_row["instance_id"] = "example__project-alpha"
        beta_row = self._make_swebench_row(
            "ImportError: cannot import name 'beta' from 'pkg.b'"
        )
        beta_row["instance_id"] = "example__project-beta"
        alpha = SweBenchVerifiedSource.to_record(alpha_row)
        beta = SweBenchVerifiedSource.to_record(beta_row)

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "store.sqlite3")
            store.add(alpha)
            store.add(beta)
            matches = store.search(
                "ImportError: cannot import name 'alpha' from 'pkg.a'"
            )

        self.assertEqual(alpha.error_kind, "missing_import_name")
        self.assertEqual(alpha.attribute_name, "alpha")
        self.assertEqual([match.record.source_id for match in matches], [alpha.source_id])

    def test_windows_file_not_found_and_os_error_are_classified(self) -> None:
        windows_row = self._make_swebench_row(
            "FileNotFoundError: [WinError 2] The system cannot find the file specified: "
            "'C:\\data\\config.json'"
        )
        os_row = self._make_swebench_row(
            "OSError: [Errno 111] Connection refused"
        )

        windows_record = SweBenchVerifiedSource.to_record(windows_row)
        os_record = SweBenchVerifiedSource.to_record(os_row)

        self.assertEqual(windows_record.error_kind, "file_not_found_error")
        self.assertEqual(windows_record.attribute_name, "C:\\data\\config.json")
        self.assertEqual(os_record.error_kind, "os_error")
        self.assertEqual(os_record.attribute_name, "111")

    def test_attribute_error_search_still_works_with_new_schema(self) -> None:
        row = _swebench_attribute_error_row()
        record = SweBenchVerifiedSource.to_record(row)

        with tempfile.TemporaryDirectory() as directory:
            store = SourceStore(Path(directory) / "store.sqlite3")
            store.add(record)

            matches = store.search(
                "AttributeError: 'NoneType' object has no attribute 'to'"
            )
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].record.error_kind, "attribute_error")

    def test_github_pr_detects_name_error(self) -> None:
        from burhan.sources import GitHubPullRequestSource

        record = GitHubPullRequestSource.to_record(
            repository="example/project",
            pull_number="7",
            issue={
                "title": "NameError in helper",
                "body": "NameError: name 'build_index' is not defined",
                "html_url": "https://github.com/example/project/pull/7",
            },
            pull_request={
                "html_url": "https://github.com/example/project/pull/7",
                "merge_commit_sha": "def456",
            },
            files=[
                {
                    "filename": "src/helper.py",
                    "patch": "@@ -1 +1 @@\n-build()\n+build_index()",
                },
            ],
        )
        self.assertEqual(record.error_kind, "name_error")
        self.assertEqual(record.attribute_name, "build_index")


if __name__ == "__main__":
    unittest.main()

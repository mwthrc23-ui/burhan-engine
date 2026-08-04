"""Tests for Phase 5: Evidence Gate V2 and SARIF Reports.

Covers mandatory test case:
9. Attempt to write report over existing file or via symlink
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from burhan.reports.sarif_reporter import (
    gate_report_to_sarif,
    hypotheses_to_sarif,
    write_sarif,
    SARIF_VERSION,
)


# ---------------------------------------------------------------------------
# SARIF document structure
# ---------------------------------------------------------------------------

class SarifDocumentTests(unittest.TestCase):
    def _make_hyps(self) -> list[dict]:
        return [
            {
                "kind": "undefined_name",
                "explanation": "الاسم 'foo' غير معرّف",
                "location": "src/app.py:10",
                "confidence": 0.9,
                "evidence": [],
            },
            {
                "kind": "missing_attribute",
                "explanation": "الخاصية 'bar' غير موجودة",
                "location": None,
                "confidence": 0.6,
                "evidence": [],
            },
        ]

    def test_sarif_version(self) -> None:
        doc = hypotheses_to_sarif([], "0.7.1", "case-abc")
        self.assertEqual(doc["version"], SARIF_VERSION)

    def test_sarif_has_schema(self) -> None:
        doc = hypotheses_to_sarif([], "0.7.1", "case-abc")
        self.assertIn("$schema", doc)

    def test_one_run(self) -> None:
        doc = hypotheses_to_sarif(self._make_hyps(), "0.7.1", "case-abc")
        self.assertEqual(len(doc["runs"]), 1)

    def test_results_count_matches_hypotheses(self) -> None:
        hyps = self._make_hyps()
        doc = hypotheses_to_sarif(hyps, "0.7.1", "case-abc")
        self.assertEqual(len(doc["runs"][0]["results"]), len(hyps))

    def test_rule_ids_follow_convention(self) -> None:
        doc = hypotheses_to_sarif(self._make_hyps(), "0.7.1", "case-abc")
        rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
        self.assertTrue(all(rid.startswith("BURHAN-") for rid in rule_ids))

    def test_high_confidence_maps_to_error_level(self) -> None:
        hyps = [{"kind": "syntax_error", "explanation": "err", "location": None, "confidence": 0.95}]
        doc = hypotheses_to_sarif(hyps, "0.7.1", "case-abc")
        self.assertEqual(doc["runs"][0]["results"][0]["level"], "error")

    def test_medium_confidence_maps_to_warning(self) -> None:
        hyps = [{"kind": "type_error", "explanation": "err", "location": None, "confidence": 0.65}]
        doc = hypotheses_to_sarif(hyps, "0.7.1", "case-abc")
        self.assertEqual(doc["runs"][0]["results"][0]["level"], "warning")

    def test_low_confidence_maps_to_note(self) -> None:
        hyps = [{"kind": "unknown", "explanation": "err", "location": None, "confidence": 0.3}]
        doc = hypotheses_to_sarif(hyps, "0.7.1", "case-abc")
        self.assertEqual(doc["runs"][0]["results"][0]["level"], "note")

    def test_location_included_when_present(self) -> None:
        hyps = [{"kind": "undefined_name", "explanation": "err", "location": "app.py:5", "confidence": 0.9}]
        doc = hypotheses_to_sarif(hyps, "0.7.1", "case-abc")
        result = doc["runs"][0]["results"][0]
        self.assertIn("locations", result)
        artifact = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertIn("app.py", artifact)

    def test_absolute_path_stripped(self) -> None:
        hyps = [{"kind": "undefined_name", "explanation": "err", "location": "/home/user/project/src/app.py:5", "confidence": 0.9}]
        doc = hypotheses_to_sarif(hyps, "0.7.1", "case-abc")
        result = doc["runs"][0]["results"][0]
        uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertFalse(uri.startswith("/"), f"absolute path leaked: {uri}")

    def test_windows_absolute_path_stripped(self) -> None:
        hyps = [{"kind": "undefined_name", "explanation": "err", "location": r"C:\Users\person\project\src\app.py:5", "confidence": 0.9}]
        doc = hypotheses_to_sarif(hyps, "0.8.0", "case-abc")
        result = doc["runs"][0]["results"][0]
        uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertEqual(uri, "src/app.py")

    def test_engine_version_in_tool_driver(self) -> None:
        doc = hypotheses_to_sarif([], "1.2.3", "case-abc")
        self.assertEqual(doc["runs"][0]["tool"]["driver"]["version"], "1.2.3")

    def test_json_serialisable(self) -> None:
        doc = hypotheses_to_sarif(self._make_hyps(), "0.7.1", "case-abc")
        json.dumps(doc)  # must not raise


# ---------------------------------------------------------------------------
# gate_report_to_sarif
# ---------------------------------------------------------------------------

class GateReportToSarifTests(unittest.TestCase):
    def _make_gate_report(self, decision: str = "pass", violations: list | None = None) -> dict:
        return {
            "schema_version": "burhan.ci-gate/v1",
            "decision": decision,
            "case_id": "case-xyz",
            "violations": violations or [],
        }

    def test_passing_report_produces_no_results(self) -> None:
        doc = gate_report_to_sarif(self._make_gate_report("pass"), "0.7.1")
        self.assertEqual(len(doc["runs"][0]["results"]), 0)

    def test_failing_report_has_results(self) -> None:
        violations = [{"code": "MIN_GRADE", "message": "درجة التحقق أقل من المطلوب"}]
        doc = gate_report_to_sarif(self._make_gate_report("fail", violations), "0.7.1")
        self.assertGreater(len(doc["runs"][0]["results"]), 0)

    def test_violation_rule_id_prefixed(self) -> None:
        violations = [{"code": "POLICY_FAIL", "message": "violation"}]
        doc = gate_report_to_sarif(self._make_gate_report("fail", violations), "0.7.1")
        rule_id = doc["runs"][0]["results"][0]["ruleId"]
        self.assertTrue(rule_id.startswith("BURHAN-GATE-"))

    def test_json_serialisable(self) -> None:
        doc = gate_report_to_sarif(self._make_gate_report(), "0.7.1")
        json.dumps(doc)


# ---------------------------------------------------------------------------
# Mandatory test 9: write_sarif – overwrite and symlink protection
# ---------------------------------------------------------------------------

class WriteSarifTests(unittest.TestCase):
    def _doc(self) -> dict:
        return hypotheses_to_sarif([], "0.7.1", "case-abc")

    def test_writes_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.sarif"
            write_sarif(self._doc(), out)
            self.assertTrue(out.exists())
            content = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(content["version"], SARIF_VERSION)

    def test_refuses_to_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.sarif"
            out.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError, msg="Should refuse to overwrite"):
                write_sarif(self._doc(), out)

    def test_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.sarif"
            real.write_text("{}", encoding="utf-8")
            link = Path(tmp) / "link.sarif"
            try:
                link.symlink_to(real)
            except NotImplementedError:
                self.skipTest("symlinks not supported on this platform")
            with self.assertRaises(ValueError, msg="Should refuse symlink"):
                write_sarif(self._doc(), link)

    def test_refuses_wrong_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.txt"
            with self.assertRaises(ValueError):
                write_sarif(self._doc(), out)

    def test_accepts_json_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            write_sarif(self._doc(), out)
            self.assertTrue(out.exists())

    def test_no_tmp_file_left_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.sarif"
            write_sarif(self._doc(), out)
            tmp_files = list(Path(tmp).glob("*.tmp"))
            self.assertEqual(len(tmp_files), 0)

    def test_predictable_tmp_symlink_is_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.txt"
            outside.write_text("unchanged", encoding="utf-8")
            (root / "report.tmp").symlink_to(outside)
            write_sarif(self._doc(), root / "report.sarif")
            self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")


if __name__ == "__main__":
    unittest.main()

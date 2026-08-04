"""SARIF 2.1.0 report generator for Burhan diagnostics.

Converts a ``GateReport`` or an ``AnalysisResult`` into a Static Analysis
Results Interchange Format (SARIF) document suitable for display in GitHub
Advanced Security, Azure DevOps, and other CI platforms.

References
----------
* SARIF 2.1.0 specification: https://docs.oasis-open.org/sarif/sarif/v2.1.0/
* GitHub SARIF upload: https://docs.github.com/en/code-security/code-scanning

Notes
-----
* Output is deliberately sanitised: no raw error text, no secrets, no
  absolute paths.
* Only relative file paths are included.
* Rule IDs follow the pattern ``BURHAN-<HYPOTHESIS-KIND>``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0-rtm.5.json"
TOOL_NAME = "burhan"
TOOL_URI = "https://github.com/mwthrc23-ui/burhan-engine"


def _sanitise_path(path: str) -> str:
    """Return a relative POSIX path, stripping any absolute component."""
    # Ensure the path is relative and uses forward slashes
    p = Path(path)
    if p.is_absolute():
        # Return only the last two components to avoid leaking directory trees
        parts = p.parts
        return "/".join(parts[-2:]) if len(parts) >= 2 else p.name
    return path.replace("\\", "/")


def _make_rule(kind: str, explanation: str) -> dict[str, Any]:
    return {
        "id": f"BURHAN-{kind.upper()}",
        "name": kind,
        "shortDescription": {"text": explanation[:80]},
        "helpUri": TOOL_URI,
        "properties": {"tags": ["burhan"]},
    }


def _location_from_str(location: str | None) -> dict[str, Any] | None:
    """Parse ``"file.py:42"`` into a SARIF physicalLocation."""
    if not location:
        return None
    parts = location.rsplit(":", 1)
    file_path = _sanitise_path(parts[0])
    try:
        line = int(parts[1])
    except (IndexError, ValueError):
        line = 1
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": file_path, "uriBaseId": "%SRCROOT%"},
            "region": {"startLine": line},
        }
    }


def hypotheses_to_sarif(
    hypotheses: list[dict[str, Any]],
    engine_version: str,
    case_id: str,
) -> dict[str, Any]:
    """Convert a list of hypothesis dicts to a SARIF document.

    Parameters
    ----------
    hypotheses:
        List of ``Hypothesis.to_dict()`` payloads.
    engine_version:
        Engine version string (e.g. ``"0.7.1"``).
    case_id:
        Case identifier for traceability.

    Returns
    -------
    dict
        SARIF 2.1.0 document.
    """
    rules_seen: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for hyp in hypotheses:
        kind = str(hyp.get("kind", "unknown"))
        explanation = str(hyp.get("explanation", ""))
        location_str = hyp.get("location")
        confidence = float(hyp.get("confidence", 0.0))

        rule_id = f"BURHAN-{kind.upper()}"
        if rule_id not in rules_seen:
            rules_seen[rule_id] = _make_rule(kind, explanation)

        # Map confidence to SARIF level
        if confidence >= 0.8:
            level = "error"
        elif confidence >= 0.55:
            level = "warning"
        else:
            level = "note"

        result: dict[str, Any] = {
            "ruleId": rule_id,
            "level": level,
            "message": {"text": explanation[:200]},
            "properties": {
                "confidence": confidence,
                "case_id": case_id,
                "hypothesis_kind": kind,
            },
        }
        loc = _location_from_str(location_str)
        if loc:
            result["locations"] = [loc]

        results.append(result)

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": engine_version,
                        "informationUri": TOOL_URI,
                        "rules": list(rules_seen.values()),
                    }
                },
                "results": results,
            }
        ],
    }


def gate_report_to_sarif(
    gate_report: dict[str, Any],
    engine_version: str,
) -> dict[str, Any]:
    """Convert a ``GateReport.to_dict()`` payload to a SARIF document.

    Violations become SARIF results; a passing report produces no results.
    """
    case_id = str(gate_report.get("case_id", ""))
    decision = str(gate_report.get("decision", ""))
    violations: list[dict[str, Any]] = gate_report.get("violations", [])

    rules_seen: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for violation in violations:
        rule_code = str(violation.get("code", "POLICY"))
        rule_id = f"BURHAN-GATE-{rule_code.upper()}"
        message = str(violation.get("message", "policy violation"))[:200]

        if rule_id not in rules_seen:
            rules_seen[rule_id] = {
                "id": rule_id,
                "name": rule_code,
                "shortDescription": {"text": message[:80]},
                "helpUri": TOOL_URI,
                "properties": {"tags": ["burhan", "gate"]},
            }
        results.append({
            "ruleId": rule_id,
            "level": "error",
            "message": {"text": message},
            "properties": {"case_id": case_id, "gate_decision": decision},
        })

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": engine_version,
                        "informationUri": TOOL_URI,
                        "rules": list(rules_seen.values()),
                    }
                },
                "results": results,
            }
        ],
    }


def write_sarif(
    sarif_doc: dict[str, Any],
    output_path: Path,
) -> None:
    """Write *sarif_doc* to *output_path* atomically.

    Raises
    ------
    ValueError
        If *output_path* already exists, is a symlink, or doesn't end
        with ``.sarif`` / ``.json``.
    """
    resolved = output_path.expanduser().resolve()
    # Reject symlinks (follow-symlink protection)
    if output_path.is_symlink():
        raise ValueError(f"report path must not be a symlink: {output_path}")
    if resolved.exists():
        raise ValueError(f"SARIF output file already exists: {resolved}")
    if resolved.suffix.lower() not in {".sarif", ".json"}:
        raise ValueError("SARIF output path must end with .sarif or .json")

    tmp_path = resolved.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(sarif_doc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.rename(resolved)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

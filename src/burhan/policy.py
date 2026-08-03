from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .model import AnalysisResult
from .patcher import ProofResult


POLICY_MAX_BYTES = 64 * 1024
POLICY_SCHEMA_VERSION = "burhan.gate-policy/v1"
REPORT_SCHEMA_VERSION = "burhan.ci-gate/v1"
GRADE_ORDER = {"V0": 0, "V1": 1, "V2": 2}
DEFAULT_REQUIRED_CHECKS = (
    "temporary_copy",
    "test_failed_before_patch",
    "patch_applied_to_copy",
    "test_passed_after_patch",
    "original_unchanged",
    "shell_false",
    "sanitized_environment",
    "parent_timeout_enforced",
    "network_disabled",
    "read_only_container",
    "capabilities_dropped",
    "resource_limits",
)
KNOWN_CHECKS = frozenset(DEFAULT_REQUIRED_CHECKS)
_POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "minimum_grade",
        "minimum_confidence",
        "require_complete_scan",
        "require_original_unchanged",
        "allowed_backends",
        "required_checks",
        "allowed_command_fingerprints",
        "allowed_runtime_fingerprints",
    }
)


@dataclass(frozen=True, slots=True)
class GatePolicy:
    minimum_grade: str = "V2"
    minimum_confidence: float = 0.8
    require_complete_scan: bool = True
    require_original_unchanged: bool = True
    allowed_backends: tuple[str, ...] = ("docker",)
    required_checks: tuple[str, ...] = DEFAULT_REQUIRED_CHECKS
    allowed_command_fingerprints: tuple[str, ...] = ()
    allowed_runtime_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.minimum_grade not in {"V1", "V2"}:
            raise ValueError("minimum_grade must be V1 or V2")
        if (
            isinstance(self.minimum_confidence, bool)
            or not isinstance(self.minimum_confidence, (int, float))
            or not 0.5 <= float(self.minimum_confidence) <= 1.0
        ):
            raise ValueError("minimum_confidence must be a number between 0.5 and 1")
        if self.require_complete_scan is not True:
            raise ValueError("require_complete_scan cannot be disabled")
        if self.require_original_unchanged is not True:
            raise ValueError("require_original_unchanged cannot be disabled")
        _validate_tuple(self.allowed_backends, "allowed_backends", frozenset({"local", "docker"}))
        _validate_tuple(self.required_checks, "required_checks", KNOWN_CHECKS)
        _validate_fingerprint_tuple(
            self.allowed_command_fingerprints, "allowed_command_fingerprints"
        )
        _validate_fingerprint_tuple(
            self.allowed_runtime_fingerprints, "allowed_runtime_fingerprints"
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> GatePolicy:
        unknown = sorted(set(value) - _POLICY_FIELDS)
        if unknown:
            raise ValueError(f"unknown policy fields: {', '.join(unknown)}")
        if value.get("schema_version") != POLICY_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {POLICY_SCHEMA_VERSION}")

        minimum_grade = value.get("minimum_grade", "V2")
        if not isinstance(minimum_grade, str) or minimum_grade not in {"V1", "V2"}:
            raise ValueError("minimum_grade must be V1 or V2")

        minimum_confidence = value.get("minimum_confidence", 0.8)
        if (
            isinstance(minimum_confidence, bool)
            or not isinstance(minimum_confidence, (int, float))
            or not 0.5 <= float(minimum_confidence) <= 1.0
        ):
            raise ValueError("minimum_confidence must be a number between 0.5 and 1")

        require_complete_scan = _strict_bool(
            value.get("require_complete_scan", True), "require_complete_scan"
        )
        require_original_unchanged = _strict_bool(
            value.get("require_original_unchanged", True), "require_original_unchanged"
        )
        if not require_complete_scan:
            raise ValueError("require_complete_scan cannot be disabled")
        if not require_original_unchanged:
            raise ValueError("require_original_unchanged cannot be disabled")
        allowed_backends = _string_tuple(
            value.get("allowed_backends", ["docker"]),
            "allowed_backends",
            allowed=frozenset({"local", "docker"}),
        )
        required_checks = _string_tuple(
            value.get("required_checks", list(DEFAULT_REQUIRED_CHECKS)),
            "required_checks",
            allowed=KNOWN_CHECKS,
        )
        allowed_command_fingerprints = _fingerprint_tuple(
            value.get("allowed_command_fingerprints", []), "allowed_command_fingerprints"
        )
        allowed_runtime_fingerprints = _fingerprint_tuple(
            value.get("allowed_runtime_fingerprints", []), "allowed_runtime_fingerprints"
        )

        return cls(
            minimum_grade=minimum_grade,
            minimum_confidence=float(minimum_confidence),
            require_complete_scan=require_complete_scan,
            require_original_unchanged=require_original_unchanged,
            allowed_backends=allowed_backends,
            required_checks=required_checks,
            allowed_command_fingerprints=allowed_command_fingerprints,
            allowed_runtime_fingerprints=allowed_runtime_fingerprints,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "minimum_grade": self.minimum_grade,
            "minimum_confidence": self.minimum_confidence,
            "require_complete_scan": self.require_complete_scan,
            "require_original_unchanged": self.require_original_unchanged,
            "allowed_backends": list(self.allowed_backends),
            "required_checks": list(self.required_checks),
            "allowed_command_fingerprints": list(self.allowed_command_fingerprints),
            "allowed_runtime_fingerprints": list(self.allowed_runtime_fingerprints),
        }

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True, slots=True)
class GateViolation:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class GateObservation:
    confidence: float
    scan_truncated: bool
    grade: str
    backend: str
    verified: bool
    original_unchanged: bool
    checks: tuple[str, ...]
    patch_artifact_hash: str
    test_failed_before: bool
    test_passed_after: bool
    command_fingerprint: str
    runtime_fingerprint: str
    project_manifest_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "scan_truncated": self.scan_truncated,
            "grade": self.grade,
            "backend": self.backend,
            "verified": self.verified,
            "original_unchanged": self.original_unchanged,
            "checks": list(self.checks),
            "patch_artifact_hash": self.patch_artifact_hash,
            "test_failed_before": self.test_failed_before,
            "test_passed_after": self.test_passed_after,
            "command_fingerprint": self.command_fingerprint,
            "runtime_fingerprint": self.runtime_fingerprint,
            "project_manifest_fingerprint": self.project_manifest_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    case_id: str
    engine_version: str
    input_fingerprint: str
    policy: GatePolicy
    observed: GateObservation
    violations: tuple[GateViolation, ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "decision": "pass" if self.passed else "fail",
            "case_id": self.case_id,
            "engine_version": self.engine_version,
            "input_fingerprint": self.input_fingerprint,
            "policy_fingerprint": self.policy.fingerprint,
            "policy": self.policy.to_dict(),
            "observed": self.observed.to_dict(),
            "violations": [item.to_dict() for item in self.violations],
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            **payload,
            "report_fingerprint": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        }


def load_policy(path: Path) -> GatePolicy:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"policy file does not exist: {resolved}")
    with resolved.open("rb") as stream:
        raw = stream.read(POLICY_MAX_BYTES + 1)
    if len(raw) > POLICY_MAX_BYTES:
        raise ValueError("policy file exceeds the 64 KiB limit")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except json.JSONDecodeError as error:
        raise ValueError("policy file must contain valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("policy file root must be a JSON object")
    return GatePolicy.from_mapping(payload)


def evaluate_gate(
    analysis: AnalysisResult,
    proof: ProofResult,
    policy: GatePolicy,
) -> GateReport:
    checks = frozenset(proof.verification.checks)
    command_fingerprint = fingerprint_command(proof.command)
    runtime_fingerprint = fingerprint_runtime(proof.runtime)
    violations: list[GateViolation] = []

    if not proof.verified:
        violations.append(GateViolation("proof_not_verified", "لم يُثبت الإصلاح"))
    if proof.before.timed_out or proof.before.exit_code in (None, 0):
        violations.append(
            GateViolation("baseline_not_failed", "لم يثبت أن الاختبار فشل قبل الرقعة")
        )
    if proof.after.timed_out or proof.after.exit_code != 0:
        violations.append(
            GateViolation("patched_test_not_passed", "لم يثبت نجاح الاختبار بعد الرقعة")
        )
    observed_grade = GRADE_ORDER.get(proof.verification.grade, -1)
    if observed_grade < GRADE_ORDER[policy.minimum_grade]:
        violations.append(
            GateViolation(
                "grade_below_minimum",
                f"درجة الإثبات {proof.verification.grade} أقل من {policy.minimum_grade}",
            )
        )
    if analysis.confidence < policy.minimum_confidence:
        violations.append(
            GateViolation(
                "confidence_below_minimum",
                "ثقة التشخيص أقل من الحد المطلوب",
            )
        )
    if analysis.provenance.scan_truncated:
        violations.append(GateViolation("scan_incomplete", "مسح المشروع غير مكتمل"))
    if not proof.original_unchanged:
        violations.append(GateViolation("original_changed", "تغير المشروع الأصلي أثناء الإثبات"))
    if proof.backend not in policy.allowed_backends:
        violations.append(
            GateViolation("backend_not_allowed", "بيئة الإثبات غير مسموح بها في السياسة")
        )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", proof.project_manifest_fingerprint) is None:
        violations.append(
            GateViolation("project_manifest_missing", "بصمة مدخلات مشروع الإثبات مفقودة")
        )
    if (
        policy.allowed_command_fingerprints
        and command_fingerprint not in policy.allowed_command_fingerprints
    ):
        violations.append(GateViolation("command_not_allowed", "أمر الاختبار غير مثبت في السياسة"))
    if (
        policy.allowed_runtime_fingerprints
        and runtime_fingerprint not in policy.allowed_runtime_fingerprints
    ):
        violations.append(GateViolation("runtime_not_allowed", "بيئة الإثبات غير مثبتة في السياسة"))
    invariant_checks = {
        "temporary_copy",
        "test_failed_before_patch",
        "patch_applied_to_copy",
        "test_passed_after_patch",
        "original_unchanged",
        "shell_false",
        "sanitized_environment",
        "parent_timeout_enforced",
    }
    if proof.verification.grade == "V2":
        invariant_checks.update(
            {
                "network_disabled",
                "read_only_container",
                "capabilities_dropped",
                "resource_limits",
            }
        )
    for required in sorted(set(policy.required_checks) | invariant_checks):
        if required not in checks:
            violations.append(
                GateViolation("required_check_missing", f"فحص مطلوب مفقود: {required}")
            )

    observation = GateObservation(
        confidence=analysis.confidence,
        scan_truncated=analysis.provenance.scan_truncated,
        grade=proof.verification.grade,
        backend=proof.backend,
        verified=proof.verified,
        original_unchanged=proof.original_unchanged,
        checks=proof.verification.checks,
        patch_artifact_hash=proof.patch.artifact_hash,
        test_failed_before=proof.before.exit_code not in (None, 0) and not proof.before.timed_out,
        test_passed_after=proof.after.exit_code == 0 and not proof.after.timed_out,
        command_fingerprint=command_fingerprint,
        runtime_fingerprint=runtime_fingerprint,
        project_manifest_fingerprint=proof.project_manifest_fingerprint,
    )
    return GateReport(
        case_id=analysis.case_id,
        engine_version=analysis.provenance.engine_version,
        input_fingerprint=analysis.provenance.input_fingerprint,
        policy=policy,
        observed=observation,
        violations=tuple(violations),
    )


def proof_failure_report(
    analysis: AnalysisResult,
    policy: GatePolicy,
    *,
    backend: str,
    command: tuple[str, ...],
    runtime: str,
    project_manifest_fingerprint: str,
) -> GateReport:
    """Create a bounded audit record when proof execution cannot complete."""

    command_fingerprint = fingerprint_command(command)
    runtime_fingerprint = fingerprint_runtime(runtime)
    violations = [
        GateViolation(
            "proof_execution_failed",
            "تعذر إكمال إثبات الإصلاح؛ راجع سجل التنفيذ المحلي",
        )
    ]
    if (
        policy.allowed_command_fingerprints
        and command_fingerprint not in policy.allowed_command_fingerprints
    ):
        violations.append(GateViolation("command_not_allowed", "أمر الاختبار غير مثبت في السياسة"))
    if (
        policy.allowed_runtime_fingerprints
        and runtime_fingerprint not in policy.allowed_runtime_fingerprints
    ):
        violations.append(GateViolation("runtime_not_allowed", "بيئة الإثبات غير مثبتة في السياسة"))

    return GateReport(
        case_id=analysis.case_id,
        engine_version=analysis.provenance.engine_version,
        input_fingerprint=analysis.provenance.input_fingerprint,
        policy=policy,
        observed=GateObservation(
            confidence=analysis.confidence,
            scan_truncated=analysis.provenance.scan_truncated,
            grade="none",
            backend=backend,
            verified=False,
            original_unchanged=False,
            checks=(),
            patch_artifact_hash="",
            test_failed_before=False,
            test_passed_after=False,
            command_fingerprint=command_fingerprint,
            runtime_fingerprint=runtime_fingerprint,
            project_manifest_fingerprint=project_manifest_fingerprint,
        ),
        violations=tuple(violations),
    )


def _strict_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def fingerprint_command(command: tuple[str, ...]) -> str:
    canonical = json.dumps(list(command), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def fingerprint_runtime(runtime: str) -> str:
    return f"sha256:{hashlib.sha256(runtime.encode('utf-8')).hexdigest()}"


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate policy field: {key}")
        result[key] = value
    return result


def _validate_tuple(value: object, name: str, allowed: frozenset[str]) -> None:
    if not isinstance(value, tuple) or not value or len(value) > 32:
        raise ValueError(f"{name} must be a non-empty tuple with at most 32 items")
    if any(not isinstance(item, str) or not item or len(item) > 128 for item in value):
        raise ValueError(f"{name} items must be non-empty strings up to 128 characters")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicate values")
    if any(item not in allowed for item in value):
        raise ValueError(f"{name} contains an unsupported value")


def _validate_fingerprint_tuple(value: object, name: str) -> None:
    if not isinstance(value, tuple) or len(value) > 32:
        raise ValueError(f"{name} must be a tuple with at most 32 items")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicate values")
    if any(
        not isinstance(item, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
        for item in value
    ):
        raise ValueError(f"{name} must contain sha256 fingerprints")


def _fingerprint_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    result = tuple(value)
    _validate_fingerprint_tuple(result, name)
    return result


def _string_tuple(
    value: object,
    name: str,
    *,
    allowed: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 32:
        raise ValueError(f"{name} must be a non-empty array with at most 32 items")
    if any(not isinstance(item, str) or not item or len(item) > 128 for item in value):
        raise ValueError(f"{name} items must be non-empty strings up to 128 characters")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicate values")
    if allowed is not None and any(item not in allowed for item in result):
        raise ValueError(f"{name} contains an unsupported value")
    return result

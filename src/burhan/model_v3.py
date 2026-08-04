"""ProofCertificateV3 — auditable proof certificate for Burhan Engine v3.

Links together all facts needed for external audit:

* Engine version and policy version
* Commit SHA of the project being repaired
* SHA-256 fingerprint of the error input
* The original error text (truncated if very long)
* The hypothesis that was accepted
* The patch that was applied
* Test results before and after
* The container image used for isolation
* SBOM fields and provenance metadata

Design rules
------------
* Fully immutable (frozen dataclass).
* No external calls.
* JSON-serialisable via ``to_dict()``.
* Certificates are ASSUMED until the sandbox confirms pass→pass.
* No self-certification: a certificate cannot grant trust to itself.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Sub-components
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TestOutcome:
    # Suppress pytest collection of this dataclass (it is not a test class)
    __test__ = False
    """Result of running a test suite at one point in time."""

    runner: str          # "pytest" | "unittest" | "jest" | "vitest" | "typecheck"
    exit_code: int
    passed: int
    failed: int
    duration_ms: float
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner": self.runner,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "failed": self.failed,
            "duration_ms": round(self.duration_ms, 3),
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class SBOMEntry:
    """A single entry in the Software Bill of Materials."""

    name: str
    version: str
    ecosystem: str   # "pip" | "npm" | "docker"
    license_spdx: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "ecosystem": self.ecosystem,
            "license_spdx": self.license_spdx,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceV3:
    """Provenance metadata for a V3 certificate."""

    engine_version: str
    policy_version: str
    issued_at_utc: str      # ISO-8601 UTC timestamp
    issuer: str             # "burhan-engine"
    sbom: tuple[SBOMEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "policy_version": self.policy_version,
            "issued_at_utc": self.issued_at_utc,
            "issuer": self.issuer,
            "sbom": [s.to_dict() for s in self.sbom],
        }


# ---------------------------------------------------------------------------
# Main certificate
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ProofCertificateV3:
    """Auditable proof certificate tying all facts together.

    Attributes
    ----------
    certificate_id:
        UUID-like unique identifier for this certificate.
    status:
        "ASSUMED" until sandbox confirms; "VERIFIED" after pass→pass cycle;
        "REJECTED" if tests did not pass after patching.
    commit_sha:
        Git commit SHA of the project that was repaired.
    input_fingerprint:
        SHA-256 of the concatenated goal + error_text.
    original_error:
        First 2048 chars of the original error text.
    hypothesis_kind:
        The ``Hypothesis.kind`` of the accepted hypothesis.
    hypothesis_explanation:
        Short explanation from the hypothesis.
    patch_diff:
        Unified diff of the applied patch.
    patch_fingerprint:
        SHA-256 of the patch diff.
    test_before:
        Test outcome before the patch was applied.
    test_after:
        Test outcome after the patch was applied.
    container_image:
        Docker image reference (with digest) used for isolation.
    provenance:
        Engine metadata and SBOM.
    """

    certificate_id: str
    status: str          # "ASSUMED" | "VERIFIED" | "REJECTED"
    commit_sha: str
    input_fingerprint: str
    original_error: str
    hypothesis_kind: str
    hypothesis_explanation: str
    patch_diff: str
    patch_fingerprint: str
    test_before: TestOutcome | None
    test_after: TestOutcome | None
    container_image: str
    provenance: ProvenanceV3

    def __post_init__(self) -> None:
        if self.status not in {"ASSUMED", "VERIFIED", "REJECTED"}:
            raise ValueError(
                f"ProofCertificateV3 status must be ASSUMED/VERIFIED/REJECTED, "
                f"got {self.status!r}"
            )

    def is_verified(self) -> bool:
        """Return True only when status is VERIFIED."""
        return self.status == "VERIFIED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "certificate_id": self.certificate_id,
            "status": self.status,
            "commit_sha": self.commit_sha,
            "input_fingerprint": self.input_fingerprint,
            "original_error": self.original_error,
            "hypothesis_kind": self.hypothesis_kind,
            "hypothesis_explanation": self.hypothesis_explanation,
            "patch_diff": self.patch_diff,
            "patch_fingerprint": self.patch_fingerprint,
            "test_before": self.test_before.to_dict() if self.test_before else None,
            "test_after": self.test_after.to_dict() if self.test_after else None,
            "container_image": self.container_image,
            "provenance": self.provenance.to_dict(),
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Return a stable JSON representation."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_input_fingerprint(goal: str, error_text: str) -> str:
    """Return SHA-256 of goal + error_text."""
    combined = f"{goal}\x00{error_text}".encode("utf-8", errors="replace")
    return hashlib.sha256(combined).hexdigest()


def compute_patch_fingerprint(patch_diff: str) -> str:
    """Return SHA-256 of the patch diff."""
    return hashlib.sha256(patch_diff.encode("utf-8", errors="replace")).hexdigest()


def promote_to_verified(cert: ProofCertificateV3) -> ProofCertificateV3:
    """Return a new certificate with status=VERIFIED.

    Requires that test_after is present and exit_code == 0.
    Raises ``ValueError`` if conditions are not met.
    """
    if cert.test_after is None:
        raise ValueError("Cannot verify: test_after is not set")
    if cert.test_after.exit_code != 0:
        raise ValueError(
            f"Cannot verify: test_after exit_code={cert.test_after.exit_code} (non-zero)"
        )
    if cert.test_before is not None and cert.test_before.exit_code == 0:
        raise ValueError(
            "Cannot verify: test_before already passed (no fail→pass transition)"
        )
    # Return a new immutable certificate — do not mutate the original
    from dataclasses import replace
    return replace(cert, status="VERIFIED")

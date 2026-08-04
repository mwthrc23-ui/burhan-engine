"""Tests: fake proof promotion attempts must be rejected (Phase security)."""
from __future__ import annotations

import dataclasses
import pytest

from burhan.model_v3 import (
    ProofCertificateV3,
    ProvenanceV3,
    TestOutcome,
    compute_input_fingerprint,
    compute_patch_fingerprint,
    promote_to_verified,
)


def _provenance() -> ProvenanceV3:
    return ProvenanceV3(
        engine_version="0.9.0",
        policy_version="v3",
        issued_at_utc="2026-08-04T00:00:00Z",
        issuer="burhan-engine",
        sbom=(),
    )


class TestFakeProofPromotion:
    def test_self_verified_cert_rejected(self) -> None:
        """A cert claiming VERIFIED status without going through promote_to_verified
        is still immutable but is_verified() returns True — the gate is that
        promote_to_verified enforces the fail→pass requirement.

        This test verifies that manually constructing a VERIFIED cert with
        passing tests before (no transition) is caught by promote_to_verified.
        """
        # Build a cert where before already passes — no fail→pass transition
        before = TestOutcome("pytest", exit_code=0, passed=5, failed=0, duration_ms=10.0, summary="ok")
        after = TestOutcome("pytest", exit_code=0, passed=5, failed=0, duration_ms=10.0, summary="ok")
        cert = ProofCertificateV3(
            certificate_id="fake",
            status="ASSUMED",
            commit_sha="abc",
            input_fingerprint=compute_input_fingerprint("g", "e"),
            original_error="Error",
            hypothesis_kind="name_error",
            hypothesis_explanation="x",
            patch_diff="diff",
            patch_fingerprint=compute_patch_fingerprint("diff"),
            test_before=before,
            test_after=after,
            container_image="img",
            provenance=_provenance(),
        )
        with pytest.raises(ValueError, match="already passed"):
            promote_to_verified(cert)

    def test_no_test_after_cannot_promote(self) -> None:
        cert = ProofCertificateV3(
            certificate_id="no-after",
            status="ASSUMED",
            commit_sha="abc",
            input_fingerprint=compute_input_fingerprint("g", "e"),
            original_error="Error",
            hypothesis_kind="name_error",
            hypothesis_explanation="x",
            patch_diff="diff",
            patch_fingerprint=compute_patch_fingerprint("diff"),
            test_before=None,
            test_after=None,
            container_image="img",
            provenance=_provenance(),
        )
        with pytest.raises(ValueError, match="test_after is not set"):
            promote_to_verified(cert)

    def test_forged_verified_status_is_still_immutable(self) -> None:
        """Direct mutation via normal attribute assignment on a frozen dataclass must raise."""
        cert = ProofCertificateV3(
            certificate_id="forged",
            status="ASSUMED",
            commit_sha="",
            input_fingerprint=compute_input_fingerprint("a", "b"),
            original_error="",
            hypothesis_kind="name_error",
            hypothesis_explanation="",
            patch_diff="",
            patch_fingerprint=compute_patch_fingerprint(""),
            test_before=None,
            test_after=None,
            container_image="",
            provenance=_provenance(),
        )
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            cert.status = "VERIFIED"  # type: ignore[misc]

    def test_empty_patch_diff_can_still_be_assumed(self) -> None:
        cert = ProofCertificateV3(
            certificate_id="empty-patch",
            status="ASSUMED",
            commit_sha="",
            input_fingerprint=compute_input_fingerprint("a", "b"),
            original_error="",
            hypothesis_kind="name_error",
            hypothesis_explanation="",
            patch_diff="",
            patch_fingerprint=compute_patch_fingerprint(""),
            test_before=None,
            test_after=None,
            container_image="",
            provenance=_provenance(),
        )
        assert cert.status == "ASSUMED"
        assert not cert.is_verified()

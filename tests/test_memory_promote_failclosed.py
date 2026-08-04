"""Tests for memory promotion fail-closed policy (Phase 6)."""
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


def _verified_cert() -> ProofCertificateV3:
    before = TestOutcome(runner="pytest", exit_code=1, passed=0, failed=1, duration_ms=50.0, summary="1 failed")
    after = TestOutcome(runner="pytest", exit_code=0, passed=1, failed=0, duration_ms=45.0, summary="1 passed")
    cert = ProofCertificateV3(
        certificate_id="cert-v",
        status="ASSUMED",
        commit_sha="deadbeef",
        input_fingerprint=compute_input_fingerprint("g", "e"),
        original_error="NameError: 'x'",
        hypothesis_kind="name_error",
        hypothesis_explanation="x not defined",
        patch_diff="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y",
        patch_fingerprint=compute_patch_fingerprint("p"),
        test_before=before,
        test_after=after,
        container_image="python@sha256:" + "f" * 64,
        provenance=_provenance(),
    )
    return promote_to_verified(cert)


class TestMemoryPromotePolicy:
    """Verify fail-closed promotion rules via ProofCertificateV3."""

    def test_verified_cert_is_verified(self) -> None:
        cert = _verified_cert()
        assert cert.is_verified()

    def test_assumed_cert_is_not_verified(self) -> None:
        cert = ProofCertificateV3(
            certificate_id="x",
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
        assert not cert.is_verified()

    def test_self_promotion_is_rejected(self) -> None:
        """A certificate cannot promote itself: promote_to_verified checks conditions."""
        cert = ProofCertificateV3(
            certificate_id="self",
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
        # No test_after means it cannot be promoted
        with pytest.raises(ValueError, match="test_after is not set"):
            promote_to_verified(cert)

    def test_already_passing_before_rejected(self) -> None:
        before = TestOutcome(runner="pytest", exit_code=0, passed=1, failed=0, duration_ms=10.0, summary="ok")
        after = TestOutcome(runner="pytest", exit_code=0, passed=1, failed=0, duration_ms=10.0, summary="ok")
        cert = ProofCertificateV3(
            certificate_id="x",
            status="ASSUMED",
            commit_sha="",
            input_fingerprint=compute_input_fingerprint("a", "b"),
            original_error="",
            hypothesis_kind="name_error",
            hypothesis_explanation="",
            patch_diff="",
            patch_fingerprint=compute_patch_fingerprint(""),
            test_before=before,
            test_after=after,
            container_image="",
            provenance=_provenance(),
        )
        with pytest.raises(ValueError, match="already passed"):
            promote_to_verified(cert)

    def test_failing_after_is_rejected(self) -> None:
        before = TestOutcome(runner="pytest", exit_code=1, passed=0, failed=1, duration_ms=10.0, summary="fail")
        after = TestOutcome(runner="pytest", exit_code=1, passed=0, failed=1, duration_ms=10.0, summary="fail")
        cert = ProofCertificateV3(
            certificate_id="x",
            status="ASSUMED",
            commit_sha="",
            input_fingerprint=compute_input_fingerprint("a", "b"),
            original_error="",
            hypothesis_kind="name_error",
            hypothesis_explanation="",
            patch_diff="",
            patch_fingerprint=compute_patch_fingerprint(""),
            test_before=before,
            test_after=after,
            container_image="",
            provenance=_provenance(),
        )
        with pytest.raises(ValueError, match="exit_code=1"):
            promote_to_verified(cert)

    def test_verified_cert_is_immutable(self) -> None:
        cert = _verified_cert()
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            cert.status = "ASSUMED"  # type: ignore[misc]

    def test_promotion_does_not_change_other_fields(self) -> None:
        cert_assumed = ProofCertificateV3(
            certificate_id="keep",
            status="ASSUMED",
            commit_sha="sha1",
            input_fingerprint=compute_input_fingerprint("g", "e"),
            original_error="NameError: 'x'",
            hypothesis_kind="name_error",
            hypothesis_explanation="missing",
            patch_diff="diff",
            patch_fingerprint=compute_patch_fingerprint("diff"),
            test_before=TestOutcome("pytest", 1, 0, 1, 10.0, "fail"),
            test_after=TestOutcome("pytest", 0, 1, 0, 10.0, "pass"),
            container_image="img",
            provenance=_provenance(),
        )
        verified = promote_to_verified(cert_assumed)
        assert verified.certificate_id == cert_assumed.certificate_id
        assert verified.commit_sha == cert_assumed.commit_sha
        assert verified.hypothesis_kind == cert_assumed.hypothesis_kind

"""Tests: candidate passes narrow test but fails full regression (Phase security)."""
from __future__ import annotations

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
        engine_version="0.10.0",
        policy_version="v3",
        issued_at_utc="2026-08-04T00:00:00Z",
        issuer="burhan-engine",
        sbom=(),
    )


class TestNarrowVsRegression:
    def test_narrow_pass_full_fail_is_not_verified(self) -> None:
        """A candidate that passes only the narrow test must not be marked verified.

        The full-regression result has exit_code=1 (failed tests), so
        promote_to_verified must reject it.
        """
        # Narrow test: before=1 (fail), after=0 (pass)
        # Full regression: before=1 (fail), after=1 (fail) — regression detected
        before = TestOutcome("pytest", exit_code=1, passed=0, failed=1, duration_ms=10.0, summary="narrow fail")
        after_full_regression = TestOutcome("pytest", exit_code=1, passed=3, failed=2, duration_ms=100.0, summary="regression")

        cert = ProofCertificateV3(
            certificate_id="narrow-pass-full-fail",
            status="ASSUMED",
            commit_sha="abc",
            input_fingerprint=compute_input_fingerprint("g", "e"),
            original_error="Error",
            hypothesis_kind="name_error",
            hypothesis_explanation="x",
            patch_diff="diff",
            patch_fingerprint=compute_patch_fingerprint("diff"),
            test_before=before,
            test_after=after_full_regression,
            container_image="img",
            provenance=_provenance(),
        )
        # Promotion must fail because test_after has exit_code=1
        with pytest.raises(ValueError, match="exit_code=1"):
            promote_to_verified(cert)
        assert cert.status == "ASSUMED"

    def test_narrow_pass_full_pass_is_verified(self) -> None:
        """A candidate that passes both narrow and full regression is verified."""
        before = TestOutcome("pytest", exit_code=1, passed=0, failed=1, duration_ms=10.0, summary="fail")
        after = TestOutcome("pytest", exit_code=0, passed=5, failed=0, duration_ms=80.0, summary="all pass")
        cert = ProofCertificateV3(
            certificate_id="both-pass",
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
        verified = promote_to_verified(cert)
        assert verified.is_verified()

    def test_assumed_cert_cannot_be_used_as_proof(self) -> None:
        cert = ProofCertificateV3(
            certificate_id="assumed-only",
            status="ASSUMED",
            commit_sha="abc",
            input_fingerprint=compute_input_fingerprint("g", "e"),
            original_error="Error",
            hypothesis_kind="name_error",
            hypothesis_explanation="x",
            patch_diff="",
            patch_fingerprint=compute_patch_fingerprint(""),
            test_before=None,
            test_after=None,
            container_image="",
            provenance=_provenance(),
        )
        assert not cert.is_verified()

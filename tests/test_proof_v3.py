"""Tests for ProofCertificateV3 (Phase 5)."""
from __future__ import annotations

import json
import pytest

from burhan.model_v3 import (
    ProofCertificateV3,
    ProvenanceV3,
    SBOMEntry,
    TestOutcome,
    compute_input_fingerprint,
    compute_patch_fingerprint,
    promote_to_verified,
)


def _make_cert(
    status: str = "ASSUMED",
    test_before_exit: int | None = 1,
    test_after_exit: int | None = 0,
) -> ProofCertificateV3:
    provenance = ProvenanceV3(
        engine_version="0.10.0",
        policy_version="v3",
        issued_at_utc="2026-08-04T00:00:00Z",
        issuer="burhan-engine",
        sbom=(
            SBOMEntry(name="burhan-engine", version="0.10.0", ecosystem="pip", license_spdx="LicenseRef-Custom-Attribution"),
        ),
    )
    before = (
        TestOutcome(runner="pytest", exit_code=test_before_exit, passed=0, failed=1, duration_ms=100.0, summary="1 failed")
        if test_before_exit is not None
        else None
    )
    after = (
        TestOutcome(runner="pytest", exit_code=test_after_exit, passed=1, failed=0, duration_ms=80.0, summary="1 passed")
        if test_after_exit is not None
        else None
    )
    return ProofCertificateV3(
        certificate_id="cert-001",
        status=status,
        commit_sha="abc123",
        input_fingerprint=compute_input_fingerprint("goal", "error"),
        original_error="NameError: 'x'",
        hypothesis_kind="name_error",
        hypothesis_explanation="x was not defined",
        patch_diff="--- a.py\n+++ b.py\n@@ -1 +1 @@\n-x\n+y",
        patch_fingerprint=compute_patch_fingerprint("some diff"),
        test_before=before,
        test_after=after,
        container_image="python@sha256:" + "a" * 64,
        provenance=provenance,
    )


class TestProofCertificateV3:
    def test_assumed_status_valid(self) -> None:
        cert = _make_cert("ASSUMED")
        assert cert.status == "ASSUMED"
        assert not cert.is_verified()

    def test_verified_status_valid(self) -> None:
        cert = _make_cert("VERIFIED")
        assert cert.is_verified()

    def test_rejected_status_valid(self) -> None:
        cert = _make_cert("REJECTED")
        assert not cert.is_verified()

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be"):
            _make_cert("FAKE")

    def test_to_dict_has_all_fields(self) -> None:
        cert = _make_cert()
        d = cert.to_dict()
        required = {
            "certificate_id", "status", "commit_sha", "input_fingerprint",
            "original_error", "hypothesis_kind", "hypothesis_explanation",
            "patch_diff", "patch_fingerprint", "test_before", "test_after",
            "container_image", "provenance",
        }
        assert required <= set(d.keys())

    def test_to_json_is_valid_json(self) -> None:
        cert = _make_cert()
        js = cert.to_json()
        parsed = json.loads(js)
        assert parsed["certificate_id"] == "cert-001"

    def test_to_json_is_stable(self) -> None:
        cert = _make_cert()
        assert cert.to_json() == cert.to_json()

    def test_promote_to_verified_succeeds(self) -> None:
        cert = _make_cert("ASSUMED", test_before_exit=1, test_after_exit=0)
        verified = promote_to_verified(cert)
        assert verified.status == "VERIFIED"
        assert verified.certificate_id == cert.certificate_id

    def test_promote_does_not_mutate_original(self) -> None:
        cert = _make_cert("ASSUMED")
        promote_to_verified(cert)
        assert cert.status == "ASSUMED"

    def test_promote_fails_without_test_after(self) -> None:
        cert = _make_cert("ASSUMED", test_after_exit=None)
        with pytest.raises(ValueError, match="test_after is not set"):
            promote_to_verified(cert)

    def test_promote_fails_if_test_after_nonzero(self) -> None:
        cert = _make_cert("ASSUMED", test_before_exit=1, test_after_exit=1)
        with pytest.raises(ValueError, match="exit_code=1"):
            promote_to_verified(cert)

    def test_promote_fails_if_test_before_passed(self) -> None:
        cert = _make_cert("ASSUMED", test_before_exit=0, test_after_exit=0)
        with pytest.raises(ValueError, match="already passed"):
            promote_to_verified(cert)

    def test_input_fingerprint_deterministic(self) -> None:
        fp1 = compute_input_fingerprint("goal", "error")
        fp2 = compute_input_fingerprint("goal", "error")
        assert fp1 == fp2
        assert len(fp1) == 64

    def test_patch_fingerprint_deterministic(self) -> None:
        fp = compute_patch_fingerprint("diff")
        assert len(fp) == 64

    def test_different_inputs_different_fingerprints(self) -> None:
        fp1 = compute_input_fingerprint("a", "b")
        fp2 = compute_input_fingerprint("a", "c")
        assert fp1 != fp2

    def test_provenance_to_dict(self) -> None:
        cert = _make_cert()
        prov = cert.provenance.to_dict()
        assert prov["engine_version"] == "0.10.0"
        assert prov["policy_version"] == "v3"
        assert isinstance(prov["sbom"], list)

    def test_test_outcome_to_dict(self) -> None:
        t = TestOutcome(runner="pytest", exit_code=0, passed=5, failed=0, duration_ms=123.0, summary="ok")
        d = t.to_dict()
        assert d["runner"] == "pytest"
        assert d["exit_code"] == 0

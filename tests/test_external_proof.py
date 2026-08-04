from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from burhan.cli import _default_proof_args, main
from burhan.patcher import ProofInfrastructureError, ProofRunner


def _external_diff() -> str:
    return (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-VALUE = 1\n"
        "+VALUE = 2\n"
    )


def _project(root: Path) -> None:
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "check.py").write_text(
        "from app import VALUE\nraise SystemExit(0 if VALUE == 2 else 1)\n",
        encoding="utf-8",
    )


def test_external_patch_is_proved_only_in_the_temporary_copy(tmp_path: Path) -> None:
    _project(tmp_path)

    proof = ProofRunner().prove(
        tmp_path,
        None,
        external_patch=_external_diff(),
        test_program="python",
        test_args=("check.py",),
    )

    assert proof.verified is True
    assert proof.patch.changed_files == ("app.py",)
    assert proof.patch.verification.grade == "V0"
    assert "external_patch_validated" in proof.verification.checks
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_verify_patch_cli_accepts_tool_agnostic_unified_diff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _project(tmp_path)
    patch_file = tmp_path / "candidate.diff"
    patch_file.write_text(_external_diff(), encoding="utf-8")

    code = main(
        [
            "verify-patch",
            "--project",
            str(tmp_path),
            "--patch-file",
            str(patch_file),
            "--trust-local-tests",
            "--test-program",
            "python",
            "--test-arg",
            "check.py",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["proof"]["verified"] is True
    assert payload["proof"]["patch"]["artifact_hash"].startswith("sha256:")


def test_local_tsc_fails_closed_when_the_compiler_is_missing(tmp_path: Path) -> None:
    with patch("burhan.patcher.shutil.which", return_value=None):
        with pytest.raises(ProofInfrastructureError, match="tsc"):
            ProofRunner().prove(
                tmp_path,
                None,
                external_patch=_external_diff(),
                test_program="tsc",
                test_args=("--noEmit",),
            )


def test_tsc_proof_default_is_no_emit() -> None:
    assert _default_proof_args("tsc") == ("--noEmit",)

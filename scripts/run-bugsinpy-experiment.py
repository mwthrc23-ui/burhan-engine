"""Re-run Burhan's pinned BugsInPy external-patch experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from burhan.patcher import ProofRejected, ProofRunner


def _run_git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ("git",) + args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        timeout=60,
    )
    return result.stdout.strip()


def _run_git_bytes(*args: str, cwd: Path | None = None) -> bytes:
    result = subprocess.run(
        ("git",) + args,
        cwd=cwd,
        check=True,
        capture_output=True,
        shell=False,
        timeout=60,
    )
    return result.stdout


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new_output(path: Path, content: str) -> None:
    target = path.expanduser()
    if target.suffix.lower() != ".json" or not target.parent.is_dir():
        raise ValueError("output must be a new JSON file in an existing directory")
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported experiment manifest")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("experiment manifest must contain cases")
    runtime = payload.get("runtime_image")
    if not isinstance(runtime, str) or "@sha256:" not in runtime:
        raise ValueError("experiment runtime must be pinned by digest")
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("experiment case must be an object")
        if not isinstance(case.get("test_args"), list) or not case["test_args"]:
            raise ValueError("experiment case needs structured test_args")
        if set(case.get("sha256", {})) != {
            "bug.info",
            "bug_patch.txt",
            "run_test.sh",
        }:
            raise ValueError("experiment case needs all source hashes")
        fixture = case.get("fixed_test_fixture")
        if fixture is not None and (
            not isinstance(fixture, dict)
            or not isinstance(case.get("fixed_commit"), str)
            or set(fixture) != {"path", "sha256"}
        ):
            raise ValueError("fixed test fixture must pin its commit, path, and hash")
    return payload


def _validate_dataset(dataset_root: Path, manifest: dict[str, Any]) -> None:
    expected_commit = manifest["dataset"]["commit"]
    actual_commit = _run_git("rev-parse", "HEAD", cwd=dataset_root)
    if actual_commit != expected_commit:
        raise ValueError("BugsInPy checkout does not match the pinned commit")
    project = manifest["subject"]["project"]
    for case in manifest["cases"]:
        bundle = dataset_root / "projects" / project / "bugs" / case["bug_id"]
        for name, expected_hash in case["sha256"].items():
            path = bundle / name
            if _sha256(path) != expected_hash:
                raise ValueError(f"source hash mismatch for {case['id']}/{name}")
        declared = (bundle / "run_test.sh").read_text(encoding="utf-8").strip()
        if declared != case["declared_command"]:
            raise ValueError(f"declared command mismatch for {case['id']}")


def _install_fixed_test_fixture(checkout: Path, case: dict[str, Any]) -> None:
    fixture = case.get("fixed_test_fixture")
    if fixture is None:
        return
    relative = PurePosixPath(fixture["path"])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"unsafe fixed test path for {case['id']}")
    content = _run_git_bytes(
        "show",
        f"{case['fixed_commit']}:{relative.as_posix()}",
        cwd=checkout,
    )
    if hashlib.sha256(content).hexdigest() != fixture["sha256"]:
        raise ValueError(f"fixed test hash mismatch for {case['id']}")
    target = checkout.joinpath(*relative.parts)
    if target.is_symlink() or not target.is_file():
        raise ValueError(f"fixed test target is not a regular file for {case['id']}")
    target.write_bytes(content)


def run_experiment(
    *, manifest_path: Path, dataset_root: Path, subject_repository: Path
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    dataset_root = dataset_root.expanduser().resolve()
    subject_repository = subject_repository.expanduser().resolve()
    _validate_dataset(dataset_root, manifest)
    _run_git("rev-parse", "--is-inside-work-tree", cwd=subject_repository)

    project = manifest["subject"]["project"]
    results: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        bundle = dataset_root / "projects" / project / "bugs" / case["bug_id"]
        patch_text = (bundle / "bug_patch.txt").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="burhan-bugsinpy-") as directory:
            checkout = Path(directory) / "subject"
            _run_git(
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                str(subject_repository),
                str(checkout),
            )
            _run_git("checkout", "--detach", case["buggy_commit"], cwd=checkout)
            _install_fixed_test_fixture(checkout, case)
            try:
                proof = ProofRunner().prove(
                    checkout,
                    None,
                    external_patch=patch_text,
                    test_program="python",
                    test_args=tuple(case["test_args"]),
                    backend="docker",
                    docker_image=manifest["runtime_image"],
                    timeout_seconds=30,
                )
            except ProofRejected as error:
                reason = str(error)
                status = "already_passes" if "already passes" in reason else "rejected"
                results.append(
                    {
                        "id": case["id"],
                        "status": status,
                        "patch_verified": False,
                        "reason": reason,
                    }
                )
            else:
                results.append(
                    {
                        "id": case["id"],
                        "status": "fail_to_pass",
                        "patch_verified": proof.verified,
                        "proof_grade": proof.verification.grade,
                        "patch_sha256": proof.patch.artifact_hash,
                    }
                )

    negative_controls = sum(item["status"] == "already_passes" for item in results)
    false_positives = sum(
        item["status"] == "already_passes" and item["patch_verified"]
        for item in results
    )
    repair_eligible = sum(item["status"] != "already_passes" for item in results)
    patch_successes = sum(item["status"] == "fail_to_pass" for item in results)
    return {
        "schema_version": 1,
        "dataset": manifest["dataset"],
        "subject": manifest["subject"],
        "runtime_image": manifest["runtime_image"],
        "cases": results,
        "metrics": {
            "total_cases": len(results),
            "negative_controls": negative_controls,
            "false_positives": false_positives,
            "false_positive_rate": (
                false_positives / negative_controls if negative_controls else None
            ),
            "repair_eligible_cases": repair_eligible,
            "patch_successes": patch_successes,
            "patch_success_rate": (
                patch_successes / repair_eligible if repair_eligible else None
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--subject-repository", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_experiment(
        manifest_path=args.manifest,
        dataset_root=args.dataset_root,
        subject_repository=args.subject_repository,
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        _write_new_output(args.output, encoded)
    print(encoded, end="")
    return 0 if result["metrics"]["false_positives"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

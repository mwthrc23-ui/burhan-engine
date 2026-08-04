from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _experiment_module():
    project = Path(__file__).parents[1]
    path = project / "scripts" / "run-bugsinpy-experiment.py"
    spec = importlib.util.spec_from_file_location("burhan_bugsinpy_experiment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_pins_external_dataset_runtime_and_source_hashes() -> None:
    project = Path(__file__).parents[1]
    manifest = _experiment_module().load_manifest(
        project / "experiments" / "bugsinpy" / "manifest.json"
    )

    assert manifest["dataset"]["commit"] == (
        "11c5f1eea954a42132cfd06bf257766a7963e0fd"
    )
    assert manifest["runtime_image"].startswith("python@sha256:")
    assert len(manifest["cases"]) == 4
    assert all(len(case["sha256"]) == 3 for case in manifest["cases"])
    assert sum("fixed_test_fixture" in case for case in manifest["cases"]) == 1


def test_runner_never_executes_the_raw_bugsinpy_script() -> None:
    project = Path(__file__).parents[1]
    source = (
        project / "scripts" / "run-bugsinpy-experiment.py"
    ).read_text(encoding="utf-8")

    assert "shell=False" in source
    assert "run_test.sh" in source
    assert "subprocess.run(case[\"declared_command\"]" not in source


def test_experiment_output_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    module = _experiment_module()

    module._write_new_output(output, "{}\n")
    with pytest.raises(FileExistsError):
        module._write_new_output(output, "replaced\n")

    assert output.read_text(encoding="utf-8") == "{}\n"


def test_fixed_test_fixture_rejects_parent_traversal(tmp_path: Path) -> None:
    module = _experiment_module()
    case = {
        "id": "unsafe",
        "fixed_commit": "a" * 40,
        "fixed_test_fixture": {"path": "../outside.py", "sha256": "b" * 64},
    }

    with pytest.raises(ValueError, match="unsafe fixed test path"):
        module._install_fixed_test_fixture(tmp_path, case)


def test_checked_in_result_has_external_success_and_controls() -> None:
    project = Path(__file__).parents[1]
    result = json.loads(
        (project / "experiments" / "bugsinpy" / "results-v0.10.0.json").read_text(
            encoding="utf-8"
        )
    )

    assert result["metrics"]["patch_successes"] == 1
    assert result["metrics"]["patch_success_rate"] == 1.0
    assert result["metrics"]["negative_controls"] == 3
    assert result["metrics"]["false_positive_rate"] == 0.0

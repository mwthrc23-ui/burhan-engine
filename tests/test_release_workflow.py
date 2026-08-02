from __future__ import annotations

import re
import unittest
from pathlib import Path


class ReleaseWorkflowSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (
            Path(__file__).parents[1] / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

    def _job(self, name: str, next_name: str | None = None) -> str:
        end = rf"(?=^  {re.escape(next_name)}:|\Z)" if next_name else r"\Z"
        match = re.search(
            rf"^  {re.escape(name)}:\n(?P<body>.*?){end}",
            self.workflow,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, f"missing workflow job: {name}")
        return match.group(0)

    def test_publish_pypi_uses_job_scoped_oidc_only(self) -> None:
        job = self._job("publish-pypi", "publish-docker")
        self.assertIn("id-token: write", job)
        self.assertNotIn("PYPI_API_TOKEN", job)
        self.assertNotRegex(job, r"^\s+password:", "OIDC publishing must not fall back to a secret")

    def test_publish_jobs_run_only_for_version_tags(self) -> None:
        for name, next_name in (("publish-pypi", "publish-docker"), ("publish-docker", None)):
            with self.subTest(job=name):
                job = self._job(name, next_name)
                self.assertIn("github.event_name == 'push'", job)
                self.assertIn("startsWith(github.ref, 'refs/tags/v')", job)

    def test_release_validates_tag_against_package_version(self) -> None:
        build_job = self._job("build-distributions", "publish-pypi")
        self.assertIn("Verify tag matches package version", build_job)
        self.assertIn("pyproject.toml", build_job)
        self.assertIn("Verify release commit is on main", build_job)

    def test_docker_publish_waits_for_pypi(self) -> None:
        docker_job = self._job("publish-docker")
        self.assertRegex(docker_job, r"needs:\s*\[[^\]]*publish-pypi[^\]]*\]")

    def test_package_write_permission_is_docker_job_scoped(self) -> None:
        top_level = self.workflow.split("jobs:", maxsplit=1)[0]
        docker_job = self._job("publish-docker")
        self.assertNotIn("packages: write", top_level)
        self.assertIn("packages: write", docker_job)


if __name__ == "__main__":
    unittest.main()

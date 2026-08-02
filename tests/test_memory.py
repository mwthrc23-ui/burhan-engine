from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from burhan.memory import MemoryQuery, RepairEpisode, RepairMemory


def episode_payload(
    *,
    episode_id: str,
    attribute: str,
    replacement: str,
    title: str,
) -> dict[str, object]:
    return {
        "id": episode_id,
        "title": title,
        "domain": "python-pytest-attribute-error",
        "signature": {
            "error_kind": "attribute_error",
            "exception_type": "AttributeError",
            "normalized_message": "object has no attribute",
            "attribute_name": attribute,
        },
        "environment": {
            "language": "python",
            "test_framework": "pytest",
            "runtime_version": "3.12",
            "dependencies": ["demo-client"],
        },
        "root_cause": f"The API renamed {attribute} to {replacement}.",
        "patch_pattern": {
            "kind": "rename_attribute",
            "from": attribute,
            "to": replacement,
        },
        "verification": {
            "grade": "V2",
            "reproduction_test": f"test_{attribute}_compatibility",
            "evidence": ["failing_before", "passing_after"],
        },
        "provenance": {
            "source_type": "synthetic",
            "repository_url": None,
            "issue_url": None,
            "pull_request_url": None,
            "commit_sha": None,
            "license_spdx": "MIT",
        },
        "tags": ["api-change", "attribute"],
    }


class RepairEpisodeTests(unittest.TestCase):
    def test_episode_requires_verification_evidence_for_v2(self) -> None:
        payload = episode_payload(
            episode_id="episode-send",
            attribute="send",
            replacement="send_message",
            title="Client send API rename",
        )
        payload["verification"] = {
            "grade": "V2",
            "reproduction_test": "test_send",
            "evidence": [],
        }

        with self.assertRaisesRegex(ValueError, "V2.*evidence"):
            RepairEpisode.from_dict(payload)

    def test_episode_rejects_a_domain_outside_the_first_mvp(self) -> None:
        payload = episode_payload(
            episode_id="episode-ts",
            attribute="send",
            replacement="sendMessage",
            title="TypeScript rename",
        )
        payload["domain"] = "typescript"

        with self.assertRaisesRegex(ValueError, "MVP domain"):
            RepairEpisode.from_dict(payload)


class RepairMemoryTests(unittest.TestCase):
    def test_search_ranks_exact_attribute_and_context_first(self) -> None:
        send = RepairEpisode.from_dict(
            episode_payload(
                episode_id="episode-send",
                attribute="send",
                replacement="send_message",
                title="Client send API rename",
            )
        )
        close = RepairEpisode.from_dict(
            episode_payload(
                episode_id="episode-close",
                attribute="close",
                replacement="shutdown",
                title="Client close API rename",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "repair-memory.sqlite3"
            memory = RepairMemory(database)
            memory.add(close)
            memory.add(send)

            matches = memory.search(
                MemoryQuery(
                    error_text="AttributeError: 'ApiClient' object has no attribute 'send'",
                    language="python",
                    test_framework="pytest",
                    dependencies=("demo-client",),
                )
            )

            reopened = RepairMemory(database)
            count = reopened.count()

        self.assertEqual(count, 2)
        self.assertEqual(matches[0].episode.id, "episode-send")
        self.assertGreater(matches[0].score, matches[1].score)
        self.assertIn("exact_attribute", matches[0].reasons)
        self.assertEqual(matches[0].episode.patch_pattern.to_value, "send_message")

    def test_unknown_error_returns_no_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = RepairMemory(Path(directory) / "memory.sqlite3")
            memory.add(
                RepairEpisode.from_dict(
                    episode_payload(
                        episode_id="episode-send",
                        attribute="send",
                        replacement="send_message",
                        title="Client send API rename",
                    )
                )
            )

            matches = memory.search(
                MemoryQuery("ValueError: invalid color", "python", "pytest")
            )

        self.assertEqual(matches, ())


if __name__ == "__main__":
    unittest.main()

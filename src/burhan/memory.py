from __future__ import annotations

import difflib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping


MVP_DOMAIN = "python-pytest-attribute-error"
ATTRIBUTE_ERROR = re.compile(
    r"AttributeError:\s+['\"](?P<object_type>[^'\"]+)['\"] object has no attribute ['\"](?P<attribute>[^'\"]+)['\"]"
)


@dataclass(frozen=True, slots=True)
class ErrorSignature:
    error_kind: str
    exception_type: str
    normalized_message: str
    attribute_name: str

    def to_dict(self) -> dict[str, str]:
        return {
            "error_kind": self.error_kind,
            "exception_type": self.exception_type,
            "normalized_message": self.normalized_message,
            "attribute_name": self.attribute_name,
        }


@dataclass(frozen=True, slots=True)
class EpisodeEnvironment:
    language: str
    test_framework: str
    runtime_version: str
    dependencies: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "test_framework": self.test_framework,
            "runtime_version": self.runtime_version,
            "dependencies": list(self.dependencies),
        }


@dataclass(frozen=True, slots=True)
class PatchPattern:
    kind: str
    from_value: str
    to_value: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "from": self.from_value, "to": self.to_value}


@dataclass(frozen=True, slots=True)
class EpisodeVerification:
    grade: str
    reproduction_test: str
    evidence: tuple[str, ...]
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "reproduction_test": self.reproduction_test,
            "evidence": list(self.evidence),
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
        }


@dataclass(frozen=True, slots=True)
class EpisodeProvenance:
    source_type: str
    repository_url: str | None
    issue_url: str | None
    pull_request_url: str | None
    commit_sha: str | None
    license_spdx: str | None
    dataset_name: str | None = None
    dataset_instance_id: str | None = None
    base_commit: str | None = None
    dataset_license_spdx: str | None = None
    upstream_license_spdx: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "repository_url": self.repository_url,
            "issue_url": self.issue_url,
            "pull_request_url": self.pull_request_url,
            "commit_sha": self.commit_sha,
            "license_spdx": self.license_spdx,
            "dataset_name": self.dataset_name,
            "dataset_instance_id": self.dataset_instance_id,
            "base_commit": self.base_commit,
            "dataset_license_spdx": self.dataset_license_spdx,
            "upstream_license_spdx": self.upstream_license_spdx,
        }


@dataclass(frozen=True, slots=True)
class RepairEpisode:
    id: str
    title: str
    domain: str
    signature: ErrorSignature
    environment: EpisodeEnvironment
    root_cause: str
    patch_pattern: PatchPattern
    verification: EpisodeVerification
    provenance: EpisodeProvenance
    tags: tuple[str, ...] = ()
    problem_description: str = ""
    solution_patch: str = ""
    test_patch: str = ""
    root_cause_status: str = "curated"

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RepairEpisode:
        episode_id = _required_text(value, "id")
        title = _required_text(value, "title")
        domain = _required_text(value, "domain")
        if domain != MVP_DOMAIN:
            raise ValueError(f"episode is outside the first MVP domain: {domain}")

        signature_value = _required_mapping(value, "signature")
        signature = ErrorSignature(
            error_kind=_required_text(signature_value, "error_kind"),
            exception_type=_required_text(signature_value, "exception_type"),
            normalized_message=_required_text(signature_value, "normalized_message"),
            attribute_name=_required_text(signature_value, "attribute_name"),
        )
        if signature.error_kind != "attribute_error" or signature.exception_type != "AttributeError":
            raise ValueError("MVP domain accepts AttributeError episodes only")

        environment_value = _required_mapping(value, "environment")
        environment = EpisodeEnvironment(
            language=_required_text(environment_value, "language").lower(),
            test_framework=_required_text(environment_value, "test_framework").lower(),
            runtime_version=_required_text(environment_value, "runtime_version"),
            dependencies=_text_tuple(environment_value.get("dependencies", ()), "dependencies"),
        )
        if environment.language != "python" or environment.test_framework != "pytest":
            raise ValueError("MVP domain requires Python and pytest")

        patch_value = _required_mapping(value, "patch_pattern")
        patch_pattern = PatchPattern(
            kind=_required_text(patch_value, "kind"),
            from_value=_required_text(patch_value, "from"),
            to_value=_required_text(patch_value, "to"),
        )

        verification_value = _required_mapping(value, "verification")
        verification = EpisodeVerification(
            grade=_required_text(verification_value, "grade"),
            reproduction_test=_required_text(verification_value, "reproduction_test"),
            evidence=_text_tuple(verification_value.get("evidence", ()), "verification.evidence"),
            fail_to_pass=_text_tuple(
                verification_value.get("fail_to_pass", ()),
                "verification.fail_to_pass",
            ),
            pass_to_pass=_text_tuple(
                verification_value.get("pass_to_pass", ()),
                "verification.pass_to_pass",
            ),
        )
        if verification.grade == "V2" and not verification.evidence:
            raise ValueError("V2 episode requires verification evidence")

        provenance_value = _required_mapping(value, "provenance")
        provenance = EpisodeProvenance(
            source_type=_required_text(provenance_value, "source_type"),
            repository_url=_optional_text(provenance_value.get("repository_url")),
            issue_url=_optional_text(provenance_value.get("issue_url")),
            pull_request_url=_optional_text(provenance_value.get("pull_request_url")),
            commit_sha=_optional_text(provenance_value.get("commit_sha")),
            license_spdx=_optional_text(provenance_value.get("license_spdx")),
            dataset_name=_optional_text(provenance_value.get("dataset_name")),
            dataset_instance_id=_optional_text(provenance_value.get("dataset_instance_id")),
            base_commit=_optional_text(provenance_value.get("base_commit")),
            dataset_license_spdx=_optional_text(
                provenance_value.get("dataset_license_spdx")
            ),
            upstream_license_spdx=_optional_text(
                provenance_value.get("upstream_license_spdx")
            ),
        )

        root_cause_status = _optional_text(value.get("root_cause_status")) or "curated"
        if root_cause_status not in {"unknown", "inferred", "source_asserted", "curated"}:
            raise ValueError("root_cause_status is not supported")

        return cls(
            id=episode_id,
            title=title,
            domain=domain,
            signature=signature,
            environment=environment,
            root_cause=_required_text(value, "root_cause"),
            patch_pattern=patch_pattern,
            verification=verification,
            provenance=provenance,
            tags=_text_tuple(value.get("tags", ()), "tags"),
            problem_description=_optional_text(value.get("problem_description")) or "",
            solution_patch=_optional_text(value.get("solution_patch")) or "",
            test_patch=_optional_text(value.get("test_patch")) or "",
            root_cause_status=root_cause_status,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "domain": self.domain,
            "signature": self.signature.to_dict(),
            "environment": self.environment.to_dict(),
            "root_cause": self.root_cause,
            "patch_pattern": self.patch_pattern.to_dict(),
            "verification": self.verification.to_dict(),
            "provenance": self.provenance.to_dict(),
            "tags": list(self.tags),
            "problem_description": self.problem_description,
            "solution_patch": self.solution_patch,
            "test_patch": self.test_patch,
            "root_cause_status": self.root_cause_status,
        }


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    error_text: str
    language: str
    test_framework: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryMatch:
    episode: RepairEpisode
    score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode.to_dict(),
            "score": self.score,
            "reasons": list(self.reasons),
        }


class RepairMemory:
    def __init__(self, database: Path) -> None:
        self._database = database.expanduser().resolve()
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(self, episode: RepairEpisode) -> None:
        if episode.root_cause_status not in {"curated", "source_asserted"}:
            raise ValueError(
                "verified repair memory requires a curated or source-asserted root cause"
            )
        payload = json.dumps(episode.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO repair_episodes (
                    id, domain, error_kind, attribute_name, language,
                    test_framework, verification_grade, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    domain = excluded.domain,
                    error_kind = excluded.error_kind,
                    attribute_name = excluded.attribute_name,
                    language = excluded.language,
                    test_framework = excluded.test_framework,
                    verification_grade = excluded.verification_grade,
                    payload_json = excluded.payload_json
                """,
                (
                    episode.id,
                    episode.domain,
                    episode.signature.error_kind,
                    episode.signature.attribute_name,
                    episode.environment.language,
                    episode.environment.test_framework,
                    episode.verification.grade,
                    payload,
                ),
            )

    def count(self) -> int:
        with self._session() as connection:
            row = connection.execute("SELECT COUNT(*) FROM repair_episodes").fetchone()
        return int(row[0])

    def search(self, query: MemoryQuery, *, limit: int = 5) -> tuple[MemoryMatch, ...]:
        if limit <= 0 or limit > 50:
            raise ValueError("search limit must be between 1 and 50")
        signature = _signature_from_error(query.error_text)
        if signature is None:
            return ()

        language = query.language.strip().lower()
        framework = query.test_framework.strip().lower()
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM repair_episodes
                WHERE domain = ? AND error_kind = ? AND language = ? AND test_framework = ?
                """,
                (MVP_DOMAIN, signature.error_kind, language, framework),
            ).fetchall()

        matches = [
            self._score(
                RepairEpisode.from_dict(json.loads(row[0])),
                signature,
                tuple(item.lower() for item in query.dependencies),
            )
            for row in rows
        ]
        matches.sort(key=lambda item: (-item.score, item.episode.id))
        return tuple(item for item in matches if item.score >= 0.45)[:limit]

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_episodes (
                    id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    error_kind TEXT NOT NULL,
                    attribute_name TEXT NOT NULL,
                    language TEXT NOT NULL,
                    test_framework TEXT NOT NULL,
                    verification_grade TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_repair_lookup
                ON repair_episodes(domain, error_kind, language, test_framework, attribute_name)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _score(
        episode: RepairEpisode,
        query_signature: ErrorSignature,
        query_dependencies: tuple[str, ...],
    ) -> MemoryMatch:
        score = 0.45
        reasons = ["same_error_kind", "same_python_pytest_domain"]
        if episode.signature.attribute_name == query_signature.attribute_name:
            score += 0.30
            reasons.append("exact_attribute")
        else:
            similarity = difflib.SequenceMatcher(
                None,
                episode.signature.attribute_name,
                query_signature.attribute_name,
            ).ratio()
            score += similarity * 0.12
            if similarity >= 0.65:
                reasons.append("similar_attribute")

        message_similarity = difflib.SequenceMatcher(
            None,
            episode.signature.normalized_message,
            query_signature.normalized_message,
        ).ratio()
        score += message_similarity * 0.10
        if message_similarity >= 0.8:
            reasons.append("similar_message_shape")

        episode_dependencies = {item.lower() for item in episode.environment.dependencies}
        query_dependency_set = set(query_dependencies)
        if episode_dependencies and query_dependency_set:
            overlap = len(episode_dependencies & query_dependency_set) / len(
                episode_dependencies | query_dependency_set
            )
            score += overlap * 0.15
            if overlap:
                reasons.append("dependency_overlap")

        return MemoryMatch(episode, round(min(score, 1.0), 4), tuple(reasons))


def _signature_from_error(error_text: str) -> ErrorSignature | None:
    match = ATTRIBUTE_ERROR.search(error_text)
    if not match:
        return None
    return ErrorSignature(
        error_kind="attribute_error",
        exception_type="AttributeError",
        normalized_message="object has no attribute",
        attribute_name=match.group("attribute"),
    )


def _required_mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"{key} must be an object")
    return item


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text field must be a string or null")
    return value.strip() or None


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array of strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} must contain non-empty strings")
    return tuple(str(item).strip() for item in value)

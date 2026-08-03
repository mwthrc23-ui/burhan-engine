from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .memory import (
    ATTRIBUTE_ERROR,
    MVP_DOMAIN,
    EpisodeEnvironment,
    EpisodeProvenance,
    EpisodeVerification,
    ErrorSignature,
    PatchPattern,
    RepairEpisode,
)


DEFAULT_ALLOWED_HOSTS = frozenset(
    {"datasets-server.huggingface.co", "api.github.com"}
)
_SAFE_PROJECT = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_BUG_ID = re.compile(r"^[0-9]+$")
_MAX_DESCRIPTION_BYTES = 2_000_000
_MAX_PATCH_BYTES = 5_000_000
_MAX_TEST_COMMAND_BYTES = 100_000

# ---------------------------------------------------------------------------
# Error-type patterns used for source record classification
# ---------------------------------------------------------------------------

_NAME_ERROR_PATTERN = re.compile(
    r"NameError:\s+name\s+['\"](?P<name>[^'\"]+)['\"]\s+is not defined"
)
_UNBOUND_ERROR_PATTERN = re.compile(
    r"UnboundLocalError:\s+(?:local variable|cannot access local variable)\s+['\"](?P<name>[^'\"]+)['\"]"
)
_TYPE_ERROR_PATTERN = re.compile(
    r"TypeError:\s+(?P<message>[^\n]+)"
)
_MODULE_ERROR_PATTERN = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s+(?:No module named\s+)?['\"]?(?P<name>[A-Za-z0-9_.]+)"
)
_IMPORT_NAME_PATTERN = re.compile(
    r"ImportError:\s+cannot import name\s+['\"](?P<name>[^'\"]+)['\"]"
    r"(?:\s+from\s+['\"](?P<module>[^'\"]+)['\"])?"
)
_KEY_ERROR_PATTERN = re.compile(
    r"KeyError:\s+'?(?P<name>[^'\n]+)'?"
)
_VALUE_ERROR_PATTERN = re.compile(
    r"ValueError:\s+(?P<message>[^\n]+)"
)
_INDEX_ERROR_PATTERN = re.compile(
    r"IndexError:\s+(?P<message>[^\n]+)"
)
_ZERO_DIV_PATTERN = re.compile(
    r"ZeroDivisionError:\s+(?P<message>[^\n]+)"
)
_RECURSION_PATTERN = re.compile(
    r"RecursionError:\s+(?P<message>[^\n]+)"
)
_FILE_NOT_FOUND_PATTERN = re.compile(
    r"FileNotFoundError:\s+(?:\[(?:Errno|WinError)\s+\d+\]\s+[^:]+:\s+)?"
    r"['\"](?P<path>[^'\"]+)['\"]"
)
_OS_ERROR_PATTERN = re.compile(
    r"(?:OSError|IOError):\s+\[(?:Errno|WinError)\s+(?P<errno>\d+)\]\s+"
    r"(?P<message>[^:\n]+)(?::\s+['\"](?P<path>[^'\"]+)['\"])?"
)

# Map (pattern, group, classification_status, error_kind)
_ERROR_CLASSIFIERS: tuple[
    tuple[re.Pattern[str], str, str, str], ...
] = (
    (ATTRIBUTE_ERROR,         "attribute", "attribute_error_candidate",   "attribute_error"),
    (_NAME_ERROR_PATTERN,     "name",      "name_error_candidate",        "name_error"),
    (_UNBOUND_ERROR_PATTERN,  "name",      "name_error_candidate",        "unbound_local_error"),
    (_TYPE_ERROR_PATTERN,     "message",   "type_error_candidate",        "type_error"),
    (_IMPORT_NAME_PATTERN,    "name",      "module_error_candidate",      "missing_import_name"),
    (_MODULE_ERROR_PATTERN,   "name",      "module_error_candidate",      "module_error"),
    (_KEY_ERROR_PATTERN,      "name",      "key_error_candidate",         "key_error"),
    (_VALUE_ERROR_PATTERN,    "message",   "value_error_candidate",       "value_error"),
    (_INDEX_ERROR_PATTERN,    "message",   "index_error_candidate",       "index_error"),
    (_ZERO_DIV_PATTERN,       "message",   "zero_div_candidate",          "zero_division_error"),
    (_RECURSION_PATTERN,      "message",   "recursion_candidate",         "recursion_error"),
    (_FILE_NOT_FOUND_PATTERN, "path",      "file_not_found_candidate",    "file_not_found_error"),
    (_OS_ERROR_PATTERN,       "errno",     "os_error_candidate",          "os_error"),
)


def _match_error(
    error_text: str,
) -> tuple[re.Match[str], str, str, str] | None:
    for pattern, group, status, kind in _ERROR_CLASSIFIERS:
        match = pattern.search(error_text)
        if match is None:
            continue
        try:
            token = match.group(group)
        except IndexError:
            continue
        if token:
            return match, token, status, kind
    return None


class _ValidatedRedirectHandler(HTTPRedirectHandler):
    max_redirections = 3

    def __init__(self, validator: Callable[[str], None]) -> None:
        super().__init__()
        self._validator = validator

    def redirect_request(
        self,
        request: Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        self._validator(new_url)
        return super().redirect_request(
            request, response, code, message, headers, new_url
        )
_SAFE_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SafeJsonClient:
    def __init__(
        self,
        *,
        allowed_hosts: set[str] | frozenset[str] = DEFAULT_ALLOWED_HOSTS,
        max_response_bytes: int = 8_000_000,
        timeout: float = 20.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("response size limit must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._max_response_bytes = max_response_bytes
        self._timeout = timeout
        self._opener = opener or build_opener(
            _ValidatedRedirectHandler(self._validate_url)
        ).open

    def get_json(self, url: str) -> Any:
        self._validate_url(url)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "Burhan/0.4",
            },
            method="GET",
        )
        with self._opener(request, timeout=self._timeout) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            self._validate_url(final_url)
            content_encoding = response.headers.get("Content-Encoding")
            if content_encoding and content_encoding.lower() != "identity":
                raise ValueError("compressed HTTP responses are not accepted")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > self._max_response_bytes:
                raise ValueError("response exceeds configured size limit")
            body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise ValueError("response exceeds configured size limit")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("response is not valid UTF-8 JSON") from error

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in self._allowed_hosts:
            raise ValueError("URL host is not in the allowed HTTPS host allowlist")
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("URL credentials and non-standard ports are not allowed")


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    classification_status: str
    problem_description: str | None
    root_cause: str | None
    root_cause_status: str
    error_text: str | None
    attribute_name: str | None
    solution_patch: str
    test_patch: str
    test_command: str
    provenance: Mapping[str, str | None]
    payload_sha256: str
    error_kind: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "classification_status": self.classification_status,
            "problem_description": self.problem_description,
            "root_cause": self.root_cause,
            "root_cause_status": self.root_cause_status,
            "error_text": self.error_text,
            "attribute_name": self.attribute_name,
            "solution_patch": self.solution_patch,
            "test_patch": self.test_patch,
            "test_command": self.test_command,
            "provenance": dict(self.provenance),
            "payload_sha256": self.payload_sha256,
            "error_kind": self.error_kind,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SourceRecord:
        provenance = value.get("provenance")
        if not isinstance(provenance, Mapping):
            raise ValueError("source provenance must be an object")
        return cls(
            source_id=_required_text(value, "source_id"),
            classification_status=_required_text(value, "classification_status"),
            problem_description=_optional_text(value.get("problem_description")),
            root_cause=_optional_text(value.get("root_cause")),
            root_cause_status=_optional_text(value.get("root_cause_status")) or "unknown",
            error_text=_optional_text(value.get("error_text")),
            attribute_name=_optional_text(value.get("attribute_name")),
            solution_patch=_optional_text(value.get("solution_patch")) or "",
            test_patch=_optional_text(value.get("test_patch")) or "",
            test_command=_optional_text(value.get("test_command")) or "",
            provenance=MappingProxyType(
                {str(key): _optional_text(item) for key, item in provenance.items()}
            ),
            payload_sha256=_required_text(value, "payload_sha256"),
            error_kind=_optional_text(value.get("error_kind")) or "unknown",
        )


@dataclass(frozen=True, slots=True)
class SourceMatch:
    record: SourceRecord
    score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "score": self.score,
            "reasons": list(self.reasons),
            "proposal_status": "source_candidate_not_locally_verified",
        }


class SourceStore:
    def __init__(self, database: Path) -> None:
        self._database = database.expanduser().resolve()
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(self, record: SourceRecord) -> bool:
        _validate_record_sizes(record)
        payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO source_record_versions (
                    source_id, payload_sha256, classification_status,
                    attribute_name, error_kind, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, payload_sha256) DO NOTHING
                """,
                (
                    record.source_id,
                    record.payload_sha256,
                    record.classification_status,
                    record.attribute_name,
                    record.error_kind,
                    payload,
                ),
            )
        return cursor.rowcount == 1

    def count(self) -> int:
        with self._session() as connection:
            row = connection.execute(
                "SELECT COUNT(DISTINCT source_id) FROM source_record_versions"
            ).fetchone()
        return int(row[0])

    def version_count(self) -> int:
        with self._session() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM source_record_versions"
            ).fetchone()
        return int(row[0])

    def search(self, error_text: str, *, limit: int = 5) -> tuple[SourceMatch, ...]:
        if limit <= 0 or limit > 50:
            raise ValueError("search limit must be between 1 and 50")

        # Identify the error kind and primary token from the error text
        classification = _match_error(error_text)
        if classification is None:
            return ()
        _match, matched_token, matched_status, matched_kind = classification

        with self._session() as connection:
            indexed_rows = connection.execute(
                """
                SELECT source_id, payload_sha256, attribute_name
                FROM source_record_versions
                WHERE rowid IN (
                    SELECT MAX(rowid) FROM source_record_versions GROUP BY source_id
                )
                AND classification_status = ?
                AND error_kind = ?
                ORDER BY CASE WHEN attribute_name = ? THEN 0 ELSE 1 END, source_id
                """,
                (matched_status, matched_kind, matched_token),
            ).fetchall()
            ranked: list[tuple[float, str, str, tuple[str, ...]]] = []
            for source_id, payload_sha256, candidate_token in indexed_rows:
                exact = candidate_token == matched_token
                similarity = difflib.SequenceMatcher(
                    None, candidate_token or "", matched_token
                ).ratio()
                if not exact and similarity < 0.65:
                    continue
                score = 0.9 if exact else round(0.55 + similarity * 0.2, 4)
                reasons = (
                    (f"same_{matched_kind}_token", "source_attested_dataset_case")
                    if exact
                    else (f"similar_{matched_kind}_token", "source_attested_dataset_case")
                )
                ranked.append((score, source_id, payload_sha256, reasons))
            ranked.sort(key=lambda item: (-item[0], item[1]))

            matches: list[SourceMatch] = []
            for score, source_id, payload_sha256, reasons in ranked[:limit]:
                row = connection.execute(
                    """
                    SELECT payload_json FROM source_record_versions
                    WHERE source_id = ? AND payload_sha256 = ?
                    """,
                    (source_id, payload_sha256),
                ).fetchone()
                if row is None:
                    continue
                record = SourceRecord.from_dict(json.loads(row[0]))
                if (
                    record.classification_status != matched_status
                    or record.attribute_name != candidate_token
                    or record.error_kind != matched_kind
                ):
                    record = replace(
                        record,
                        classification_status=matched_status,
                        attribute_name=candidate_token,
                        error_kind=matched_kind,
                    )
                matches.append(SourceMatch(record, score, reasons))
        return tuple(matches)

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS burhan_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO burhan_metadata(key, value)
                VALUES ('application', 'burhan')
                ON CONFLICT(key) DO NOTHING
                """
            )
            schema_row = connection.execute(
                "SELECT value FROM burhan_metadata WHERE key = 'source_schema_version'"
            ).fetchone()
            try:
                schema_version = int(schema_row[0]) if schema_row is not None else 0
            except (TypeError, ValueError):
                schema_version = 0
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_record_versions (
                    source_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    classification_status TEXT NOT NULL,
                    attribute_name TEXT,
                    error_kind TEXT NOT NULL DEFAULT 'unknown',
                    payload_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source_id, payload_sha256)
                )
                """
            )
            # Migrate existing tables that predate the error_kind column.
            try:
                connection.execute(
                    "ALTER TABLE source_record_versions ADD COLUMN error_kind TEXT NOT NULL DEFAULT 'unknown'"
                )
            except sqlite3.OperationalError:
                pass  # Column already exists
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_source_record_version_lookup
                ON source_record_versions(classification_status, attribute_name, source_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_source_record_error_kind
                ON source_record_versions(error_kind, source_id)
                """
            )
            if schema_version < 4:
                self._copy_legacy_records(connection)
                self._backfill_error_kinds(connection)
            connection.execute(
                """
                INSERT INTO burhan_metadata(key, value)
                VALUES ('source_schema_version', '4')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )

    @staticmethod
    def _copy_legacy_records(connection: sqlite3.Connection) -> None:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_records'"
        ).fetchone()
        if exists is None:
            return
        columns = {
            str(column[1])
            for column in connection.execute("PRAGMA table_info(source_records)").fetchall()
        }
        required = {
            "source_id",
            "payload_sha256",
            "classification_status",
            "attribute_name",
            "payload_json",
        }
        if not required.issubset(columns):
            return
        connection.execute(
            """
            INSERT OR IGNORE INTO source_record_versions (
                source_id, payload_sha256, classification_status,
                attribute_name, payload_json
            )
            SELECT source_id, payload_sha256, classification_status,
                attribute_name, payload_json
            FROM source_records
            """
        )

    @staticmethod
    def _backfill_error_kinds(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT rowid, classification_status, attribute_name, error_kind, payload_json
            FROM source_record_versions
            """
        ).fetchall()
        updates: list[tuple[str, str, str, int]] = []
        for rowid, current_status, current_token, current_kind, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            error_text = _optional_text(payload.get("error_text"))
            description = _optional_text(payload.get("problem_description"))
            classification = _match_error(description or "")
            if classification is None:
                classification = _match_error(error_text or "")
            if classification is None:
                continue
            _match, token, status, kind = classification
            if (current_status, current_token, current_kind) != (status, token, kind):
                updates.append((status, token, kind, int(rowid)))
        connection.executemany(
            """
            UPDATE source_record_versions
            SET classification_status = ?, attribute_name = ?, error_kind = ?
            WHERE rowid = ?
            """,
            updates,
        )

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=5.0)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class SweBenchVerifiedSource:
    DATASET_NAME = "SWE-bench_Verified"

    def __init__(self, client: SafeJsonClient | None = None) -> None:
        self._client = client or SafeJsonClient()

    def fetch(self, *, offset: int = 0, length: int = 100) -> tuple[Mapping[str, object], ...]:
        if offset < 0 or length <= 0 or length > 100:
            raise ValueError("offset must be non-negative and length must be between 1 and 100")
        query = urlencode(
            {
                "dataset": "princeton-nlp/SWE-bench_Verified",
                "config": "default",
                "split": "test",
                "offset": offset,
                "length": length,
            }
        )
        payload = self._client.get_json(
            f"https://datasets-server.huggingface.co/rows?{query}"
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("rows"), list):
            raise ValueError("SWE-bench response has an unexpected schema")
        rows: list[Mapping[str, object]] = []
        for item in payload["rows"]:
            if isinstance(item, Mapping) and isinstance(item.get("row"), Mapping):
                rows.append(item["row"])
        return tuple(rows)

    @staticmethod
    def to_record(row: Mapping[str, object]) -> SourceRecord:
        repo = _required_text(row, "repo")
        instance_id = _required_text(row, "instance_id")
        description = _required_raw_text(row, "problem_statement")
        solution_patch = _required_raw_text(row, "patch")
        test_patch = _required_raw_text(row, "test_patch")
        fail_to_pass = _json_text_tuple(row.get("FAIL_TO_PASS"), "FAIL_TO_PASS")
        framework_is_pytest = any("::" in item for item in fail_to_pass)

        error_match: re.Match[str] | None = None
        error_token: str | None = None
        classification = "unclassified"
        error_kind = "unknown"
        classified = _match_error(description) if framework_is_pytest else None
        if classified is not None:
            error_match, error_token, classification, error_kind = classified

        canonical = json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
        provenance = MappingProxyType(
            {
                "source_type": "swe-bench-verified",
                "dataset_name": SweBenchVerifiedSource.DATASET_NAME,
                "dataset_instance_id": instance_id,
                "repository_url": f"https://github.com/{repo}",
                "base_commit": _optional_text(row.get("base_commit")),
                "dataset_license_spdx": "MIT",
                "upstream_license_spdx": None,
            }
        )
        # For backward compat, attribute_name holds the primary error token
        # regardless of error type (attribute name, undefined name, etc.)
        attribute = (
            error_match.group("attribute")
            if error_match and error_kind == "attribute_error"
            else error_token
        )
        return SourceRecord(
            source_id=f"swebench:{instance_id}",
            classification_status=classification,
            problem_description=description,
            root_cause=None,
            root_cause_status="unknown",
            error_text=error_match.group(0) if error_match else None,
            attribute_name=attribute,
            solution_patch=solution_patch,
            test_patch=test_patch,
            test_command="\n".join(fail_to_pass),
            provenance=provenance,
            payload_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            error_kind=error_kind,
        )

    @staticmethod
    def to_episode(row: Mapping[str, object]) -> RepairEpisode:
        record = SweBenchVerifiedSource.to_record(row)
        if record.classification_status != "attribute_error_candidate":
            raise ValueError("SWE-bench row is outside the Python pytest AttributeError MVP")
        repo = _required_text(row, "repo")
        instance_id = _required_text(row, "instance_id")
        fail_to_pass = _json_text_tuple(row.get("FAIL_TO_PASS"), "FAIL_TO_PASS")
        pass_to_pass = _json_text_tuple(row.get("PASS_TO_PASS"), "PASS_TO_PASS")
        attribute = record.attribute_name or "unknown"
        title = (record.problem_description or instance_id).splitlines()[0].strip()
        return RepairEpisode(
            id=f"swebench:{instance_id}",
            title=title,
            domain=MVP_DOMAIN,
            signature=ErrorSignature(
                error_kind="attribute_error",
                exception_type="AttributeError",
                normalized_message="object has no attribute",
                attribute_name=attribute,
            ),
            environment=EpisodeEnvironment(
                language="python",
                test_framework="pytest",
                runtime_version="unspecified",
                dependencies=(repo.split("/", 1)[-1],),
            ),
            root_cause="غير مصنف في المصدر؛ يحتاج مراجعة بشرية.",
            patch_pattern=PatchPattern(
                kind="dataset_gold_patch",
                from_value=attribute,
                to_value="راجع solution_patch واختبره في بيئة معزولة",
            ),
            verification=EpisodeVerification(
                grade="SOURCE_ATTESTED",
                reproduction_test=fail_to_pass[0],
                evidence=("swe-bench-verified", "gold_patch", "test_patch"),
                fail_to_pass=fail_to_pass,
                pass_to_pass=pass_to_pass,
            ),
            provenance=EpisodeProvenance(
                source_type="swe-bench-verified",
                repository_url=f"https://github.com/{repo}",
                issue_url=None,
                pull_request_url=None,
                commit_sha=None,
                license_spdx=None,
                dataset_name=SweBenchVerifiedSource.DATASET_NAME,
                dataset_instance_id=instance_id,
                base_commit=_optional_text(row.get("base_commit")),
                dataset_license_spdx="MIT",
                upstream_license_spdx=None,
            ),
            tags=("swe-bench", "attribute-error", "source-candidate"),
            problem_description=record.problem_description or "",
            solution_patch=record.solution_patch,
            test_patch=record.test_patch,
            root_cause_status="unknown",
        )


class BugsInPySource:
    REPOSITORY = "soarsmu/BugsInPy"

    def __init__(self, client: SafeJsonClient | None = None) -> None:
        self._client = client or SafeJsonClient()

    def fetch(self, *, project: str, bug_id: str) -> SourceRecord:
        if not _valid_project(project) or not _SAFE_BUG_ID.fullmatch(bug_id):
            raise ValueError("project or bug id contains unsupported characters")
        files: dict[str, str] = {}
        for filename in ("bug.info", "bug_patch.txt", "run_test.sh"):
            path = "/".join(
                quote(part, safe="")
                for part in ("projects", project, "bugs", bug_id, filename)
            )
            payload = self._client.get_json(
                f"https://api.github.com/repos/{self.REPOSITORY}/contents/{path}"
            )
            if not isinstance(payload, Mapping) or payload.get("encoding") != "base64":
                raise ValueError(f"BugsInPy file {filename} has an unexpected schema")
            encoded = payload.get("content")
            if not isinstance(encoded, str):
                raise ValueError(f"BugsInPy file {filename} has no content")
            try:
                compact = "".join(encoded.split())
                files[filename] = base64.b64decode(compact, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as error:
                raise ValueError(f"BugsInPy file {filename} is not valid UTF-8 base64") from error
        return self.to_record(project=project, bug_id=bug_id, files=files)

    @staticmethod
    def to_record(
        *, project: str, bug_id: str, files: Mapping[str, str]
    ) -> SourceRecord:
        if not _valid_project(project) or not _SAFE_BUG_ID.fullmatch(bug_id):
            raise ValueError("project or bug id contains unsupported characters")
        info = _parse_shell_assignments(_required_text(files, "bug.info"))
        patch = _required_text(files, "bug_patch.txt")
        test_command = _required_text(files, "run_test.sh")
        canonical = json.dumps(dict(files), ensure_ascii=False, sort_keys=True)
        provenance = MappingProxyType(
            {
                "source_type": "bugsinpy",
                "repository_url": "https://github.com/soarsmu/BugsInPy",
                "project": project,
                "bug_id": bug_id,
                "buggy_commit": info.get("buggy_commit_id"),
                "fixed_commit": info.get("fixed_commit_id"),
                "dataset_license_spdx": None,
                "upstream_license_spdx": None,
            }
        )
        return SourceRecord(
            source_id=f"bugsinpy:{project}:{bug_id}",
            classification_status="unclassified",
            problem_description=None,
            root_cause=None,
            root_cause_status="unknown",
            error_text=None,
            attribute_name=None,
            solution_patch=patch,
            test_patch="",
            test_command=test_command,
            provenance=provenance,
            payload_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )


class GitHubPullRequestSource:
    def __init__(self, client: SafeJsonClient | None = None) -> None:
        self._client = client or SafeJsonClient()

    def fetch(self, *, repository: str, pull_number: str) -> SourceRecord:
        _validate_repository_pull(repository, pull_number)
        escaped_repository = "/".join(quote(part, safe="") for part in repository.split("/", 1))
        issue = self._client.get_json(
            f"https://api.github.com/repos/{escaped_repository}/issues/{pull_number}"
        )
        pull_request = self._client.get_json(
            f"https://api.github.com/repos/{escaped_repository}/pulls/{pull_number}"
        )
        files = self._client.get_json(
            f"https://api.github.com/repos/{escaped_repository}/pulls/{pull_number}/files?per_page=100"
        )
        if not isinstance(issue, Mapping):
            raise ValueError("GitHub issue response has an unexpected schema")
        if not isinstance(pull_request, Mapping):
            raise ValueError("GitHub pull request response has an unexpected schema")
        if not isinstance(files, list) or not all(isinstance(item, Mapping) for item in files):
            raise ValueError("GitHub pull request files response has an unexpected schema")
        return self.to_record(
            repository=repository,
            pull_number=pull_number,
            issue=issue,
            pull_request=pull_request,
            files=files,
        )

    @staticmethod
    def to_record(
        *,
        repository: str,
        pull_number: str,
        issue: Mapping[str, object],
        pull_request: Mapping[str, object],
        files: list[Mapping[str, object]],
    ) -> SourceRecord:
        _validate_repository_pull(repository, pull_number)
        title = _optional_text(issue.get("title")) or f"GitHub PR {pull_number}"
        body = _optional_text(issue.get("body")) or ""
        description = f"{title}\n\n{body}".strip()

        error_match_obj: re.Match[str] | None = None
        error_token: str | None = None
        error_kind = "unknown"
        classified = _match_error(description)
        if classified is not None:
            error_match_obj, error_token, _status, error_kind = classified

        # For attribute_error, keep the original "attribute" group; otherwise use error_token
        attribute = (
            error_match_obj.group("attribute")
            if error_match_obj and error_kind == "attribute_error"
            else error_token
        )

        solution_patch = _github_files_patch(files, include_tests=True)
        test_patch = _github_files_patch(files, include_tests=False)
        canonical = json.dumps(
            {
                "repository": repository,
                "pull_number": pull_number,
                "issue": dict(issue),
                "pull_request": dict(pull_request),
                "files": [dict(item) for item in files],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        pull_url = _optional_text(pull_request.get("html_url")) or (
            f"https://github.com/{repository}/pull/{pull_number}"
        )
        provenance = MappingProxyType(
            {
                "source_type": "github-pr",
                "repository_url": f"https://github.com/{repository}",
                "issue_url": _optional_text(issue.get("html_url")),
                "pull_request_url": pull_url,
                "commit_sha": _optional_text(pull_request.get("merge_commit_sha")),
                "dataset_license_spdx": None,
                "upstream_license_spdx": None,
            }
        )
        return SourceRecord(
            source_id=f"github-pr:{repository}:{pull_number}",
            classification_status="unclassified",
            problem_description=description,
            root_cause=None,
            root_cause_status="unknown",
            error_text=error_match_obj.group(0) if error_match_obj else None,
            attribute_name=attribute,
            solution_patch=solution_patch,
            test_patch=test_patch,
            test_command="",
            provenance=provenance,
            payload_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            error_kind=error_kind,
        )


def _parse_shell_assignments(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = raw_value.strip().strip('"').strip("'")
    return values


def _valid_project(project: str) -> bool:
    return bool(
        _SAFE_PROJECT.fullmatch(project)
        and project not in {".", ".."}
        and not project.startswith(".")
        and not project.endswith(".")
    )


def _validate_record_sizes(record: SourceRecord) -> None:
    fields = (
        ("problem description", record.problem_description or "", _MAX_DESCRIPTION_BYTES),
        ("solution patch", record.solution_patch, _MAX_PATCH_BYTES),
        ("test patch", record.test_patch, _MAX_PATCH_BYTES),
        ("test command", record.test_command, _MAX_TEST_COMMAND_BYTES),
    )
    for name, value, limit in fields:
        if len(value.encode("utf-8")) > limit:
            raise ValueError(f"{name} exceeds the configured size limit")


def _validate_repository_pull(repository: str, pull_number: str) -> None:
    parts = repository.split("/", 1)
    if (
        not _SAFE_REPOSITORY.fullmatch(repository)
        or len(parts) != 2
        or not all(_valid_project(part) for part in parts)
        or not _SAFE_BUG_ID.fullmatch(pull_number)
    ):
        raise ValueError("repository or pull number contains unsupported characters")


def _github_files_patch(
    files: list[Mapping[str, object]], *, include_tests: bool
) -> str:
    patches: list[str] = []
    for file_item in files:
        filename = _optional_text(file_item.get("filename"))
        patch = _optional_text(file_item.get("patch"))
        if filename is None or patch is None:
            continue
        is_test_file = bool(re.search(r"(^|/)(tests?|test)_|(^|/)tests?/", filename))
        if include_tests or is_test_file:
            patches.append(f"diff --git a/{filename} b/{filename}\n{patch}")
    return "\n\n".join(patches)


def _json_text_tuple(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{name} must be a JSON array") from error
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{name} must contain non-empty strings")
    return tuple(item.strip() for item in value)


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item.strip()


def _required_raw_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text field must be a string or null")
    return value.strip() or None

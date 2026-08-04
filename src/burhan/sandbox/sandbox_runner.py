"""Sandbox runner: validates Docker image constraints and run configuration.

This module does NOT execute Docker commands directly – that remains the
responsibility of ``patcher.ProofRunner``.  Instead it provides:

* ``SandboxConfig`` – immutable configuration for a sandboxed run.
* ``validate_sandbox_config`` – pre-flight checks that reject runs that
  violate security policies before a single Docker command is issued.
* ``SandboxPolicy`` – the set of rules enforced by validation.

Security rules enforced
-----------------------
* Docker image MUST be pinned by ``@sha256:<64-hex>`` digest.
* Network is disabled by default; must be explicitly opted in.
* The project directory is mounted read-only.
* Linux capabilities are dropped.
* Resource limits (CPU/memory/processes) are required.
* The image digest must match an allow-list if one is configured.
* Stale (cached) results from a previous project state are rejected.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PINNED_DIGEST = re.compile(
    r"(?=.{1,255}@sha256:)"
    r"(?:[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})/)?"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?"
    r"@sha256:[0-9a-f]{64}"
)


class SandboxViolation(ValueError):
    """Raised when a sandbox configuration violates a security policy."""


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Configurable sandbox security policy.

    Attributes
    ----------
    require_pinned_digest:
        Require ``image@sha256:<64-hex>`` format.
    require_network_disabled:
        Network must be off unless explicitly allowed.
    allow_network:
        Override to permit network access (CI use-cases only).
    require_read_only_mount:
        Source directory must be mounted read-only.
    require_capabilities_dropped:
        Linux capabilities must be dropped.
    require_resource_limits:
        CPU/memory limits must be set.
    allowed_digests:
        If non-empty, image digest must be in this set.
    max_timeout_seconds:
        Upper bound on per-run timeout.
    """

    require_pinned_digest: bool = True
    require_network_disabled: bool = True
    allow_network: bool = False
    require_read_only_mount: bool = True
    require_capabilities_dropped: bool = True
    require_resource_limits: bool = True
    allowed_digests: tuple[str, ...] = ()
    max_timeout_seconds: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_pinned_digest": self.require_pinned_digest,
            "require_network_disabled": self.require_network_disabled,
            "allow_network": self.allow_network,
            "require_read_only_mount": self.require_read_only_mount,
            "require_capabilities_dropped": self.require_capabilities_dropped,
            "require_resource_limits": self.require_resource_limits,
            "allowed_digests": list(self.allowed_digests),
            "max_timeout_seconds": self.max_timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class SandboxConfig:
    """Parameters for a single sandboxed test run.

    Attributes
    ----------
    image:
        Docker image reference (must be pinned by digest).
    command:
        Command to execute inside the container.
    project_root:
        Host path to the project directory.
    project_fingerprint:
        SHA-256 fingerprint of the project at the time config was created.
        Used to detect stale results.
    timeout_seconds:
        Per-run timeout.
    memory_mb:
        Memory limit in megabytes.
    cpu_quota:
        CPU quota (100000 = 1 CPU).
    network_disabled:
        Whether to disable container network access.
    read_only_mount:
        Whether to mount the source directory read-only.
    capabilities_dropped:
        Whether to drop Linux capabilities.
    """

    image: str
    command: tuple[str, ...]
    project_root: Path
    project_fingerprint: str
    timeout_seconds: int = 60
    memory_mb: int = 512
    cpu_quota: int = 100000
    network_disabled: bool = True
    read_only_mount: bool = True
    capabilities_dropped: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "command": list(self.command),
            "project_root": str(self.project_root),
            "project_fingerprint": self.project_fingerprint,
            "timeout_seconds": self.timeout_seconds,
            "memory_mb": self.memory_mb,
            "cpu_quota": self.cpu_quota,
            "network_disabled": self.network_disabled,
            "read_only_mount": self.read_only_mount,
            "capabilities_dropped": self.capabilities_dropped,
        }


def validate_sandbox_config(
    config: SandboxConfig,
    policy: SandboxPolicy | None = None,
) -> None:
    """Raise ``SandboxViolation`` if *config* violates *policy*.

    Parameters
    ----------
    config:
        The sandbox configuration to validate.
    policy:
        Security policy to apply.  Defaults to ``SandboxPolicy()`` (strictest).

    Raises
    ------
    SandboxViolation
        If any policy rule is violated.
    """
    if policy is None:
        policy = SandboxPolicy()

    # 1. Pinned digest
    if policy.require_pinned_digest and not _PINNED_DIGEST.fullmatch(config.image):
        raise SandboxViolation(
            f"صورة Docker غير مثبتة بـdigest: '{config.image}'. "
            "استخدم صيغة image@sha256:<64-hex>."
        )

    # 2. Allow-list
    if policy.allowed_digests:
        digest_part = config.image.split("@sha256:")[-1] if "@sha256:" in config.image else ""
        full_ref = config.image
        if full_ref not in policy.allowed_digests and digest_part not in policy.allowed_digests:
            raise SandboxViolation(
                f"الصورة '{config.image}' غير موجودة في قائمة الصور المسموح بها."
            )

    # 3. Network
    if policy.require_network_disabled and not policy.allow_network and not config.network_disabled:
        raise SandboxViolation(
            "الشبكة مفعّلة في الحاوية لكن السياسة تتطلب تعطيلها."
        )

    # 4. Read-only mount
    if policy.require_read_only_mount and not config.read_only_mount:
        raise SandboxViolation(
            "يجب تركيب مجلد المشروع بوضع القراءة فقط (read-only)."
        )

    # 5. Capabilities dropped
    if policy.require_capabilities_dropped and not config.capabilities_dropped:
        raise SandboxViolation(
            "يجب إسقاط صلاحيات Linux (capabilities) في الحاوية."
        )

    # 6. Resource limits
    if policy.require_resource_limits:
        if config.memory_mb <= 0:
            raise SandboxViolation("يجب تحديد حد للذاكرة (memory_mb > 0).")
        if config.cpu_quota <= 0:
            raise SandboxViolation("يجب تحديد حصة CPU (cpu_quota > 0).")

    # 7. Timeout
    if config.timeout_seconds <= 0 or config.timeout_seconds > policy.max_timeout_seconds:
        raise SandboxViolation(
            f"المهلة الزمنية يجب أن تكون بين 1 و{policy.max_timeout_seconds} ثانية."
        )


def fingerprint_project(project_root: Path) -> str:
    """Return a stable SHA-256 fingerprint of the project's source files.

    Only files tracked by the scanner (non-secret, non-excluded) contribute
    to the fingerprint.  This allows detecting project changes between
    analysis and proof runs.
    """
    digest = hashlib.sha256()
    try:
        paths = sorted(
            p for p in project_root.rglob("*")
            if p.is_file() and not _is_excluded(p, project_root)
        )
    except OSError:
        return "error:unreadable"

    for path in paths:
        try:
            content = path.read_bytes()
        except OSError:
            continue
        rel = str(path.relative_to(project_root))
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")

    return f"sha256:{digest.hexdigest()[:32]}"


def _is_excluded(path: Path, root: Path) -> bool:
    """Return True for paths that should be excluded from fingerprinting."""
    _EXCLUDED_DIRS = frozenset({
        ".git", ".hg", "__pycache__", ".venv", "venv", "node_modules",
        "dist", "build", ".next", ".turbo",
    })
    _SECRET_NAMES = frozenset({
        ".env", "credentials.json", "secrets.json", "id_rsa", "id_ed25519",
    })
    _SECRET_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx"})

    for part in path.relative_to(root).parts[:-1]:
        if part in _EXCLUDED_DIRS or part.startswith("."):
            return True
    name = path.name.lower()
    if name in _SECRET_NAMES or name.startswith(".env."):
        return True
    if path.suffix.lower() in _SECRET_SUFFIXES:
        return True
    return False

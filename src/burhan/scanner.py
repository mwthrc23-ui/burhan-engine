from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_EXTENSIONS = frozenset(
    {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".json", ".toml", ".yaml", ".yml", ".md"}
)
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
        ".next",
        ".turbo",
    }
)
SECRET_FILE_NAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "service-account.json",
        "id_rsa",
        "id_ed25519",
    }
)


def is_secret_file(path: Path) -> bool:
    name = path.name.lower()
    return name in SECRET_FILE_NAMES or name.startswith(".env.") or path.suffix.lower() in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
    }


@dataclass(frozen=True, slots=True)
class ScanLimits:
    max_files: int = 500
    max_file_bytes: int = 1_000_000
    max_total_bytes: int = 10_000_000

    def __post_init__(self) -> None:
        if self.max_files <= 0 or self.max_file_bytes <= 0 or self.max_total_bytes <= 0:
            raise ValueError("scan limits must be positive")


@dataclass(frozen=True, slots=True)
class SourceFile:
    relative_path: str
    content: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    root: Path
    files: tuple[SourceFile, ...]
    skipped_secret_files: int = 0
    skipped_oversized_files: int = 0
    truncated: bool = False

    @property
    def combined_text(self) -> str:
        return "\n".join(item.content for item in self.files)


class ProjectScanner:
    def __init__(self, limits: ScanLimits | None = None) -> None:
        self._limits = limits or ScanLimits()

    def scan(self, project: Path) -> ProjectSnapshot:
        root = project.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"project directory does not exist: {root}")

        files: list[SourceFile] = []
        skipped_secrets = 0
        skipped_oversized = 0
        total_bytes = 0
        truncated = False

        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = sorted(
                name for name in directories if name not in EXCLUDED_DIRECTORIES and not name.startswith(".")
            )
            for name in sorted(names):
                path = Path(current) / name
                if is_secret_file(path):
                    skipped_secrets += 1
                    continue
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS or path.is_symlink():
                    continue
                if len(files) >= self._limits.max_files:
                    truncated = True
                    break
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > self._limits.max_file_bytes:
                    skipped_oversized += 1
                    continue
                if total_bytes + size > self._limits.max_total_bytes:
                    truncated = True
                    break
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                relative = path.relative_to(root).as_posix()
                files.append(SourceFile(relative, content, size))
                total_bytes += size
            if truncated:
                break

        return ProjectSnapshot(
            root=root,
            files=tuple(files),
            skipped_secret_files=skipped_secrets,
            skipped_oversized_files=skipped_oversized,
            truncated=truncated,
        )

    @staticmethod
    def _is_secret(path: Path) -> bool:
        return is_secret_file(path)

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class BackupRequest:
    project_root: Path
    backup_root: Path
    requested_at: datetime


@dataclass(frozen=True)
class BackupResult:
    backup_file: Path
    included_tenants: tuple[str, ...]


@dataclass(frozen=True)
class RestoreRequest:
    project_root: Path
    backup_file: Path
    scratch_root: Path
    requested_at: datetime


@dataclass(frozen=True)
class RestoreResult:
    backup_id: str
    included_tenants: tuple[str, ...]


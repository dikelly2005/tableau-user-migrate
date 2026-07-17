# PORT AS-IS from: REFERENCE/tableau_cloud_user_rekey/models/mapping.py
# No changes needed — pure dataclasses with no external dependencies.

from dataclasses import dataclass, field
from typing import Optional, List, Set
from enum import Enum


class MappingStatus(Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


_TERMINAL_STATUSES = frozenset({
    MappingStatus.COMPLETED,
    MappingStatus.FAILED,
    MappingStatus.SKIPPED,
})


@dataclass
class UserInfo:
    user_id: str
    name: str
    site_role: str
    auth_setting: Optional[str] = None
    exists: bool = True
    email: Optional[str] = None
    full_name: Optional[str] = None
    domain_name: Optional[str] = None


@dataclass
class MappingState:
    old_username: str
    new_username: str
    status: MappingStatus = MappingStatus.PENDING
    old_user: Optional[UserInfo] = None
    new_user: Optional[UserInfo] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    completed_steps: Set[str] = field(default_factory=set)
    permissions_cloned: int = 0
    groups_cloned: int = 0
    content_reassigned: int = 0
    ux_artifacts_cloned: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.status = MappingStatus.FAILED

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    def mark_step_complete(self, step: str) -> None:
        self.completed_steps.add(step)

    def is_step_complete(self, step: str) -> bool:
        return step in self.completed_steps

    def mark_in_progress(self) -> None:
        if self.status == MappingStatus.PENDING:
            self.status = MappingStatus.IN_PROGRESS

    def mark_complete(self) -> None:
        if self.status is not MappingStatus.FAILED:
            self.status = MappingStatus.COMPLETED

    def mark_skipped(self, reason: str) -> None:
        self.status = MappingStatus.SKIPPED
        self.warnings.append(f"Skipped: {reason}")


@dataclass
class BatchResult:
    total: int
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    failed_mappings: List[MappingState] = field(default_factory=list)
    skipped_mappings: List[MappingState] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.total < 0:
            raise ValueError(f"BatchResult.total must be >= 0, got {self.total}")

    def add_success(self) -> None:
        self.succeeded += 1

    def add_failure(self, mapping: MappingState) -> None:
        self.failed += 1
        self.failed_mappings.append(mapping)

    def add_skipped(self, mapping: MappingState) -> None:
        self.skipped += 1
        self.skipped_mappings.append(mapping)

    @property
    def processed(self) -> int:
        return self.succeeded + self.failed + self.skipped

    @property
    def success_rate(self) -> float:
        if self.processed == 0:
            return 0.0
        return (self.succeeded / self.processed) * 100.0

    @property
    def completion_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.processed / self.total) * 100.0

    @property
    def has_failures(self) -> bool:
        return self.failed > 0

    def summary(self) -> str:
        return (
            f"Total: {self.total} | "
            f"Succeeded: {self.succeeded} | "
            f"Failed: {self.failed} | "
            f"Skipped: {self.skipped} | "
            f"Success Rate: {self.success_rate:.1f}%"
        )

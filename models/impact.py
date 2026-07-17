# PORT AS-IS from: REFERENCE/tableau_cloud_user_rekey/models/impact.py
# No changes needed — pure dataclasses with no external dependencies.

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum


class PermissionMode(Enum):
    ALLOW = "Allow"
    DENY = "Deny"


class PermissionScope(Enum):
    EXPLICIT = "explicit"
    DEFAULT = "default"
    INHERITED = "inherited"


class DiffStatus(Enum):
    WILL_BE_ADDED = "WILL_BE_ADDED"
    UNCHANGED = "UNCHANGED"
    WILL_BE_REMOVED = "WILL_BE_REMOVED"


@dataclass(eq=False)
class Permission:
    capability_name: str
    capability_mode: PermissionMode
    content_type: str
    content_id: str
    content_name: Optional[str] = None
    scope: PermissionScope = PermissionScope.EXPLICIT

    def key(self) -> str:
        return (
            f"{self.content_type}:{self.content_id}:"
            f"{self.capability_name}:{self.capability_mode.value}"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Permission):
            return NotImplemented
        return self.key() == other.key()

    def __hash__(self) -> int:
        return hash(self.key())

    def to_dict(self) -> dict:
        return {
            "capability_name": self.capability_name,
            "capability_mode": self.capability_mode.value,
            "content_type": self.content_type,
            "content_id": self.content_id,
            "content_name": self.content_name,
            "scope": self.scope.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Permission":
        return cls(
            capability_name=data["capability_name"],
            capability_mode=PermissionMode(data["capability_mode"]),
            content_type=data["content_type"],
            content_id=data["content_id"],
            content_name=data.get("content_name"),
            scope=PermissionScope(data.get("scope", PermissionScope.EXPLICIT.value)),
        )


@dataclass
class PermissionDiff:
    old_username: str
    new_username: str
    to_add: List[Permission] = field(default_factory=list)
    unchanged: List[Permission] = field(default_factory=list)
    to_remove: List[Permission] = field(default_factory=list)

    def add_permission(self, perm: Permission, status: DiffStatus) -> None:
        if status is DiffStatus.WILL_BE_ADDED:
            self.to_add.append(perm)
        elif status is DiffStatus.UNCHANGED:
            self.unchanged.append(perm)
        elif status is DiffStatus.WILL_BE_REMOVED:
            self.to_remove.append(perm)
        else:
            raise ValueError(f"Unknown DiffStatus: {status!r}")

    @property
    def total_changes(self) -> int:
        return len(self.to_add) + len(self.to_remove)

    def to_rows(self) -> List[dict]:
        rows = []
        for perm in self.to_add:
            rows.append({**perm.to_dict(), "diff_status": DiffStatus.WILL_BE_ADDED.value})
        for perm in self.unchanged:
            rows.append({**perm.to_dict(), "diff_status": DiffStatus.UNCHANGED.value})
        for perm in self.to_remove:
            rows.append({**perm.to_dict(), "diff_status": DiffStatus.WILL_BE_REMOVED.value})
        return rows


@dataclass
class GroupMembership:
    group_id: str
    group_name: str


@dataclass
class ContentItem:
    content_id: str
    content_type: str
    content_name: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None


@dataclass
class UXArtifact:
    artifact_id: str
    artifact_type: str
    content_id: Optional[str] = None
    content_type: Optional[str] = None
    content_name: Optional[str] = None
    details: Optional[Dict] = None


@dataclass
class UserImpact:
    old_username: str
    new_username: str
    old_user_exists: bool = False
    new_user_exists: bool = False
    permission_count: int = 0
    group_count: int = 0
    content_count: int = 0
    subscription_count: int = 0
    alert_count: int = 0
    favorite_count: int = 0
    custom_view_count: int = 0
    permissions: List[Permission] = field(default_factory=list)
    groups: List[GroupMembership] = field(default_factory=list)
    content: List[ContentItem] = field(default_factory=list)
    ux_artifacts: List[UXArtifact] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)

    def add_validation_error(self, error: str) -> None:
        self.validation_errors.append(error)

    def add_validation_warning(self, warning: str) -> None:
        self.validation_warnings.append(warning)

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors

    @property
    def total_impact(self) -> int:
        return (
            self.permission_count + self.group_count + self.content_count
            + self.subscription_count + self.alert_count
            + self.favorite_count + self.custom_view_count
        )

    @property
    def ux_artifact_count(self) -> int:
        return (
            self.subscription_count + self.alert_count
            + self.favorite_count + self.custom_view_count
        )


@dataclass
class ImpactAnalysis:
    user_impacts: List[UserImpact] = field(default_factory=list)

    @property
    def total_users(self) -> int:
        return len(self.user_impacts)

    @property
    def valid_users(self) -> int:
        return sum(1 for u in self.user_impacts if u.is_valid)

    @property
    def invalid_users(self) -> int:
        return self.total_users - self.valid_users

    @property
    def total_permissions(self) -> int:
        return sum(u.permission_count for u in self.user_impacts)

    @property
    def total_groups(self) -> int:
        return sum(u.group_count for u in self.user_impacts)

    @property
    def total_content(self) -> int:
        return sum(u.content_count for u in self.user_impacts)

    @property
    def total_ux_artifacts(self) -> int:
        return sum(u.ux_artifact_count for u in self.user_impacts)

    def invalid_user_impacts(self) -> List[UserImpact]:
        return [u for u in self.user_impacts if not u.is_valid]

    def summary(self) -> str:
        return (
            f"Users: {self.total_users} "
            f"({self.valid_users} valid, {self.invalid_users} invalid) | "
            f"Permissions: {self.total_permissions} | "
            f"Groups: {self.total_groups} | "
            f"Content: {self.total_content} | "
            f"UX Artifacts: {self.total_ux_artifacts}"
        )

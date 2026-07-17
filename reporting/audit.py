# Tableau migration audit logger with optional PII redaction
# Co-authored with CoCo
import hashlib
import json
import re
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from enum import Enum


class AuditAction(Enum):
    USER_LOOKUP = "USER_LOOKUP"
    USER_CREATE = "USER_CREATE"
    USER_REUSE = "USER_REUSE"
    USER_DEACTIVATE = "USER_DEACTIVATE"
    USER_UNLICENSE = "USER_UNLICENSE"

    CLONE_PERMISSION = "CLONE_PERMISSION"
    REMOVE_PERMISSION = "REMOVE_PERMISSION"
    CLONE_DEFAULT_PERMISSION = "CLONE_DEFAULT_PERMISSION"
    REMOVE_DEFAULT_PERMISSION = "REMOVE_DEFAULT_PERMISSION"

    ADD_TO_GROUP = "ADD_TO_GROUP"
    REMOVE_FROM_GROUP = "REMOVE_FROM_GROUP"

    REASSIGN_OWNERSHIP = "REASSIGN_OWNERSHIP"

    CLONE_SUBSCRIPTION = "CLONE_SUBSCRIPTION"
    REMOVE_SUBSCRIPTION = "REMOVE_SUBSCRIPTION"
    CLONE_ALERT = "CLONE_ALERT"
    REMOVE_ALERT = "REMOVE_ALERT"
    CLONE_FAVORITE = "CLONE_FAVORITE"
    REMOVE_FAVORITE = "REMOVE_FAVORITE"
    CLONE_CUSTOM_VIEW = "CLONE_CUSTOM_VIEW"
    REMOVE_CUSTOM_VIEW = "REMOVE_CUSTOM_VIEW"
    CLONE_COLLECTION = "CLONE_COLLECTION"
    REMOVE_COLLECTION = "REMOVE_COLLECTION"
    CLONE_PULSE_SUBSCRIPTION = "CLONE_PULSE_SUBSCRIPTION"
    REMOVE_PULSE_SUBSCRIPTION = "REMOVE_PULSE_SUBSCRIPTION"
    CLONE_PULSE_ALERT = "CLONE_PULSE_ALERT"
    REMOVE_PULSE_ALERT = "REMOVE_PULSE_ALERT"
    CLONE_WEBHOOK = "CLONE_WEBHOOK"
    REMOVE_WEBHOOK = "REMOVE_WEBHOOK"

    DRY_RUN_START = "DRY_RUN_START"
    DRY_RUN_COMPLETE = "DRY_RUN_COMPLETE"
    CLONE_START = "CLONE_START"
    CLONE_COMPLETE = "CLONE_COMPLETE"
    MIGRATE_START = "MIGRATE_START"
    MIGRATE_COMPLETE = "MIGRATE_COMPLETE"
    CLEANUP_START = "CLEANUP_START"
    CLEANUP_COMPLETE = "CLEANUP_COMPLETE"

    DEACTIVATE_USER = "DEACTIVATE_USER"
    ERROR = "ERROR"
    RETRY = "RETRY"


class AuditResult(Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    SKIPPED = "SKIPPED"
    WARNING = "WARNING"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"


_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def _hash_email(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()[:12]


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _EMAIL_PATTERN.sub(lambda m: _hash_email(m.group(0)), value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


class AuditLogger:
    _FIELD_ORDER = (
        "run_id",
        "timestamp_utc",
        "action",
        "result",
        "old_username",
        "new_username",
        "object_type",
        "object_name",
        "object_id",
        "error_message",
        "details",
    )

    def __init__(self, audit_file: Path, run_id: str, redact_pii: bool = False):
        self.audit_file = audit_file
        self.run_id = run_id
        self._redact_pii = redact_pii
        self._lock = threading.Lock()

        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        self.audit_file.touch()

        try:
            with open(self.audit_file, "a", encoding="utf-8"):
                pass
        except OSError as e:
            raise OSError(
                f"Audit log file is not writable: {self.audit_file}"
            ) from e

    def log(
        self,
        action: AuditAction,
        result: AuditResult,
        old_username: Optional[str] = None,
        new_username: Optional[str] = None,
        object_type: Optional[str] = None,
        object_name: Optional[str] = None,
        object_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        event: Dict[str, Any] = {
            "run_id": self.run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "action": action.value,
            "result": result.value,
        }

        optional = {
            "old_username": old_username,
            "new_username": new_username,
            "object_type": object_type,
            "object_name": object_name,
            "object_id": object_id,
            "error_message": error_message,
            "details": details,
        }
        for key, value in optional.items():
            if value is not None:
                event[key] = value

        if self._redact_pii:
            event = _redact_value(event)

        ordered = {k: event[k] for k in self._FIELD_ORDER if k in event}
        for k, v in event.items():
            if k not in ordered:
                ordered[k] = v

        line = json.dumps(ordered, ensure_ascii=False) + "\n"

        with self._lock:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(line)

    def log_success(
        self,
        action: AuditAction,
        old_username: Optional[str] = None,
        new_username: Optional[str] = None,
        object_type: Optional[str] = None,
        object_name: Optional[str] = None,
        object_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.log(
            action=action, result=AuditResult.SUCCESS,
            old_username=old_username, new_username=new_username,
            object_type=object_type, object_name=object_name,
            object_id=object_id, details=details,
        )

    def log_failure(
        self,
        action: AuditAction,
        error_message: str,
        old_username: Optional[str] = None,
        new_username: Optional[str] = None,
        object_type: Optional[str] = None,
        object_name: Optional[str] = None,
        object_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.log(
            action=action, result=AuditResult.FAILURE,
            old_username=old_username, new_username=new_username,
            object_type=object_type, object_name=object_name,
            object_id=object_id, details=details,
            error_message=error_message,
        )

    def log_skipped(
        self,
        action: AuditAction,
        reason: str,
        old_username: Optional[str] = None,
        new_username: Optional[str] = None,
        object_type: Optional[str] = None,
        object_name: Optional[str] = None,
        object_id: Optional[str] = None,
    ) -> None:
        self.log(
            action=action, result=AuditResult.SKIPPED,
            old_username=old_username, new_username=new_username,
            object_type=object_type, object_name=object_name,
            object_id=object_id,
            details={"reason": reason},
        )

    def log_warning(
        self,
        action: AuditAction,
        warning_message: str,
        old_username: Optional[str] = None,
        new_username: Optional[str] = None,
        object_type: Optional[str] = None,
        object_name: Optional[str] = None,
        object_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        merged_details = dict(details or {})
        merged_details["warning"] = warning_message
        self.log(
            action=action, result=AuditResult.WARNING,
            old_username=old_username, new_username=new_username,
            object_type=object_type, object_name=object_name,
            object_id=object_id, details=merged_details,
        )

    def log_retry(
        self,
        action: AuditAction,
        attempt: int,
        max_attempts: int,
        error_message: str,
        old_username: Optional[str] = None,
        new_username: Optional[str] = None,
        object_type: Optional[str] = None,
        object_name: Optional[str] = None,
    ) -> None:
        self.log(
            action=AuditAction.RETRY,
            result=AuditResult.RETRY_SCHEDULED,
            old_username=old_username, new_username=new_username,
            object_type=object_type, object_name=object_name,
            details={
                "original_action": action.value,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "error": error_message,
            },
        )

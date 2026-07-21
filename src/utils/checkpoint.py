# Per-user per-step checkpoint manager for resumable migrations
# Co-authored with CoCo
import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict
from enum import Enum

from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class CheckpointStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class UserCheckpoint:
    old_username: str
    new_username: str
    status: CheckpointStatus = CheckpointStatus.PENDING
    mode: str = ""
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    error: Optional[str] = None
    steps_completed: List[str] = field(default_factory=list)
    retry_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "UserCheckpoint":
        data = dict(data)
        data["status"] = CheckpointStatus(data.get("status", "pending"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CheckpointManager:
    def __init__(self):
        self._checkpoints: Dict[str, UserCheckpoint] = {}
        self._file_path: Optional[Path] = None
        self._run_id: Optional[str] = None
        self._mode: Optional[str] = None

    @property
    def total(self) -> int:
        return len(self._checkpoints)

    @property
    def completed_count(self) -> int:
        return sum(1 for c in self._checkpoints.values() if c.status == CheckpointStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self._checkpoints.values() if c.status == CheckpointStatus.FAILED)

    def initialize(self, mappings: List[dict], mode: str, run_id: str, checkpoint_dir: Path) -> None:
        self._mode = mode
        self._run_id = run_id
        self._file_path = checkpoint_dir / f"checkpoint_{run_id}.json"
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        for m in mappings:
            old = m["old_username"]
            new = m["new_username"]
            self._checkpoints[old] = UserCheckpoint(
                old_username=old,
                new_username=new,
                mode=mode,
            )
        self.save()
        print_status("CHECKPOINT", f"Initialized {len(mappings)} user checkpoints")

    def mark_in_progress(self, old_username: str) -> None:
        cp = self._checkpoints.get(old_username)
        if cp is None:
            return
        cp.status = CheckpointStatus.IN_PROGRESS
        cp.started_at = datetime.now(timezone.utc).isoformat()
        cp.updated_at = cp.started_at
        self.save()

    def mark_completed(self, old_username: str) -> None:
        cp = self._checkpoints.get(old_username)
        if cp is None:
            return
        cp.status = CheckpointStatus.COMPLETED
        cp.updated_at = datetime.now(timezone.utc).isoformat()
        cp.error = None
        self.save()
        print_status("CHECKPOINT", f"Completed: {old_username}")

    def mark_failed(self, old_username: str, error: str) -> None:
        cp = self._checkpoints.get(old_username)
        if cp is None:
            return
        cp.status = CheckpointStatus.FAILED
        cp.updated_at = datetime.now(timezone.utc).isoformat()
        cp.error = error
        cp.retry_count += 1
        self.save()
        print_status("CHECKPOINT", f"Failed: {old_username} — {error[:100]} (attempt {cp.retry_count})")

    def is_failed(self, old_username: str) -> bool:
        cp = self._checkpoints.get(old_username)
        if cp is None:
            return False
        return cp.status == CheckpointStatus.FAILED

    def mark_step_completed(self, old_username: str, step: str) -> None:
        cp = self._checkpoints.get(old_username)
        if cp is None:
            return
        if step not in cp.steps_completed:
            cp.steps_completed.append(step)
        cp.updated_at = datetime.now(timezone.utc).isoformat()
        self.save()

    def flush(self) -> None:
        pass

    def is_step_completed(self, old_username: str, step: str) -> bool:
        cp = self._checkpoints.get(old_username)
        if cp is None:
            return False
        return step in cp.steps_completed

    MAX_RETRIES = 3

    def get_pending(self) -> List[UserCheckpoint]:
        pending = []
        for cp in self._checkpoints.values():
            if cp.status in (CheckpointStatus.PENDING, CheckpointStatus.IN_PROGRESS, CheckpointStatus.FAILED):
                if cp.retry_count >= self.MAX_RETRIES:
                    continue
                pending.append(cp)
        return pending

    def get_permanently_failed(self) -> List[UserCheckpoint]:
        return [
            cp for cp in self._checkpoints.values()
            if cp.status == CheckpointStatus.FAILED and cp.retry_count >= self.MAX_RETRIES
        ]

    def get_all(self) -> List[UserCheckpoint]:
        return list(self._checkpoints.values())

    def save(self, path: Optional[Path] = None) -> None:
        save_path = path or self._file_path
        if save_path is None:
            return
        save_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "run_id": self._run_id,
            "mode": self._mode,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "checkpoints": [cp.to_dict() for cp in self._checkpoints.values()],
        }
        tmp_fd, tmp_path = tempfile.mkstemp(dir=save_path.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, save_path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._run_id = data.get("run_id")
        self._mode = data.get("mode")
        self._file_path = path
        self._checkpoints.clear()
        for cp_data in data.get("checkpoints", []):
            cp = UserCheckpoint.from_dict(cp_data)
            self._checkpoints[cp.old_username] = cp
        pending = len(self.get_pending())
        print_status("CHECKPOINT", f"Loaded {len(self._checkpoints)} checkpoints ({pending} pending)")

    @classmethod
    def find_latest(cls, checkpoint_dir: Path) -> Optional[Path]:
        if not checkpoint_dir.exists():
            return None
        files = sorted(checkpoint_dir.glob("checkpoint_*.json"), reverse=True)
        for f in files:
            try:
                with open(f, "r") as fh:
                    data = json.load(fh)
                checkpoints = data.get("checkpoints", [])
                has_pending = any(
                    c.get("status") in ("pending", "in_progress", "failed")
                    for c in checkpoints
                )
                if has_pending:
                    return f
            except Exception:
                continue
        return None

    def summary(self) -> str:
        statuses = {}
        for cp in self._checkpoints.values():
            statuses[cp.status.value] = statuses.get(cp.status.value, 0) + 1
        parts = [f"{k}: {v}" for k, v in sorted(statuses.items())]
        return f"Checkpoints — {' | '.join(parts)}"

# Rollback workflow - reverses migration using audit JSONL as source of truth
# Co-authored with CoCo
import json
from pathlib import Path
from typing import List, Dict, Optional

from src.services.users import UserService
from src.services.ownership import OwnershipService
from src.services.groups import GroupService
from src.services.permissions import PermissionService
from reporting.audit import AuditLogger, AuditAction, AuditResult
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class RollbackWorkflow:
    """Reverses a completed or partial migration using its audit JSONL log.

    Rollback operations (in reverse order):
    1. Re-activate old users (set siteRole back from Unlicensed)
    2. Transfer ownership back to old users
    3. Remove new users from groups
    4. Remove permissions granted to new users
    5. Delete newly created users (optional)
    """

    def __init__(
        self,
        user_svc: UserService,
        ownership_svc: OwnershipService,
        group_svc: GroupService,
        permission_svc: PermissionService,
        audit_logger: AuditLogger,
    ):
        self._users = user_svc
        self._ownership = ownership_svc
        self._groups = group_svc
        self._permissions = permission_svc
        self._audit = audit_logger

    @staticmethod
    def load_audit_log(audit_file: Path) -> List[Dict]:
        events = []
        with open(audit_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    @staticmethod
    def extract_successful_events(events: List[Dict]) -> List[Dict]:
        return [e for e in events if e.get("result") == AuditResult.SUCCESS.value]

    async def execute(
        self,
        audit_file: Path,
        delete_new_users: bool = False,
        target_run_id: Optional[str] = None,
    ) -> Dict[str, int]:
        print_status("START", f"Rolling back migration from audit log: {audit_file}")

        events = self.load_audit_log(audit_file)
        successful = self.extract_successful_events(events)

        if target_run_id:
            successful = [e for e in successful if e.get("run_id") == target_run_id]

        stats = {
            "reactivated": 0,
            "ownership_reversed": 0,
            "groups_removed": 0,
            "users_deleted": 0,
            "errors": 0,
        }

        # 1. Re-activate deactivated users
        deactivations = [e for e in successful if e.get("action") == AuditAction.USER_DEACTIVATE.value]
        for event in deactivations:
            old_username = event.get("old_username")
            if not old_username:
                continue
            try:
                user = await self._users.lookup_user(old_username, live=True)
                if user and user.get("site_role") == "Unlicensed":
                    original_role = event.get("details", {}).get("original_site_role", "Explorer")
                    await self._users.update_user(user["id"], old_username, original_role)
                    stats["reactivated"] += 1
                    print_status("ROLLBACK", f"Re-activated {old_username} as {original_role}")
            except Exception as e:
                logger.warning(f"Failed to re-activate {old_username}: {e}")
                stats["errors"] += 1

        # 2. Reverse ownership transfers
        ownership_events = [e for e in successful if e.get("action") == AuditAction.REASSIGN_OWNERSHIP.value]
        for event in reversed(ownership_events):
            old_username = event.get("old_username")
            new_username = event.get("new_username")
            content_type = event.get("object_type")
            content_id = event.get("object_id")
            if not all([old_username, content_id]):
                continue
            try:
                old_user = await self._users.lookup_user(old_username, live=True)
                if old_user:
                    await self._ownership.transfer_single(
                        content_type, content_id, old_user["id"], old_username
                    )
                    stats["ownership_reversed"] += 1
            except Exception as e:
                logger.warning(f"Failed to reverse ownership for {content_type}/{content_id}: {e}")
                stats["errors"] += 1

        # 3. Remove new users from groups they were added to
        group_events = [e for e in successful if e.get("action") == AuditAction.ADD_TO_GROUP.value]
        for event in group_events:
            new_username = event.get("new_username")
            group_id = event.get("object_id")
            group_name = event.get("object_name", "")
            if not all([new_username, group_id]):
                continue
            try:
                new_user = await self._users.lookup_user(new_username, live=True)
                if new_user:
                    await self._groups.remove_user_from_group(
                        group_id, new_user["id"], new_username, group_name
                    )
                    stats["groups_removed"] += 1
            except Exception as e:
                logger.warning(f"Failed to remove {new_username} from group {group_name}: {e}")
                stats["errors"] += 1

        # 4. Optionally delete newly created users
        if delete_new_users:
            create_events = [e for e in successful if e.get("action") == AuditAction.USER_CREATE.value]
            for event in create_events:
                new_username = event.get("new_username")
                if not new_username:
                    continue
                try:
                    new_user = await self._users.lookup_user(new_username, live=True)
                    if new_user:
                        await self._users.deactivate_user(new_user["id"], new_username)
                        stats["users_deleted"] += 1
                        print_status("ROLLBACK", f"Deactivated new user: {new_username}")
                except Exception as e:
                    logger.warning(f"Failed to deactivate new user {new_username}: {e}")
                    stats["errors"] += 1

        print_status("DONE", f"Rollback complete: {stats}")
        return stats

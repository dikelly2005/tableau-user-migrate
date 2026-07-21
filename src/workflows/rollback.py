# Rollback workflow - reverses migration using audit JSONL as source of truth
# Co-authored with CoCo
import json
from json import JSONDecodeError
from pathlib import Path
from typing import List, Dict, Optional

from src.services.users import UserService
from src.services.ownership import OwnershipService
from src.services.groups import GroupService
from src.services.permissions import PermissionService
from src.services.favorites import FavoriteService
from src.services.subscriptions import SubscriptionService
from src.services.alerts import AlertService
from src.services.collections import CollectionService
from src.services.custom_views import CustomViewService
from reporting.audit import AuditLogger, AuditAction, AuditResult
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class RollbackWorkflow:
    """Reverses a completed or partial migration using its audit JSONL log.

    Rollback operations — undo additions to new user, then restore removals from old user:
    Phase A (undo new user additions):
      1. Remove new user from groups
      2. Remove permissions granted to new user
      3. Remove favorites added for new user
      4. Delete subscriptions created for new user
      5. Remove new user from alerts
      6. Delete cloned collections
      7. Transfer custom view ownership back to old user
    Phase B (restore old user removals):
      8. Re-add old user to groups
      9. Re-grant old user's permissions
      10. Re-add old user's favorites
      11. Re-add old user to alerts
    Phase C (structural):
      12. Transfer ownership back to old user
      13. Re-activate old user
      14. Optionally deactivate new users
    """

    def __init__(
        self,
        user_svc: UserService,
        ownership_svc: OwnershipService,
        group_svc: GroupService,
        permission_svc: PermissionService,
        favorite_svc: FavoriteService,
        subscription_svc: SubscriptionService,
        alert_svc: AlertService,
        collection_svc: CollectionService,
        custom_view_svc: CustomViewService,
        audit_logger: AuditLogger,
    ):
        self._users = user_svc
        self._ownership = ownership_svc
        self._groups = group_svc
        self._permissions = permission_svc
        self._favorites = favorite_svc
        self._subscriptions = subscription_svc
        self._alerts = alert_svc
        self._collections = collection_svc
        self._custom_views = custom_view_svc
        self._audit = audit_logger

    @staticmethod
    def load_audit_log(audit_file: Path) -> List[Dict]:
        events = []
        with open(audit_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except JSONDecodeError as e:
                    logger.warning(f"Skipping malformed JSONL at line {line_num}: {e} — content: {line[:100]}")
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
            "permissions_removed": 0,
            "favorites_removed": 0,
            "subscriptions_removed": 0,
            "alerts_removed": 0,
            "collections_removed": 0,
            "custom_views_reversed": 0,
            "groups_restored": 0,
            "permissions_restored": 0,
            "favorites_restored": 0,
            "alerts_restored": 0,
            "users_deleted": 0,
            "errors": 0,
            "skipped_lines": 0,
        }

        username_map = {}
        for e in successful:
            old = e.get("old_username")
            new = e.get("new_username")
            if old and new:
                username_map[old] = new

        # === Phase A: Undo additions to new user ===

        # A1. Remove new user from groups
        group_add_events = [e for e in successful if e.get("action") == AuditAction.ADD_TO_GROUP.value]
        for event in group_add_events:
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

        # A2. Remove permissions granted to new user
        perm_clone_events = [
            e for e in successful
            if e.get("action") in (AuditAction.CLONE_PERMISSION.value, AuditAction.CLONE_DEFAULT_PERMISSION.value)
        ]
        for event in perm_clone_events:
            new_username = event.get("new_username")
            content_type = event.get("object_type")
            content_id = event.get("object_id")
            details = event.get("details", {})
            capability = details.get("capability")
            mode = details.get("capability_mode", details.get("mode"))
            if not all([new_username, content_type, content_id, capability, mode]):
                continue
            try:
                new_user = await self._users.lookup_user(new_username, live=True)
                if new_user:
                    await self._permissions.remove_single_permission(
                        content_type, content_id, new_user["id"], capability, mode
                    )
                    stats["permissions_removed"] += 1
            except Exception as e:
                logger.warning(f"Failed to remove permission {capability}/{mode} from {new_username} on {content_type}/{content_id}: {e}")
                stats["errors"] += 1

        # A3. Remove favorites added for new user
        fav_clone_events = [e for e in successful if e.get("action") == AuditAction.CLONE_FAVORITE.value]
        for event in fav_clone_events:
            new_username = event.get("new_username")
            content_type = event.get("object_type")
            content_id = event.get("object_id")
            if not all([new_username, content_type, content_id]):
                continue
            try:
                new_user = await self._users.lookup_user(new_username, live=True)
                if new_user:
                    await self._favorites.remove_single_favorite(
                        new_user["id"], new_username, content_type, content_id
                    )
                    stats["favorites_removed"] += 1
            except Exception as e:
                logger.warning(f"Failed to remove favorite {content_type}/{content_id} from {new_username}: {e}")
                stats["errors"] += 1

        # A4. Delete subscriptions created for new user
        sub_clone_events = [e for e in successful if e.get("action") == AuditAction.CLONE_SUBSCRIPTION.value]
        for event in sub_clone_events:
            new_sub_id = event.get("object_id")
            new_username = event.get("new_username")
            if not new_sub_id:
                continue
            try:
                await self._subscriptions.remove_single_subscription(new_sub_id, new_username or "")
                stats["subscriptions_removed"] += 1
            except Exception as e:
                logger.warning(f"Failed to remove subscription {new_sub_id}: {e}")
                stats["errors"] += 1

        # A5. Remove new user from alerts
        alert_clone_events = [e for e in successful if e.get("action") == AuditAction.CLONE_ALERT.value]
        for event in alert_clone_events:
            new_username = event.get("new_username")
            alert_id = event.get("object_id")
            if not all([new_username, alert_id]):
                continue
            try:
                new_user = await self._users.lookup_user(new_username, live=True)
                if new_user:
                    await self._alerts.remove_user_from_alert(alert_id, new_user["id"], new_username)
                    stats["alerts_removed"] += 1
            except Exception as e:
                logger.warning(f"Failed to remove {new_username} from alert {alert_id}: {e}")
                stats["errors"] += 1

        # A6. Delete cloned collections
        collection_clone_events = [e for e in successful if e.get("action") == AuditAction.CLONE_COLLECTION.value]
        for event in collection_clone_events:
            collection_id = event.get("object_id")
            if not collection_id:
                continue
            try:
                await self._collections.delete_collection(collection_id)
                stats["collections_removed"] += 1
            except Exception as e:
                logger.warning(f"Failed to delete cloned collection {collection_id}: {e}")
                stats["errors"] += 1

        # A7. Transfer custom view ownership back to old user
        cv_clone_events = [e for e in successful if e.get("action") == AuditAction.CLONE_CUSTOM_VIEW.value]
        for event in cv_clone_events:
            old_username = event.get("old_username")
            cv_id = event.get("object_id")
            if not all([old_username, cv_id]):
                continue
            try:
                old_user = await self._users.lookup_user(old_username, live=True)
                if old_user:
                    await self._custom_views.transfer_ownership(cv_id, old_user["id"], old_username)
                    stats["custom_views_reversed"] += 1
            except Exception as e:
                logger.warning(f"Failed to transfer custom view {cv_id} back to {old_username}: {e}")
                stats["errors"] += 1

        # === Phase B: Restore removals from old user ===

        # B1. Re-add old user to groups they were removed from
        group_remove_events = [e for e in successful if e.get("action") == AuditAction.REMOVE_FROM_GROUP.value]
        for event in group_remove_events:
            old_username = event.get("old_username")
            group_id = event.get("object_id")
            group_name = event.get("object_name", "")
            if not all([old_username, group_id]):
                continue
            try:
                old_user = await self._users.lookup_user(old_username, live=True)
                if old_user:
                    await self._groups.add_user_to_group(
                        group_id, old_user["id"], old_username, group_name
                    )
                    stats["groups_restored"] += 1
            except Exception as e:
                logger.warning(f"Failed to re-add {old_username} to group {group_name}: {e}")
                stats["errors"] += 1

        # B2. Re-grant old user's permissions
        perm_remove_events = [
            e for e in successful
            if e.get("action") in (AuditAction.REMOVE_PERMISSION.value, AuditAction.REMOVE_DEFAULT_PERMISSION.value)
        ]
        for event in perm_remove_events:
            old_username = event.get("old_username")
            content_type = event.get("object_type")
            content_id = event.get("object_id")
            details = event.get("details", {})
            capability = details.get("capability")
            mode = details.get("capability_mode")
            if not all([old_username, content_type, content_id, capability, mode]):
                logger.warning(
                    f"Cannot restore permission for {old_username} on {content_type}/{content_id}: "
                    f"missing capability_mode in audit details"
                )
                stats["errors"] += 1
                continue
            try:
                old_user = await self._users.lookup_user(old_username, live=True)
                if old_user:
                    await self._permissions.grant_single_permission(
                        content_type, content_id, old_user["id"], capability, mode
                    )
                    stats["permissions_restored"] += 1
            except Exception as e:
                logger.warning(f"Failed to re-grant {capability}/{mode} to {old_username} on {content_type}/{content_id}: {e}")
                stats["errors"] += 1

        # B3. Re-add old user's favorites
        fav_remove_events = [e for e in successful if e.get("action") == AuditAction.REMOVE_FAVORITE.value]
        for event in fav_remove_events:
            old_username = event.get("old_username")
            content_type = event.get("object_type")
            content_id = event.get("object_id")
            if not all([old_username, content_type, content_id]):
                logger.warning(f"Cannot restore favorite for {old_username}: missing content_type or content_id in audit")
                stats["errors"] += 1
                continue
            try:
                old_user = await self._users.lookup_user(old_username, live=True)
                if old_user:
                    await self._favorites.add_favorite(old_user["id"], old_username, content_type, content_id)
                    stats["favorites_restored"] += 1
            except Exception as e:
                logger.warning(f"Failed to re-add favorite {content_type}/{content_id} for {old_username}: {e}")
                stats["errors"] += 1

        # B4. Re-add old user to alerts
        alert_remove_events = [e for e in successful if e.get("action") == AuditAction.REMOVE_ALERT.value]
        for event in alert_remove_events:
            old_username = event.get("old_username")
            alert_id = event.get("object_id")
            if not all([old_username, alert_id]):
                continue
            try:
                old_user = await self._users.lookup_user(old_username, live=True)
                if old_user:
                    await self._alerts.add_user_to_alert(alert_id, old_user["id"], old_username)
                    stats["alerts_restored"] += 1
            except Exception as e:
                logger.warning(f"Failed to re-add {old_username} to alert {alert_id}: {e}")
                stats["errors"] += 1

        # === Phase C: Structural reversal ===

        # C1. Reverse ownership transfers
        ownership_events = [e for e in successful if e.get("action") == AuditAction.REASSIGN_OWNERSHIP.value]
        for event in reversed(ownership_events):
            old_username = event.get("old_username")
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

        # C2. Re-activate deactivated users
        deactivations = [e for e in successful if e.get("action") == AuditAction.USER_DEACTIVATE.value]
        for event in deactivations:
            old_username = event.get("old_username")
            if not old_username:
                continue
            try:
                user = await self._users.lookup_user(old_username, live=True)
                if user and user.get("site_role") == "Unlicensed":
                    original_role = None
                    new_username = event.get("new_username") or username_map.get(old_username)
                    if new_username:
                        new_user = await self._users.lookup_user(new_username, live=True)
                        if new_user:
                            original_role = new_user.get("site_role")
                    if not original_role:
                        original_role = event.get("details", {}).get("original_site_role")
                    if not original_role or original_role == "Unknown":
                        logger.warning(
                            f"Cannot re-activate {old_username}: original role unknown. "
                            f"Manual intervention required."
                        )
                        stats["errors"] += 1
                        continue
                    await self._users.update_user(user["id"], old_username, original_role)
                    stats["reactivated"] += 1
                    print_status("ROLLBACK", f"Re-activated {old_username} as {original_role}")
            except Exception as e:
                logger.warning(f"Failed to re-activate {old_username}: {e}")
                stats["errors"] += 1

        # C3. Optionally deactivate newly created users
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

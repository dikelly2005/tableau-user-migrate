from typing import List, Dict

from config.settings import Settings
from src.utils.checkpoint import CheckpointManager
from reporting.audit import AuditLogger, AuditAction, AuditResult
from models.mapping import BatchResult, MappingState
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class CleanupWorkflow:
    def __init__(
        self,
        user_service,
        permission_service,
        group_service,
        favorite_service,
        subscription_service,
        alert_service,
        custom_view_service,
        pulse_service,
        webhook_service,
        checkpoint: CheckpointManager,
        audit: AuditLogger,
        settings: Settings,
    ):
        self._users = user_service
        self._permissions = permission_service
        self._groups = group_service
        self._favorites = favorite_service
        self._subscriptions = subscription_service
        self._alerts = alert_service
        self._custom_views = custom_view_service
        self._pulse = pulse_service
        self._webhooks = webhook_service
        self._checkpoint = checkpoint
        self._audit = audit
        self._settings = settings

    async def execute(self, mappings: List[Dict]) -> BatchResult:
        print_status("START", f"Cleanup workflow: {len(mappings)} users")
        self._audit.log(AuditAction.CLEANUP_START, result=AuditResult.SUCCESS)

        result = BatchResult(total=len(mappings))
        pending = self._checkpoint.get_pending()
        pending_usernames = {cp.old_username for cp in pending}

        for m in mappings:
            old_username = m["old_username"]
            new_username = m["new_username"]

            if pending_usernames and old_username not in pending_usernames:
                result.add_success()
                continue

            self._checkpoint.mark_in_progress(old_username)

            try:
                old_user = await self._users.lookup_user(old_username)
                if not old_user:
                    self._checkpoint.mark_failed(old_username, f"User not found: {old_username}")
                    state = MappingState(old_username=old_username, new_username=new_username)
                    state.add_error(f"User not found: {old_username}")
                    result.add_failure(state)
                    continue

                old_user_id = old_user["id"]

                if not self._checkpoint.is_step_completed(old_username, "remove_permissions"):
                    await self._permissions.remove_permissions(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_permissions")

                if not self._checkpoint.is_step_completed(old_username, "remove_groups"):
                    await self._groups.remove_groups(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_groups")

                if not self._checkpoint.is_step_completed(old_username, "remove_favorites"):
                    await self._favorites.remove_favorites(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_favorites")

                if not self._checkpoint.is_step_completed(old_username, "remove_subscriptions"):
                    await self._subscriptions.remove_subscriptions(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_subscriptions")

                if not self._checkpoint.is_step_completed(old_username, "remove_alerts"):
                    await self._alerts.remove_alerts(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_alerts")

                if not self._checkpoint.is_step_completed(old_username, "remove_custom_views"):
                    await self._custom_views.remove_custom_views(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_custom_views")

                if not self._checkpoint.is_step_completed(old_username, "remove_pulse_subscriptions"):
                    await self._pulse.remove_pulse_subscriptions(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_pulse_subscriptions")

                if not self._checkpoint.is_step_completed(old_username, "remove_pulse_alerts"):
                    await self._pulse.remove_pulse_alerts(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_pulse_alerts")

                if not self._checkpoint.is_step_completed(old_username, "remove_webhooks"):
                    await self._webhooks.remove_webhooks(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "remove_webhooks")

                if not self._checkpoint.is_step_completed(old_username, "deactivate"):
                    await self._users.deactivate_user(old_user_id, old_username)
                    self._checkpoint.mark_step_completed(old_username, "deactivate")

                self._checkpoint.mark_completed(old_username)
                result.add_success()

            except Exception as e:
                self._checkpoint.mark_failed(old_username, str(e))
                state = MappingState(old_username=old_username, new_username=new_username)
                state.add_error(str(e))
                result.add_failure(state)
                logger.error(f"Cleanup failed for {old_username}: {e}")

        self._audit.log(AuditAction.CLEANUP_COMPLETE, result=AuditResult.SUCCESS)
        print_status("DONE", result.summary())
        return result

from pathlib import Path
from typing import List, Dict

from config.settings import Settings
from src.utils.cache import DimensionCache
from src.utils.checkpoint import CheckpointManager
from reporting.audit import AuditLogger, AuditAction, AuditResult
from models.mapping import BatchResult, MappingState
from src.utils.logging_config import get_logger, print_status
from src.workflows.report_mixin import UserReportMixin

logger = get_logger(__name__)


class CloneWorkflow(UserReportMixin):
    def __init__(
        self,
        user_service,
        permission_service,
        group_service,
        favorite_service,
        subscription_service,
        alert_service,
        custom_view_service,
        collection_service,
        pulse_service,
        webhook_service,
        cache: DimensionCache,
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
        self._collections = collection_service
        self._pulse = pulse_service
        self._webhooks = webhook_service
        self._cache = cache
        self._checkpoint = checkpoint
        self._audit = audit
        self._settings = settings

    async def execute(self, mappings: List[Dict], audit_dir: Path) -> BatchResult:
        print_status("START", f"Clone workflow: {len(mappings)} users")
        self._audit.log(AuditAction.CLONE_START, result=AuditResult.SUCCESS)

        await self._generate_all_user_reports(mappings, audit_dir, "clone")

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
                    self._checkpoint.mark_failed(old_username, f"Old user not found: {old_username}")
                    state = MappingState(old_username=old_username, new_username=new_username)
                    state.add_error(f"Old user not found: {old_username}")
                    result.add_failure(state)
                    continue

                old_user_id = old_user["id"]
                site_role = old_user["site_role"]
                auth_setting = old_user.get("auth_setting")

                if not self._checkpoint.is_step_completed(old_username, "create_user"):
                    new_user = await self._users.create_user(new_username, site_role, auth_setting=auth_setting)
                    self._checkpoint.mark_step_completed(old_username, "create_user")
                else:
                    new_user = await self._users.lookup_user(new_username)
                    if not new_user:
                        new_user = await self._users.create_user(new_username, site_role, auth_setting=auth_setting)

                new_user_id = new_user["id"]

                if not self._checkpoint.is_step_completed(old_username, "clone_permissions"):
                    await self._permissions.clone_permissions(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_permissions")

                if not self._checkpoint.is_step_completed(old_username, "clone_groups"):
                    await self._groups.clone_groups(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_groups")

                if not self._checkpoint.is_step_completed(old_username, "clone_favorites"):
                    await self._favorites.clone_favorites(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_favorites")

                if not self._checkpoint.is_step_completed(old_username, "clone_subscriptions"):
                    await self._subscriptions.clone_subscriptions(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_subscriptions")

                if not self._checkpoint.is_step_completed(old_username, "clone_alerts"):
                    await self._alerts.clone_alerts(old_user_id, old_username, new_user_id, new_username, transfer_ownership=False)
                    self._checkpoint.mark_step_completed(old_username, "clone_alerts")

                if not self._checkpoint.is_step_completed(old_username, "clone_custom_views"):
                    print_status("SKIP", f"Custom view ownership skipped in clone mode (no copy API — use migrate mode to transfer): {old_username}")
                    self._checkpoint.mark_step_completed(old_username, "clone_custom_views")

                if not self._checkpoint.is_step_completed(old_username, "clone_custom_view_defaults"):
                    await self._custom_views.clone_custom_view_defaults(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_custom_view_defaults")

                if not self._checkpoint.is_step_completed(old_username, "clone_collections"):
                    await self._collections.clone_collections(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_collections")

                if not self._checkpoint.is_step_completed(old_username, "clone_pulse_subscriptions"):
                    await self._pulse.clone_pulse_subscriptions(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_pulse_subscriptions")

                if not self._checkpoint.is_step_completed(old_username, "clone_pulse_alerts"):
                    await self._pulse.clone_pulse_alerts(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_pulse_alerts")

                if not self._checkpoint.is_step_completed(old_username, "clone_webhooks"):
                    await self._webhooks.clone_webhooks(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_webhooks")

                self._checkpoint.mark_completed(old_username)
                result.add_success()

            except Exception as e:
                self._checkpoint.mark_failed(old_username, str(e))
                state = MappingState(old_username=old_username, new_username=new_username)
                state.add_error(str(e))
                result.add_failure(state)
                logger.error(f"Clone failed for {old_username}: {e}")

        self._audit.log(AuditAction.CLONE_COMPLETE, result=AuditResult.SUCCESS)
        print_status("DONE", result.summary())
        return result

# Shared cleanup step sequence used by both migrate and clean-only workflows
# Co-authored with CoCo
from typing import Set

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class CleanupMixin:

    async def _cleanup_user(self, old_user_id: str, old_username: str, skip_steps: Set[str] = None) -> None:
        skip = skip_steps or set()

        steps = [
            ("remove_custom_view_defaults", lambda: self._custom_views.remove_custom_view_defaults(old_user_id, old_username)),
            ("remove_favorites", lambda: self._favorites.remove_favorites(old_user_id, old_username)),
            ("remove_subscriptions", lambda: self._subscriptions.remove_subscriptions(old_user_id, old_username)),
            ("remove_alerts", lambda: self._alerts.remove_alerts(old_user_id, old_username)),
            ("remove_custom_views", lambda: self._custom_views.remove_custom_views(old_user_id, old_username)),
            ("remove_pulse_subscriptions", lambda: self._pulse.remove_pulse_subscriptions(old_user_id, old_username)),
            ("remove_pulse_alerts", lambda: self._pulse.remove_pulse_alerts(old_user_id, old_username)),
            ("remove_webhooks", lambda: self._webhooks.remove_webhooks(old_user_id, old_username)),
            ("remove_permissions", lambda: self._permissions.remove_permissions(old_user_id, old_username)),
            ("remove_groups", lambda: self._groups.remove_groups(old_user_id, old_username)),
            ("deactivate", lambda: self._users.deactivate_user(old_user_id, old_username)),
        ]

        for step_name, step_fn in steps:
            if step_name in skip:
                continue
            if not self._checkpoint.is_step_completed(old_username, step_name):
                await step_fn()
                self._checkpoint.mark_step_completed(old_username, step_name)

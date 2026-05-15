from typing import List

from src.api.client import TableauAPIClient, _findall_any, _find_any
from src.utils.cache import DimensionCache, user_filter
from models.impact import UXArtifact
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class SubscriptionService:
    def __init__(self, client: TableauAPIClient, audit: AuditLogger, cache: DimensionCache):
        self._client = client
        self._audit = audit
        self._cache = cache

    async def get_user_subscriptions(self, user_id: str, username: str) -> List[UXArtifact]:
        sub_ids = self._cache.get_ids("subscriptions", filter_fn=user_filter(user_id))
        subs = []
        for sid in sub_ids:
            record = self._cache.get_record("subscriptions", sid)
            content = record.attrs.get("content", {})
            schedule = record.attrs.get("schedule", {})
            subs.append(UXArtifact(
                artifact_id=record.id,
                artifact_type="subscription",
                content_type=content.get("type") if isinstance(content, dict) else None,
                content_id=content.get("id") if isinstance(content, dict) else None,
                details={
                    "subject": record.name,
                    "schedule_id": schedule.get("id") if isinstance(schedule, dict) else None,
                },
            ))
        print_status("CACHE", f"Found {len(subs)} subscriptions for {username}")
        return subs

    async def clone_subscriptions(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Cloning subscriptions: {old_username} -> {new_username}")
        subs = await self.get_user_subscriptions(old_user_id, old_username)
        cloned = 0

        for sub in subs:
            endpoint = f"/sites/{self._client.site_id}/subscriptions"
            schedule_id = sub.details.get("schedule_id") if sub.details else None
            subject = sub.details.get("subject", "Subscription") if sub.details else "Subscription"

            payload = (
                '<tsRequest>'
                f'<subscription subject="{subject}">'
                f'<content type="{sub.content_type}" id="{sub.content_id}"/>'
                f'<schedule id="{schedule_id}"/>'
                f'<user id="{new_user_id}"/>'
                '</subscription>'
                '</tsRequest>'
            )

            try:
                await self._client.post(endpoint, payload)
                cloned += 1
                self._audit.log_success(AuditAction.CLONE_SUBSCRIPTION, new_username=new_username, object_type="subscription", object_id=sub.artifact_id)
            except Exception as e:
                if "409" in str(e) or "already exists" in str(e).lower():
                    self._audit.log_skipped(AuditAction.CLONE_SUBSCRIPTION, reason="Subscription already exists", new_username=new_username)
                    cloned += 1
                else:
                    logger.warning(f"Failed to clone subscription: {e}")
                    self._audit.log_failure(AuditAction.CLONE_SUBSCRIPTION, error_message=str(e), new_username=new_username)

        print_status("DONE", f"Cloned {cloned} subscriptions for {new_username}")
        return cloned

    async def remove_subscriptions(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing subscriptions from {username}")
        subs = await self.get_user_subscriptions(user_id, username)
        removed = 0

        for sub in subs:
            endpoint = f"/sites/{self._client.site_id}/subscriptions/{sub.artifact_id}"
            try:
                await self._client.delete(endpoint)
                removed += 1
                self._audit.log_success(AuditAction.REMOVE_SUBSCRIPTION, old_username=username, object_type="subscription", object_id=sub.artifact_id)
            except Exception as e:
                logger.warning(f"Failed to remove subscription: {e}")

        print_status("DONE", f"Removed {removed} subscriptions from {username}")
        return removed

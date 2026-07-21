# Tableau subscription cloning with schedule validation
# Co-authored with CoCo
from typing import List

from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache, user_filter
from src.utils.paths import resolve_endpoint_path
from src.utils.exceptions import is_conflict_error
from src.utils.xml_escape import xml_attr_val
from models.impact import UXArtifact
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class SubscriptionService:
    def __init__(self, client: TableauAPIClient, audit: AuditLogger, cache: DimensionCache, endpoints_config: dict):
        self._client = client
        self._audit = audit
        self._cache = cache
        self._endpoints = endpoints_config.get("endpoints", {})

    def _resolve_path(self, endpoint_name: str, **kwargs) -> str:
        ep_config = self._endpoints.get(endpoint_name)
        if not ep_config:
            raise ValueError(f"Unknown endpoint: {endpoint_name}")
        return resolve_endpoint_path(ep_config["path"], self._client.site_id, **kwargs)

    async def get_user_subscriptions(self, user_id: str, username: str) -> List[UXArtifact]:
        if not self._cache.has_dimension("subscriptions"):
            logger.warning("Cache miss for 'subscriptions' — dimension not populated. Results may be incomplete.")
            return []
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
                    "frequency": schedule.get("frequency") if isinstance(schedule, dict) else None,
                    "frequency_details": schedule.get("frequencyDetails") if isinstance(schedule, dict) else None,
                    "send_if_view_empty": content.get("sendIfViewEmpty", "true") if isinstance(content, dict) else "true",
                },
            ))
        print_status("CACHE", f"Found {len(subs)} subscriptions for {username}")
        return subs

    def _build_schedule_xml(self, schedule: dict) -> str:
        frequency = schedule.get("frequency", "Daily")
        details = schedule.get("frequencyDetails", {})
        start = details.get("start", "00:00:00")
        end = details.get("end", start)
        intervals_data = details.get("intervals", {})

        interval_elements = []
        if intervals_data:
            interval = intervals_data.get("interval", {})
            if isinstance(interval, dict) and interval:
                interval_elements.append(interval)
            elif isinstance(interval, list):
                interval_elements.extend(interval)

        if frequency == "Daily":
            has_hours = any("hours" in elem for elem in interval_elements)
            if not has_hours:
                interval_elements.insert(0, {"hours": "24"})

        intervals_xml = ""
        if interval_elements:
            inner = "".join(
                '<interval ' + " ".join(f'{k}="{v}"' for k, v in elem.items()) + '/>'
                for elem in interval_elements
            )
            intervals_xml = f'<intervals>{inner}</intervals>'

        end_attr = ""
        if frequency in ("Daily", "Hourly"):
            end_attr = f' end="{end}"'

        return (
            f'<schedule frequency="{frequency}">'
            f'<frequencyDetails start="{start}"{end_attr}>'
            f'{intervals_xml}'
            f'</frequencyDetails>'
            f'</schedule>'
        )

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

        endpoint = self._resolve_path("subscriptions")

        for sub in subs:
            subject = sub.details.get("subject", "Subscription") if sub.details else "Subscription"
            content_type = sub.content_type or "View"
            content_id = sub.content_id or ""
            send_if_empty = sub.details.get("send_if_view_empty", "true") if sub.details else "true"

            record = self._cache.get_record("subscriptions", sub.artifact_id)
            schedule = record.attrs.get("schedule", {}) if record else {}
            schedule_xml = self._build_schedule_xml(schedule)

            payload = (
                '<tsRequest>'
                f'<subscription subject="{xml_attr_val(subject)}">'
                f'<content id="{content_id}" type="{content_type}" sendIfViewEmpty="{send_if_empty}"/>'
                f'<user id="{new_user_id}"/>'
                f'</subscription>'
                f'{schedule_xml}'
                '</tsRequest>'
            )

            try:
                await self._client.post(endpoint, payload)
                cloned += 1
                self._audit.log_success(AuditAction.CLONE_SUBSCRIPTION, new_username=new_username, object_type="subscription", object_id=sub.artifact_id, details={"subject": subject})
            except Exception as e:
                error_str = str(e)
                if is_conflict_error(e):
                    cloned += 1
                    self._audit.log_skipped(AuditAction.CLONE_SUBSCRIPTION, reason="Subscription already exists", new_username=new_username, object_type="subscription", object_id=sub.artifact_id)
                else:
                    logger.warning(f"Failed to create subscription '{subject}': {e}")
                    self._audit.log_failure(AuditAction.CLONE_SUBSCRIPTION, error_message=error_str[:300], new_username=new_username, object_id=sub.artifact_id)

        print_status("DONE", f"Cloned {cloned} subscriptions for {new_username}")
        return cloned

    async def remove_subscriptions(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing subscriptions from {username}")
        subs = await self.get_user_subscriptions(user_id, username)
        removed = 0

        for sub in subs:
            endpoint = self._resolve_path("subscription_single", subscription_id=sub.artifact_id)
            try:
                await self._client.delete(endpoint)
                removed += 1
                self._audit.log_success(
                    AuditAction.REMOVE_SUBSCRIPTION,
                    old_username=username,
                    object_type="subscription",
                    object_id=sub.artifact_id,
                    object_name=sub.artifact_name,
                    details={
                        "content_type": sub.content_type,
                        "content_id": sub.content_id,
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to remove subscription: {e}")

        print_status("DONE", f"Removed {removed} subscriptions from {username}")
        return removed

    async def remove_single_subscription(self, subscription_id: str, username: str) -> None:
        endpoint = self._resolve_path("subscription_single", subscription_id=subscription_id)
        await self._client.delete(endpoint)

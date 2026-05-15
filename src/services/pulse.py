import json
from typing import List, Dict, Optional

from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


class PulseService:
    def __init__(self, client: TableauAPIClient, audit: AuditLogger, cache: DimensionCache):
        self._client = client
        self._audit = audit
        self._cache = cache

    def _get_subscriptions_from_cache(self, user_id: str) -> List[Dict]:
        records = self._cache.get_all_records("pulse_subscriptions")
        results = []
        for r in records:
            follower = r.attrs.get("follower_id") or r.attrs.get("user_id")
            if follower == user_id:
                results.append({
                    "id": r.id,
                    "metric_id": r.attrs.get("metric_id"),
                    "user_id": follower,
                    "condition": r.attrs.get("condition"),
                    "creator_id": r.attrs.get("creator_id"),
                })
        return results

    def get_user_pulse_subscriptions(self, user_id: str, username: str) -> List[Dict]:
        subs = self._get_subscriptions_from_cache(user_id)
        print_status("GET", f"Found {len(subs)} Pulse subscriptions for {username}")
        return subs

    def get_pulse_definition(self, definition_id: str) -> Optional[Dict]:
        record = self._cache.get_record("pulse_definitions", definition_id)
        if not record:
            return None
        return {
            "id": record.id,
            "name": record.name,
            "specification": record.attrs.get("specification"),
            "owner": record.attrs.get("owner"),
        }

    async def clone_pulse_subscriptions(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Cloning Pulse subscriptions: {old_username} -> {new_username}")
        subs = self._get_subscriptions_from_cache(old_user_id)
        cloned = 0

        for sub in subs:
            metric_id = sub.get("metric_id")
            if not metric_id:
                continue

            payload = {
                "metric_id": metric_id,
                "user_id": new_user_id,
            }
            condition = sub.get("condition")
            if condition:
                payload["condition"] = condition

            endpoint = "/-/api/pulse/subscriptions"
            try:
                await self._client._base.request(
                    "POST", endpoint,
                    content=json.dumps(payload),
                    headers=_JSON_HEADERS,
                )
                cloned += 1
                self._audit.log_success(
                    AuditAction.CLONE_PULSE_SUBSCRIPTION,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="pulse_subscription",
                    object_id=sub.get("id"),
                    details={"metric_id": metric_id},
                )
            except Exception as e:
                if "409" in str(e) or "already exists" in str(e).lower():
                    self._audit.log_skipped(
                        AuditAction.CLONE_PULSE_SUBSCRIPTION,
                        reason="Pulse subscription already exists",
                        new_username=new_username,
                    )
                    cloned += 1
                else:
                    logger.warning(f"Failed to clone Pulse subscription for metric {metric_id}: {e}")
                    self._audit.log_failure(
                        AuditAction.CLONE_PULSE_SUBSCRIPTION,
                        error_message=str(e),
                        old_username=old_username,
                        new_username=new_username,
                        object_id=sub.get("id"),
                    )

        print_status("DONE", f"Cloned {cloned} Pulse subscriptions for {new_username}")
        return cloned

    async def remove_pulse_subscriptions(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing Pulse subscriptions from {username}")
        subs = self._get_subscriptions_from_cache(user_id)
        removed = 0

        for sub in subs:
            sub_id = sub.get("id")
            if not sub_id:
                continue
            endpoint = f"/-/api/pulse/subscriptions/{sub_id}"
            try:
                await self._client._base.request(
                    "DELETE", endpoint, headers=_JSON_HEADERS,
                )
                removed += 1
                self._audit.log_success(
                    AuditAction.REMOVE_PULSE_SUBSCRIPTION,
                    old_username=username,
                    object_type="pulse_subscription",
                    object_id=sub_id,
                )
            except Exception as e:
                logger.warning(f"Failed to remove Pulse subscription {sub_id}: {e}")
                self._audit.log_failure(
                    AuditAction.REMOVE_PULSE_SUBSCRIPTION,
                    error_message=str(e),
                    old_username=username,
                    object_id=sub_id,
                )

        print_status("DONE", f"Removed {removed} Pulse subscriptions from {username}")
        return removed

    def _get_alerts_from_cache(self, user_id: str) -> List[Dict]:
        records = self._cache.get_all_records("pulse_alerts")
        results = []
        for r in records:
            owner = r.attrs.get("owner_id") or r.attrs.get("creator_id")
            if owner == user_id:
                results.append({
                    "id": r.id,
                    "owner_id": r.attrs.get("owner_id"),
                    "creator_id": r.attrs.get("creator_id"),
                    "metric_id": r.attrs.get("metric_id"),
                    "condition": r.attrs.get("condition"),
                    "threshold": r.attrs.get("threshold"),
                })
        return results

    def get_user_pulse_alerts(self, user_id: str, username: str) -> List[Dict]:
        alerts = self._get_alerts_from_cache(user_id)
        print_status("GET", f"Found {len(alerts)} Pulse alerts for {username}")
        return alerts

    async def clone_pulse_alerts(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Cloning Pulse alerts: {old_username} -> {new_username}")
        alerts = self._get_alerts_from_cache(old_user_id)
        cloned = 0

        for alert in alerts:
            alert_id = alert.get("id")
            if not alert_id:
                continue

            endpoint = f"/-/pulse/alerts/{alert_id}"
            payload = {"owner_id": new_user_id}
            try:
                await self._client._base.request(
                    "PUT", endpoint,
                    content=json.dumps(payload),
                    headers=_JSON_HEADERS,
                )
                cloned += 1
                self._audit.log_success(
                    AuditAction.CLONE_PULSE_ALERT,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="pulse_alert",
                    object_id=alert_id,
                    details={"metric_id": alert.get("metric_id")},
                )
            except Exception as e:
                logger.warning(f"Failed to transfer Pulse alert {alert_id}: {e}")
                self._audit.log_failure(
                    AuditAction.CLONE_PULSE_ALERT,
                    error_message=str(e),
                    old_username=old_username,
                    new_username=new_username,
                    object_id=alert_id,
                )

        print_status("DONE", f"Transferred {cloned} Pulse alerts to {new_username}")
        return cloned

    async def remove_pulse_alerts(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing Pulse alerts from {username}")
        alerts = self._get_alerts_from_cache(user_id)
        removed = 0

        for alert in alerts:
            alert_id = alert.get("id")
            if not alert_id:
                continue
            endpoint = f"/-/pulse/alerts/{alert_id}"
            try:
                await self._client._base.request(
                    "DELETE", endpoint, headers=_JSON_HEADERS,
                )
                removed += 1
                self._audit.log_success(
                    AuditAction.REMOVE_PULSE_ALERT,
                    old_username=username,
                    object_type="pulse_alert",
                    object_id=alert_id,
                )
            except Exception as e:
                logger.warning(f"Failed to remove Pulse alert {alert_id}: {e}")
                self._audit.log_failure(
                    AuditAction.REMOVE_PULSE_ALERT,
                    error_message=str(e),
                    old_username=username,
                    object_id=alert_id,
                )

        print_status("DONE", f"Removed {removed} Pulse alerts from {username}")
        return removed

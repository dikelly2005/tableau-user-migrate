# Tableau Pulse subscription and alert migration service
# Co-authored with CoCo
import json
from typing import List, Dict, Optional

from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache
from src.utils.paths import resolve_endpoint_path
from src.utils.exceptions import is_conflict_error
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


class PulseService:
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

    def _get_subscriptions_from_cache(self, user_id: str) -> List[Dict]:
        if not self._cache.has_dimension("pulse_subscriptions"):
            logger.warning("Cache miss for 'pulse_subscriptions' — dimension not populated. Results may be incomplete.")
            return []
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
                "follower_id": new_user_id,
            }
            condition = sub.get("condition")
            if condition:
                payload["condition"] = condition

            endpoint = self._resolve_path("pulse_subscriptions")
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
                if is_conflict_error(e):
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
            endpoint = f"{self._resolve_path('pulse_subscriptions')}/{sub_id}"
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
        if not self._cache.has_dimension("pulse_alerts"):
            logger.warning("Cache miss for 'pulse_alerts' — dimension not populated. Results may be incomplete.")
            return []
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
        # Tableau Pulse API only exposes GET /pulse/alerts (read-only).
        # PUT/PATCH on individual alerts is not supported — alerts cannot be transferred via API.
        alerts = self._get_alerts_from_cache(old_user_id)
        if alerts:
            logger.warning(
                f"Cannot clone {len(alerts)} Pulse alerts for {old_username}: "
                "Tableau API does not support PUT/DELETE on alerts"
            )
            for alert in alerts:
                self._audit.log_skipped(
                    AuditAction.CLONE_PULSE_ALERT,
                    reason="Tableau Pulse API does not support alert transfer (GET-only endpoint)",
                    old_username=old_username,
                    new_username=new_username,
                    object_id=alert.get("id"),
                )
        print_status("SKIP", f"Pulse alerts cannot be transferred via API ({len(alerts)} found for {old_username})")
        return 0

    async def remove_pulse_alerts(self, user_id: str, username: str) -> int:
        # Tableau Pulse API only exposes GET /pulse/alerts (read-only).
        # DELETE on individual alerts is not supported.
        alerts = self._get_alerts_from_cache(user_id)
        if alerts:
            logger.warning(
                f"Cannot remove {len(alerts)} Pulse alerts for {username}: "
                "Tableau API does not support DELETE on alerts"
            )
            for alert in alerts:
                self._audit.log_skipped(
                    AuditAction.REMOVE_PULSE_ALERT,
                    reason="Tableau Pulse API does not support alert deletion (GET-only endpoint)",
                    old_username=username,
                    object_id=alert.get("id"),
                )
        print_status("SKIP", f"Pulse alerts cannot be removed via API ({len(alerts)} found for {username})")
        return 0

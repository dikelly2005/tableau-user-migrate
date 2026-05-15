import asyncio
from typing import List

from src.api.client import TableauAPIClient, _findall_any, _find_any
from src.utils.cache import DimensionCache, owner_filter
from src.utils.exceptions import APIError
from models.impact import UXArtifact
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_MAX_ALERT_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0


class AlertService:
    def __init__(self, client: TableauAPIClient, audit: AuditLogger, cache: DimensionCache):
        self._client = client
        self._audit = audit
        self._cache = cache

    async def get_user_alerts(self, user_id: str, username: str) -> List[UXArtifact]:
        alert_ids = self._cache.get_ids("data_alerts", filter_fn=owner_filter(user_id))
        alerts = []
        for aid in alert_ids:
            record = self._cache.get_record("data_alerts", aid)
            alerts.append(UXArtifact(
                artifact_id=record.id,
                artifact_type="alert",
                content_type="view",
                content_id=record.attrs.get("view", {}).get("id") if isinstance(record.attrs.get("view"), dict) else None,
                details={"subject": record.name, "creatorId": record.attrs.get("creatorId")},
            ))
        print_status("CACHE", f"Found {len(alerts)} alerts for {username}")
        return alerts

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(exc, APIError) and exc.status_code and exc.status_code >= 500:
            return True
        return False

    @staticmethod
    def _is_conflict(exc: Exception) -> bool:
        return "409" in str(exc) or "already exists" in str(exc).lower()

    async def _add_recipient_with_retry(
        self,
        endpoint: str,
        payload: str,
        alert_id: str,
        max_retries: int = _MAX_ALERT_RETRIES,
    ) -> bool:
        for attempt in range(max_retries):
            try:
                await self._client.post(endpoint, payload)
                return True
            except Exception as e:
                if self._is_conflict(e):
                    return True
                if self._is_transient(e) and attempt < max_retries - 1:
                    wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning(f"Transient error adding recipient to alert {alert_id} (attempt {attempt + 1}/{max_retries}): {e}")
                    self._audit.log_retry(
                        AuditAction.CLONE_ALERT,
                        attempt=attempt + 1,
                        max_attempts=max_retries,
                        error_message=str(e),
                        object_type="alert",
                        object_name=alert_id,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
        return False

    async def clone_alerts(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Cloning alerts: {old_username} -> {new_username}")
        alerts = await self.get_user_alerts(old_user_id, old_username)
        cloned = 0

        for alert in alerts:
            recipient_endpoint = f"/sites/{self._client.site_id}/dataAlerts/{alert.artifact_id}/users"
            recipient_payload = f'<tsRequest><user id="{new_user_id}"/></tsRequest>'

            try:
                await self._add_recipient_with_retry(recipient_endpoint, recipient_payload, alert.artifact_id)
            except Exception as e:
                logger.warning(f"Failed to add new user to alert {alert.artifact_id} after retries: {e}")
                self._audit.log_failure(
                    AuditAction.CLONE_ALERT,
                    error_message=str(e),
                    new_username=new_username,
                    object_id=alert.artifact_id,
                )
                continue

            transfer_endpoint = f"/sites/{self._client.site_id}/dataAlerts/{alert.artifact_id}"
            transfer_payload = (
                f'<tsRequest><dataAlert>'
                f'<owner id="{new_user_id}"/>'
                f'</dataAlert></tsRequest>'
            )
            try:
                await self._client.put(transfer_endpoint, transfer_payload)
                cloned += 1
                self._audit.log_success(
                    AuditAction.CLONE_ALERT,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="alert",
                    object_id=alert.artifact_id,
                    details={"ownership_transferred": True},
                )
            except Exception as e:
                logger.warning(f"Failed to transfer alert ownership {alert.artifact_id}: {e}")
                self._audit.log_failure(
                    AuditAction.CLONE_ALERT,
                    error_message=f"Added as recipient but ownership transfer failed: {e}",
                    old_username=old_username,
                    new_username=new_username,
                    object_id=alert.artifact_id,
                )
                cloned += 1

        print_status("DONE", f"Cloned {cloned} alerts for {new_username}")
        return cloned

    async def remove_alerts(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing alerts from {username}")
        alerts = await self.get_user_alerts(user_id, username)
        removed = 0

        for alert in alerts:
            endpoint = f"/sites/{self._client.site_id}/dataAlerts/{alert.artifact_id}/users/{user_id}"
            try:
                await self._client.delete(endpoint)
                removed += 1
                self._audit.log_success(AuditAction.REMOVE_ALERT, old_username=username, object_type="alert", object_id=alert.artifact_id)
            except Exception as e:
                logger.warning(f"Failed to remove alert recipient: {e}")
                self._audit.log_failure(AuditAction.REMOVE_ALERT, error_message=str(e), old_username=username, object_id=alert.artifact_id)

        print_status("DONE", f"Removed {removed} alerts from {username}")
        return removed

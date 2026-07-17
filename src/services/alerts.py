# Tableau data alert cloning and removal service
# Co-authored with CoCo
import asyncio
from typing import List

from src.api.client import TableauAPIClient, _findall_any, _find_any
from src.utils.cache import DimensionCache, owner_filter
from src.utils.paths import resolve_endpoint_path
from src.utils.exceptions import APIError
from models.impact import UXArtifact
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_MAX_ALERT_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0


class AlertService:
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

    async def _get_alert_recipients(self, alert_id: str) -> List[str]:
        endpoint = self._resolve_path("data_alert_single", alert_id=alert_id)
        try:
            root = await self._client.get(endpoint)
            recipients = []
            for recipient_el in _findall_any(root, "recipient"):
                rid = recipient_el.get("id")
                if rid:
                    recipients.append(rid)
            return recipients
        except Exception as e:
            logger.warning(f"Failed to get recipients for alert {alert_id}: {e}")
            return []

    async def clone_alerts(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
        transfer_ownership: bool = True,
    ) -> int:
        print_status("START", f"Cloning alerts: {old_username} -> {new_username}")
        owned_alerts = await self.get_user_alerts(old_user_id, old_username)
        owned_alert_ids = {a.artifact_id for a in owned_alerts}
        cloned = 0

        for alert in owned_alerts:
            recipient_endpoint = self._resolve_path("data_alert_users", alert_id=alert.artifact_id)
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

            if transfer_ownership:
                transfer_endpoint = self._resolve_path("data_alert_single", alert_id=alert.artifact_id)
                transfer_payload = (
                    f'<tsRequest><dataAlert>'
                    f'<owner id="{new_user_id}"/>'
                    f'</dataAlert></tsRequest>'
                )
                try:
                    await self._client.put(transfer_endpoint, transfer_payload)
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
            else:
                self._audit.log_success(
                    AuditAction.CLONE_ALERT,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="alert",
                    object_id=alert.artifact_id,
                    details={"recipient_only": True, "ownership_kept_by_old_user": True},
                )
            cloned += 1

        all_alert_ids = self._cache.get_ids("data_alerts")
        non_owned_ids = [aid for aid in all_alert_ids if aid not in owned_alert_ids]
        if non_owned_ids:
            print_status("INFO", f"Checking {len(non_owned_ids)} non-owned alerts for recipient membership")
        for aid in non_owned_ids:
            recipients = await self._get_alert_recipients(aid)
            if old_user_id not in recipients:
                continue
            recipient_endpoint = self._resolve_path("data_alert_users", alert_id=aid)
            recipient_payload = f'<tsRequest><user id="{new_user_id}"/></tsRequest>'
            try:
                await self._add_recipient_with_retry(recipient_endpoint, recipient_payload, aid)
                cloned += 1
                record = self._cache.get_record("data_alerts", aid)
                self._audit.log_success(
                    AuditAction.CLONE_ALERT,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="alert",
                    object_id=aid,
                    details={"recipient_only": True, "subject": record.name if record else None},
                )
            except Exception as e:
                logger.warning(f"Failed to add new user as recipient to alert {aid}: {e}")
                self._audit.log_failure(
                    AuditAction.CLONE_ALERT,
                    error_message=str(e),
                    new_username=new_username,
                    object_id=aid,
                    details={"recipient_only": True},
                )

        print_status("DONE", f"Cloned {cloned} alerts for {new_username}")
        return cloned

    async def remove_alerts(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing alerts from {username}")
        all_alert_ids = self._cache.get_ids("data_alerts")
        removed = 0

        for aid in all_alert_ids:
            endpoint = self._resolve_path("data_alert_user_single", alert_id=aid, user_id=user_id)
            try:
                await self._client.delete(endpoint)
                removed += 1
                self._audit.log_success(AuditAction.REMOVE_ALERT, old_username=username, object_type="alert", object_id=aid)
            except Exception as e:
                if "404" in str(e) or "Not Found" in str(e):
                    continue
                logger.warning(f"Failed to remove alert recipient from {aid}: {e}")
                self._audit.log_failure(AuditAction.REMOVE_ALERT, error_message=str(e), old_username=username, object_id=aid)

        print_status("DONE", f"Removed {removed} alert memberships from {username}")
        return removed

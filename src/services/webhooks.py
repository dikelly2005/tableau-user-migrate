# Tableau webhook ownership transfer service using JSON API
# Co-authored with CoCo
from typing import List, Dict
import json

from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache, owner_filter
from src.utils.paths import resolve_endpoint_path
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class WebhookService:
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

    def get_user_webhooks(self, user_id: str, username: str) -> List[Dict]:
        webhook_ids = self._cache.get_ids("webhooks", filter_fn=owner_filter(user_id))
        webhooks = []
        for wid in webhook_ids:
            record = self._cache.get_record("webhooks", wid)
            webhooks.append({
                "webhook_id": record.id,
                "webhook_name": record.name,
                "event": record.attrs.get("event"),
                "url": record.attrs.get("url"),
            })
        print_status("CACHE", f"Found {len(webhooks)} webhooks for {username}")
        return webhooks

    async def clone_webhooks(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Transferring webhook ownership: {old_username} -> {new_username}")
        webhooks = self.get_user_webhooks(old_user_id, old_username)
        cloned = 0

        for wh in webhooks:
            endpoint = self._resolve_path("webhook_single", webhook_id=wh['webhook_id'])
            # H1 fix: Webhook API requires JSON payload, not XML
            payload = json.dumps({
                "webhook": {
                    "owner": {"id": new_user_id},
                    "webhook-destination": {
                        "webhook-destination-http": {"url": wh["url"]}
                    },
                }
            })
            try:
                await self._client._base.request(
                    "PUT", endpoint,
                    content=payload,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                cloned += 1
                self._audit.log_success(
                    AuditAction.CLONE_WEBHOOK,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="webhook",
                    object_name=wh["webhook_name"],
                    object_id=wh["webhook_id"],
                )
            except Exception as e:
                logger.warning(f"Failed to transfer webhook {wh['webhook_id']}: {e}")
                self._audit.log_failure(
                    AuditAction.CLONE_WEBHOOK,
                    error_message=str(e),
                    old_username=old_username,
                    new_username=new_username,
                    object_id=wh["webhook_id"],
                )

        print_status("DONE", f"Transferred {cloned} webhooks to {new_username}")
        return cloned

    async def remove_webhooks(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing webhooks from {username}")
        webhooks = self.get_user_webhooks(user_id, username)
        removed = 0

        for wh in webhooks:
            endpoint = self._resolve_path("webhook_single", webhook_id=wh['webhook_id'])
            try:
                await self._client.delete(endpoint)
                removed += 1
                self._audit.log_success(
                    AuditAction.REMOVE_WEBHOOK,
                    old_username=username,
                    object_type="webhook",
                    object_name=wh["webhook_name"],
                    object_id=wh["webhook_id"],
                )
            except Exception as e:
                logger.warning(f"Failed to delete webhook {wh['webhook_id']}: {e}")
                self._audit.log_failure(
                    AuditAction.REMOVE_WEBHOOK,
                    error_message=str(e),
                    old_username=username,
                    object_id=wh["webhook_id"],
                )

        print_status("DONE", f"Removed {removed} webhooks from {username}")
        return removed

from typing import List, Dict

from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache, owner_filter
from src.utils.paths import resolve_endpoint_path, resolve_element_tag
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class OwnershipService:
    def __init__(
        self,
        client: TableauAPIClient,
        audit: AuditLogger,
        cache: DimensionCache,
        endpoints_config: dict,
    ):
        self._client = client
        self._audit = audit
        self._cache = cache
        self._endpoints = endpoints_config.get("endpoints", {})

    async def get_owned_content(self, user_id: str, username: str) -> List[Dict]:
        owned = []

        for ep_name, ep_config in self._endpoints.items():
            if not ep_config.get("ownership_transferable"):
                continue

            if self._cache.has_dimension(ep_name):
                item_ids = self._cache.get_ids(ep_name, filter_fn=owner_filter(user_id))
                for item_id in item_ids:
                    record = self._cache.get_record(ep_name, item_id)
                    owned.append({
                        "content_type": ep_name,
                        "content_id": item_id,
                        "content_name": record.name if record else None,
                    })
            else:
                list_path = resolve_endpoint_path(ep_config["path"], self._client.site_id)
                tag = resolve_element_tag(ep_config, ep_name)
                elements = await self._client.paginate_items(list_path, tag)
                for elem in elements:
                    owner_elem = None
                    for child in elem:
                        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_tag == "owner":
                            owner_elem = child
                            break
                    if owner_elem is not None and owner_elem.get("id") == user_id:
                        owned.append({
                            "content_type": ep_name,
                            "content_id": elem.get("id"),
                            "content_name": elem.get("name"),
                        })

        print_status("GET", f"Found {len(owned)} owned items for {username}")
        return owned

    async def transfer_ownership(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Transferring ownership: {old_username} -> {new_username}")
        owned = await self.get_owned_content(old_user_id, old_username)
        transferred = 0

        for item in owned:
            ct = item["content_type"]
            cid = item["content_id"]
            ep_config = self._endpoints.get(ct)
            if not ep_config or not ep_config.get("ownership_transferable"):
                continue

            update_path = resolve_endpoint_path(ep_config["path"], self._client.site_id) + f"/{cid}"
            tag = resolve_element_tag(ep_config, ct)
            payload = (
                f'<tsRequest><{tag} id="{cid}">'
                f'<owner id="{new_user_id}"/>'
                f'</{tag}></tsRequest>'
            )

            try:
                await self._client.put(update_path, payload)
                transferred += 1
                self._cache.invalidate_owner(ct, cid, new_user_id)
                self._audit.log_success(
                    AuditAction.REASSIGN_OWNERSHIP,
                    old_username=old_username,
                    new_username=new_username,
                    object_type=ct,
                    object_name=item.get("content_name"),
                    object_id=cid,
                )
            except Exception as e:
                logger.warning(f"Failed to transfer {ct}/{cid}: {e}")
                self._audit.log_failure(
                    AuditAction.REASSIGN_OWNERSHIP,
                    error_message=str(e),
                    old_username=old_username,
                    new_username=new_username,
                    object_type=ct,
                    object_id=cid,
                )

        print_status("DONE", f"Transferred {transferred} items to {new_username}")
        return transferred

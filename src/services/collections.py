# Tableau user migration - collection cloning with safety checks
# Co-authored with CoCo
import json
from typing import List, Dict, Optional

from src.api.client import TableauAPIClient, _findall_any
from src.utils.cache import DimensionCache, owner_filter
from src.utils.paths import resolve_endpoint_path
from src.utils.exceptions import is_conflict_error
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class CollectionService:
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

    def _resolve_path(self, endpoint_name: str, **kwargs) -> str:
        ep_config = self._endpoints.get(endpoint_name)
        if not ep_config:
            raise ValueError(f"Unknown endpoint: {endpoint_name}")
        return resolve_endpoint_path(ep_config["path"], self._client.site_id, **kwargs)

    def get_user_collections(self, user_id: str, username: str) -> List[Dict]:
        if not self._cache.has_dimension("collections"):
            logger.warning("Cache miss for 'collections' — dimension not populated. Results may be incomplete.")
            return []
        collection_ids = self._cache.get_ids("collections", filter_fn=owner_filter(user_id))
        collections = []
        for cid in collection_ids:
            record = self._cache.get_record("collections", cid)
            collections.append({
                "id": cid,
                "name": record.name if record else None,
                "description": record.attrs.get("description") if record else None,
            })
        print_status("CACHE", f"Found {len(collections)} owned collections for {username}")
        return collections

    async def _get_collection_items(self, collection_luid: str) -> List[Dict]:
        endpoint = self._resolve_path("collection_items", collection_luid=collection_luid)
        response = await self._client._base.request(
            "GET", endpoint,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        data = response.json()
        return data.get("items", [])

    async def _create_collection(self, name: str, description: Optional[str] = None) -> Dict:
        endpoint = self._resolve_path("collections")
        payload = {"name": name}
        if description:
            payload["description"] = description
        response = await self._client._base.request(
            "POST", endpoint,
            content=json.dumps(payload),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        return response.json()

    async def _add_collection_items(self, collection_luid: str, items: List[Dict]) -> int:
        if not items:
            return 0
        endpoint = self._resolve_path("collection_items", collection_luid=collection_luid)
        added = 0
        for item in items:
            item_type = item.get("type") or item.get("contentType")
            item_id = None
            content = item.get("content")
            if isinstance(content, dict):
                item_id = content.get("luid") or content.get("id")
            if not item_type or not item_id:
                continue
            payload = {"items": [{"type": item_type, "content": {"id": item_id}}]}
            try:
                await self._client._base.request(
                    "POST", endpoint,
                    content=json.dumps(payload),
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                added += 1
            except Exception as e:
                if is_conflict_error(e):
                    added += 1
                else:
                    logger.warning(f"Failed to add item {item_type}/{item_id} to collection {collection_luid}: {e}")
        return added

    async def _get_collection_permissions(self, collection_luid: str) -> List[Dict]:
        endpoint = self._resolve_path("collection_permissions", collection_luid=collection_luid)
        root = await self._client.get(endpoint)
        grants = []
        for grant_el in _findall_any(root, "granteeCapabilities"):
            user_el = None
            group_el = None
            for child in grant_el:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag == "user":
                    user_el = child
                elif tag == "group":
                    group_el = child
            capabilities = []
            for cap_el in _findall_any(grant_el, "capability"):
                capabilities.append({
                    "name": cap_el.get("name"),
                    "mode": cap_el.get("mode"),
                })
            grants.append({
                "user_id": user_el.get("id") if user_el is not None else None,
                "group_id": group_el.get("id") if group_el is not None else None,
                "capabilities": capabilities,
            })
        return grants

    async def _add_collection_permission(
        self,
        collection_luid: str,
        user_id: Optional[str],
        group_id: Optional[str],
        capability_name: str,
        capability_mode: str,
    ) -> None:
        endpoint = self._resolve_path("collection_permissions", collection_luid=collection_luid)
        if user_id:
            grantee = f'<user id="{user_id}"/>'
        elif group_id:
            grantee = f'<group id="{group_id}"/>'
        else:
            return
        payload = (
            '<tsRequest><permissions><granteeCapabilities>'
            f'{grantee}'
            f'<capabilities><capability name="{capability_name}" mode="{capability_mode}"/></capabilities>'
            '</granteeCapabilities></permissions></tsRequest>'
        )
        await self._client.put(endpoint, payload)

    async def _clone_collection_permissions(
        self,
        old_collection_luid: str,
        new_collection_luid: str,
        old_user_id: str,
        new_user_id: str,
    ) -> int:
        grants = await self._get_collection_permissions(old_collection_luid)
        cloned = 0
        for grant in grants:
            grant_user_id = grant.get("user_id")
            grant_group_id = grant.get("group_id")
            for cap in grant.get("capabilities", []):
                target_user_id = new_user_id if grant_user_id == old_user_id else grant_user_id
                try:
                    await self._add_collection_permission(
                        new_collection_luid,
                        target_user_id,
                        grant_group_id,
                        cap["name"],
                        cap["mode"],
                    )
                    cloned += 1
                except Exception as e:
                    if is_conflict_error(e):
                        cloned += 1
                    else:
                        logger.warning(f"Failed to clone collection permission: {e}")
        return cloned

    async def _delete_collection(self, collection_luid: str) -> None:
        endpoint = self._resolve_path("collection_single", collection_luid=collection_luid)
        await self._client._base.request(
            "DELETE", endpoint,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    async def clone_collections(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Cloning collections: {old_username} -> {new_username}")
        collections = self.get_user_collections(old_user_id, old_username)
        cloned = 0

        for coll in collections:
            old_luid = coll["id"]
            coll_name = coll["name"] or "Untitled Collection"
            try:
                items = await self._get_collection_items(old_luid)
                new_coll = await self._create_collection(coll_name, coll.get("description"))
                new_luid = new_coll.get("luid") or new_coll.get("id")

                if not new_luid:
                    logger.warning(
                        f"Collection '{coll_name}': new collection ID is None. "
                        f"Skipping delete of old collection {old_luid}."
                    )
                    self._audit.log_failure(
                        AuditAction.CLONE_COLLECTION,
                        error_message="New collection ID is None — old collection preserved.",
                        old_username=old_username,
                        new_username=new_username,
                        object_type="collection",
                        object_name=coll_name,
                        object_id=old_luid,
                    )
                    continue

                items_added = await self._add_collection_items(new_luid, items)
                perms_cloned = await self._clone_collection_permissions(old_luid, new_luid, old_user_id, new_user_id)

                # C1 fix: Only delete old collection after confirming all items copied
                if len(items) > 0 and items_added < len(items):
                    logger.warning(
                        f"Collection '{coll_name}': only {items_added}/{len(items)} items copied. "
                        f"Skipping delete of old collection {old_luid} to prevent data loss."
                    )
                    self._audit.log_failure(
                        AuditAction.CLONE_COLLECTION,
                        error_message=f"Incomplete copy: {items_added}/{len(items)} items. Old collection preserved.",
                        old_username=old_username,
                        new_username=new_username,
                        object_type="collection",
                        object_name=coll_name,
                        object_id=old_luid,
                    )
                    continue

                await self._delete_collection(old_luid)

                cloned += 1
                self._audit.log_success(
                    AuditAction.CLONE_COLLECTION,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="collection",
                    object_name=coll_name,
                    object_id=old_luid,
                    details={
                        "new_collection_id": new_luid,
                        "items_added": items_added,
                        "permissions_cloned": perms_cloned,
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to clone collection {coll_name}: {e}")
                self._audit.log_failure(
                    AuditAction.CLONE_COLLECTION,
                    error_message=str(e),
                    old_username=old_username,
                    new_username=new_username,
                    object_type="collection",
                    object_name=coll_name,
                    object_id=old_luid,
                )

        print_status("DONE", f"Cloned {cloned} collections for {new_username}")
        return cloned

    async def transfer_collection_ownership(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Transferring collection ownership: {old_username} -> {new_username}")
        collections = self.get_user_collections(old_user_id, old_username)
        if not collections:
            print_status("DONE", f"No collections to transfer for {old_username}")
            return 0

        batch_endpoint = "/-/collections/batchUpdate"
        items_xml = "".join(
            f'<batchUpdateCollection luid="{coll["id"]}" ownerLuid="{new_user_id}"/>'
            for coll in collections
        )
        batch_payload = f'<batchUpdateCollections>{items_xml}</batchUpdateCollections>'

        try:
            await self._client._base.request(
                "POST", batch_endpoint,
                content=batch_payload,
                headers={"Accept": "application/json", "Content-Type": "application/xml"},
            )
            for coll in collections:
                self._cache.invalidate_owner("collections", coll["id"], new_user_id)
                self._audit.log_success(
                    AuditAction.CLONE_COLLECTION,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="collection",
                    object_name=coll.get("name") or "Untitled",
                    object_id=coll["id"],
                    details={"ownership_transferred": True, "method": "batchUpdate"},
                )
            print_status("DONE", f"Batch transferred {len(collections)} collections to {new_username}")
            return len(collections)
        except Exception as e:
            logger.warning(f"Batch XML collection ownership transfer failed: {e}")
            transferred = 0
            for coll in collections:
                coll_luid = coll["id"]
                coll_name = coll.get("name") or "Untitled"
                try:
                    single_payload = f'<batchUpdateCollections><batchUpdateCollection luid="{coll_luid}" ownerLuid="{new_user_id}"/></batchUpdateCollections>'
                    await self._client._base.request(
                        "POST", batch_endpoint,
                        content=single_payload,
                        headers={"Accept": "application/json", "Content-Type": "application/xml"},
                    )
                    transferred += 1
                    self._cache.invalidate_owner("collections", coll_luid, new_user_id)
                    self._audit.log_success(
                        AuditAction.CLONE_COLLECTION,
                        old_username=old_username,
                        new_username=new_username,
                        object_type="collection",
                        object_name=coll_name,
                        object_id=coll_luid,
                        details={"ownership_transferred": True},
                    )
                except Exception as inner_e:
                    logger.warning(f"Failed to transfer collection '{coll_name}': {inner_e}")
                    self._audit.log_failure(
                        AuditAction.CLONE_COLLECTION,
                        error_message=str(inner_e),
                        old_username=old_username,
                        new_username=new_username,
                        object_type="collection",
                        object_name=coll_name,
                        object_id=coll_luid,
                    )
            print_status("DONE", f"Transferred {transferred} collections to {new_username}")
            return transferred

    async def remove_collections(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing collections from {username}")
        collections = self.get_user_collections(user_id, username)
        removed = 0

        for coll in collections:
            try:
                await self._delete_collection(coll["id"])
                removed += 1
                self._audit.log_success(
                    AuditAction.REMOVE_COLLECTION,
                    old_username=username,
                    object_type="collection",
                    object_name=coll["name"],
                    object_id=coll["id"],
                )
            except Exception as e:
                logger.warning(f"Failed to remove collection {coll['name']}: {e}")
                self._audit.log_failure(
                    AuditAction.REMOVE_COLLECTION,
                    error_message=str(e),
                    old_username=username,
                    object_type="collection",
                    object_id=coll["id"],
                )

        print_status("DONE", f"Removed {removed} collections from {username}")
        return removed

    async def delete_collection(self, collection_id: str) -> None:
        await self._delete_collection(collection_id)

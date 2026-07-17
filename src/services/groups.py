# Tableau group membership cloning and removal service
# Co-authored with CoCo
from typing import List, Dict

from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache
from src.utils.paths import resolve_endpoint_path
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class GroupService:
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

    async def get_user_groups(self, user_id: str, username: str) -> List[Dict]:
        records = self._cache.get_parents_for_child("group_users", user_id)
        groups = []
        for r in records:
            group_id = r.attrs.get("_parent_id")
            if not group_id:
                continue
            group_record = self._cache.get_record("groups", group_id)
            group_name = group_record.name if group_record else r.name or group_id
            if group_name.lower() == "all users":
                continue
            groups.append({"id": group_id, "name": group_name})

        if not groups and not self._cache.has_dimension("group_users"):
            endpoint = self._resolve_path("user_groups", user_id=user_id)
            elements = await self._client.paginate_items(endpoint, "group")
            groups = [
                {"id": e.get("id"), "name": e.get("name")}
                for e in elements
                if e.get("name", "").lower() != "all users"
            ]

        print_status("CACHE", f"Found {len(groups)} groups for {username}")
        return groups

    async def add_user_to_group(self, group_id: str, user_id: str, username: str, group_name: str) -> None:
        endpoint = self._resolve_path("group_users", group_id=group_id)
        payload = f'<tsRequest><user id="{user_id}"/></tsRequest>'

        try:
            await self._client.post(endpoint, payload)
            self._audit.log_success(AuditAction.ADD_TO_GROUP, new_username=username, object_type="group", object_name=group_name, object_id=group_id)
        except Exception as e:
            if "409" in str(e) or "already" in str(e).lower():
                self._audit.log_skipped(AuditAction.ADD_TO_GROUP, reason="User already in group", new_username=username, object_type="group", object_name=group_name)
            else:
                self._audit.log_failure(AuditAction.ADD_TO_GROUP, error_message=str(e), new_username=username, object_type="group", object_id=group_id)
                raise

    async def remove_user_from_group(self, group_id: str, user_id: str, username: str, group_name: str) -> None:
        endpoint = self._resolve_path("group_user_single", group_id=group_id, user_id=user_id)
        try:
            await self._client.delete(endpoint)
            self._audit.log_success(AuditAction.REMOVE_FROM_GROUP, old_username=username, object_type="group", object_name=group_name, object_id=group_id)
        except Exception as e:
            self._audit.log_failure(AuditAction.REMOVE_FROM_GROUP, error_message=str(e), old_username=username, object_type="group", object_id=group_id)
            raise

    async def clone_groups(self, old_user_id: str, old_username: str, new_user_id: str, new_username: str) -> int:
        print_status("START", f"Cloning groups: {old_username} -> {new_username}")
        groups = await self.get_user_groups(old_user_id, old_username)
        cloned = 0
        for group in groups:
            try:
                await self.add_user_to_group(group["id"], new_user_id, new_username, group["name"])
                cloned += 1
            except Exception as e:
                logger.warning(f"Failed to clone group {group['name']}: {e}")
        print_status("DONE", f"Cloned {cloned} groups for {new_username}")
        return cloned

    async def remove_groups(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing groups from {username}")
        groups = await self.get_user_groups(user_id, username)
        removed = 0
        for group in groups:
            try:
                await self.remove_user_from_group(group["id"], user_id, username, group["name"])
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to remove group {group['name']}: {e}")
        print_status("DONE", f"Removed {removed} groups from {username}")
        return removed

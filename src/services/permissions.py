import asyncio
from typing import List, Dict

from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache
from src.utils.paths import resolve_endpoint_path
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_DEFAULT_PERM_CONTENT_TYPES = (
    "project_default_permissions_workbooks",
    "project_default_permissions_datasources",
    "project_default_permissions_flows",
    "project_default_permissions_virtualconnections",
    "project_default_permissions_databases",
    "project_default_permissions_tables",
    "project_default_permissions_metrics",
    "database_default_permissions",
)


class PermissionService:
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

    def _is_tabbed_workbook_view(self, content_type: str, content_id: str) -> bool:
        if content_type != "view_permissions":
            return False
        view_record = self._cache.get_record("views", content_id)
        if not view_record:
            return False
        wb = view_record.attrs.get("workbook")
        if not isinstance(wb, dict) or not wb.get("id"):
            return False
        wb_record = self._cache.get_record("workbooks", wb["id"])
        if not wb_record:
            return False
        show_tabs = wb_record.attrs.get("showTabs")
        return str(show_tabs).lower() == "true"

    def _filter_tabbed_view_perms(self, perms: List[Dict]) -> tuple[List[Dict], int]:
        filtered = []
        skipped = 0
        for p in perms:
            if self._is_tabbed_workbook_view(p.get("content_type", ""), p.get("content_id", "")):
                skipped += 1
            else:
                filtered.append(p)
        return filtered, skipped

    async def get_user_permissions(self, user_id: str, username: str) -> List[Dict]:
        perms = self._cache.get_user_explicit_permissions(user_id)
        perms, skipped = self._filter_tabbed_view_perms(perms)
        if skipped:
            print_status("CACHE", f"Skipped {skipped} view permissions on tabbed workbooks for {username}")
        print_status("CACHE", f"Found {len(perms)} explicit permissions for {username}")
        return perms

    async def get_user_permissions_with_skipped(self, user_id: str, username: str) -> tuple[List[Dict], int]:
        perms = self._cache.get_user_explicit_permissions(user_id)
        perms, skipped = self._filter_tabbed_view_perms(perms)
        if skipped:
            print_status("CACHE", f"Skipped {skipped} view permissions on tabbed workbooks for {username}")
        print_status("CACHE", f"Found {len(perms)} explicit permissions for {username}")
        return perms, skipped

    async def get_user_default_permissions(self, user_id: str, username: str) -> List[Dict]:
        defaults = self._cache.get_user_default_permissions(user_id)
        defaults, skipped = self._filter_tabbed_view_perms(defaults)
        if skipped:
            print_status("CACHE", f"Skipped {skipped} default view permissions on tabbed workbooks for {username}")
        print_status("CACHE", f"Found {len(defaults)} default permissions for {username}")
        return defaults

    async def get_user_default_permissions_with_skipped(self, user_id: str, username: str) -> tuple[List[Dict], int]:
        defaults = self._cache.get_user_default_permissions(user_id)
        defaults, skipped = self._filter_tabbed_view_perms(defaults)
        if skipped:
            print_status("CACHE", f"Skipped {skipped} default view permissions on tabbed workbooks for {username}")
        print_status("CACHE", f"Found {len(defaults)} default permissions for {username}")
        return defaults, skipped

    async def clone_permissions(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
        max_concurrent: int = 10,
    ) -> int:
        print_status("START", f"Cloning permissions: {old_username} -> {new_username}")
        explicit = await self.get_user_permissions(old_user_id, old_username)
        defaults = await self.get_user_default_permissions(old_user_id, old_username)
        all_perms = explicit + defaults

        grouped: Dict[str, List[Dict]] = {}
        for p in all_perms:
            key = f"{p['content_type']}:{p['content_id']}"
            grouped.setdefault(key, []).append(p)

        sem = asyncio.Semaphore(max_concurrent)
        cloned = {"n": 0}

        async def _clone_group(key: str, perm_list: List[Dict]) -> None:
            async with sem:
                content_type = perm_list[0]["content_type"]
                content_id = perm_list[0]["content_id"]
                is_default = perm_list[0].get("is_default", False)

                perm_path = self._resolve_perm_path(content_type, content_id)
                if not perm_path:
                    return

                caps_xml = "".join(
                    f'<capability name="{p["capability_name"]}" mode="{p["capability_mode"]}"/>'
                    for p in perm_list
                )
                payload = (
                    '<tsRequest><permissions>'
                    f'<granteeCapabilities>'
                    f'<user id="{new_user_id}"/>'
                    f'<capabilities>{caps_xml}</capabilities>'
                    f'</granteeCapabilities>'
                    '</permissions></tsRequest>'
                )

                action = AuditAction.CLONE_DEFAULT_PERMISSION if is_default else AuditAction.CLONE_PERMISSION

                try:
                    await self._client.put(perm_path, payload)
                    cloned["n"] += len(perm_list)
                    for p in perm_list:
                        self._audit.log_success(
                            action,
                            old_username=old_username,
                            new_username=new_username,
                            object_type=content_type,
                            object_id=content_id,
                            details={"capability": p["capability_name"], "mode": p["capability_mode"]},
                        )
                except Exception as e:
                    if "409" in str(e) or "already exists" in str(e).lower():
                        cloned["n"] += len(perm_list)
                        for p in perm_list:
                            self._audit.log_skipped(
                                action,
                                reason="Permission already exists",
                                new_username=new_username,
                                object_type=content_type,
                            )
                    else:
                        logger.warning(f"Failed to clone permissions on {content_type}/{content_id}: {e}")
                        for p in perm_list:
                            self._audit.log_failure(
                                action,
                                error_message=str(e),
                                new_username=new_username,
                                object_type=content_type,
                                object_id=content_id,
                            )

        print_status("PUT", f"Cloning {len(grouped)} permission groups ({max_concurrent} concurrent)...")
        await asyncio.gather(*[_clone_group(k, v) for k, v in grouped.items()])

        print_status("DONE", f"Cloned {cloned['n']} permissions (explicit + default) for {new_username}")
        return cloned["n"]

    async def remove_permissions(self, user_id: str, username: str, max_concurrent: int = 5) -> int:
        print_status("START", f"Removing permissions from {username}")
        explicit = await self.get_user_permissions(user_id, username)
        defaults = await self.get_user_default_permissions(user_id, username)
        all_perms = explicit + defaults

        sem = asyncio.Semaphore(max_concurrent)
        removed = {"n": 0}

        async def _remove_perm(p: Dict) -> None:
            async with sem:
                perm_path = self._resolve_perm_path(p["content_type"], p["content_id"])
                if not perm_path:
                    return

                delete_path = f"{perm_path}/users/{user_id}/{p['capability_name']}/{p['capability_mode']}"
                action = AuditAction.REMOVE_DEFAULT_PERMISSION if p.get("is_default") else AuditAction.REMOVE_PERMISSION

                try:
                    await self._client.delete(delete_path)
                    removed["n"] += 1
                    self._audit.log_success(
                        action,
                        old_username=username,
                        object_type=p["content_type"],
                        object_id=p["content_id"],
                        details={"capability": p["capability_name"]},
                    )
                except Exception as e:
                    logger.warning(f"Failed to remove permission: {e}")
                    self._audit.log_failure(
                        action,
                        error_message=str(e),
                        old_username=username,
                        object_type=p["content_type"],
                        object_id=p["content_id"],
                    )

        print_status("DELETE", f"Removing {len(all_perms)} permissions ({max_concurrent} concurrent)...")
        await asyncio.gather(*[_remove_perm(p) for p in all_perms])

        print_status("DONE", f"Removed {removed['n']} permissions from {username}")
        return removed["n"]

    def _resolve_perm_path(self, content_type: str, content_id: str) -> str | None:
        ep_config = self._endpoints.get(content_type)
        if not ep_config:
            return None

        perm_path_tpl = ep_config.get("permissions_endpoint")
        if perm_path_tpl:
            return resolve_endpoint_path(perm_path_tpl, self._client.site_id, id=content_id)

        path_tpl = ep_config.get("path")
        if not path_tpl:
            return None

        placeholders = {
            "site_id": self._client.site_id,
            "workbook_id": content_id,
            "view_id": content_id,
            "datasource_id": content_id,
            "flow_id": content_id,
            "project_id": content_id,
            "project_luid": content_id,
            "virtual_connection_luid": content_id,
            "database_luid": content_id,
            "table_id": content_id,
            "collection_luid": content_id,
        }
        try:
            resolved = path_tpl.format(**{k: v for k, v in placeholders.items() if f"{{{k}}}" in path_tpl})
            return resolve_endpoint_path(resolved, self._client.site_id)
        except KeyError:
            return None

# Tableau content ownership transfer service
# Co-authored with CoCo
import asyncio
from typing import List, Dict, Optional

from src.api.client import TableauAPIClient, _find_any
from src.utils.cache import DimensionCache, owner_filter
from src.utils.paths import resolve_endpoint_path, resolve_element_tag
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_PERSONAL_SPACE_CONTENT_TYPES = ("workbooks", "datasources", "flows")


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
        self._migration_project_id: Optional[str] = None

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

    async def transfer_single(
        self,
        content_type: str,
        content_id: str,
        new_owner_id: str,
        new_owner_username: str,
    ) -> None:
        """Transfer a single item's ownership. Used by rollback."""
        ep_config = self._endpoints.get(content_type)
        if not ep_config or not ep_config.get("ownership_transferable"):
            raise ValueError(f"Content type '{content_type}' is not ownership-transferable")

        update_path = resolve_endpoint_path(ep_config["path"], self._client.site_id) + f"/{content_id}"
        tag = resolve_element_tag(ep_config, content_type)
        payload = (
            f'<tsRequest><{tag} id="{content_id}">'
            f'<owner id="{new_owner_id}"/>'
            f'</{tag}></tsRequest>'
        )
        await self._client.put(update_path, payload)
        self._cache.invalidate_owner(content_type, content_id, new_owner_id)
        print_status("PUT", f"Transferred {content_type}/{content_id} to {new_owner_username}")

    async def ensure_migration_project(self, project_name: str) -> str:
        if self._migration_project_id:
            return self._migration_project_id

        all_project_ids = self._cache.get_ids("projects")
        for pid in all_project_ids:
            record = self._cache.get_record("projects", pid)
            if record and record.name and record.name.lower() == project_name.lower():
                parent = record.attrs.get("parentProjectId")
                if not parent:
                    self._migration_project_id = pid
                    print_status("PROJECT", f"Found existing top-level project: '{project_name}' ({pid})")
                    return pid

        projects_path = resolve_endpoint_path(
            self._endpoints["projects"]["path"], self._client.site_id
        )
        payload = (
            f'<tsRequest><project name="{project_name}" '
            f'description="Content relocated from Personal Space during user migration"/>'
            f'</tsRequest>'
        )
        root = await self._client.post(projects_path, payload)
        project_el = _find_any(root, "project")
        if project_el is None:
            raise RuntimeError(f"Failed to create project '{project_name}' — no project element in response")

        new_id = project_el.get("id")
        self._migration_project_id = new_id
        print_status("PROJECT", f"Created top-level project: '{project_name}' ({new_id})")

        self._audit.log_success(
            AuditAction.REASSIGN_OWNERSHIP,
            old_username="system",
            object_type="project",
            object_name=project_name,
            object_id=new_id,
            details={"action": "created_migration_project"},
        )
        return new_id

    def _is_personal_space(self, record) -> bool:
        project = record.attrs.get("project")
        if isinstance(project, dict):
            return not project.get("id")
        return True

    async def relocate_personal_space_content(
        self,
        user_id: str,
        username: str,
        target_project_id: str,
    ) -> int:
        print_status("START", f"Relocating Personal Space content for {username}")
        relocated = 0

        for content_type in _PERSONAL_SPACE_CONTENT_TYPES:
            if not self._cache.has_dimension(content_type):
                continue

            item_ids = self._cache.get_ids(content_type, filter_fn=owner_filter(user_id))
            for item_id in item_ids:
                record = self._cache.get_record(content_type, item_id)
                if not record or not self._is_personal_space(record):
                    continue

                ep_config = self._endpoints.get(content_type)
                if not ep_config:
                    continue

                update_path = resolve_endpoint_path(ep_config["path"], self._client.site_id) + f"/{item_id}"
                tag = resolve_element_tag(ep_config, content_type)
                payload = (
                    f'<tsRequest><{tag} id="{item_id}">'
                    f'<project id="{target_project_id}"/>'
                    f'</{tag}></tsRequest>'
                )

                try:
                    await self._client.put(update_path, payload)
                    relocated += 1

                    if isinstance(record.attrs.get("project"), dict):
                        record.attrs["project"]["id"] = target_project_id
                    else:
                        record.attrs["project"] = {"id": target_project_id}

                    self._audit.log_success(
                        AuditAction.REASSIGN_OWNERSHIP,
                        old_username=username,
                        object_type=content_type,
                        object_name=record.name,
                        object_id=item_id,
                        details={"action": "relocated_from_personal_space", "target_project": target_project_id},
                    )
                except Exception as e:
                    logger.warning(f"Failed to relocate {content_type}/{item_id} from Personal Space: {e}")
                    self._audit.log_failure(
                        AuditAction.REASSIGN_OWNERSHIP,
                        error_message=str(e),
                        old_username=username,
                        object_type=content_type,
                        object_id=item_id,
                        details={"action": "relocate_personal_space_failed"},
                    )

        print_status("DONE", f"Relocated {relocated} items from Personal Space for {username}")
        return relocated

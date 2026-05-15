from typing import List

from src.api.client import TableauAPIClient, _findall_any, _find_any
from src.utils.cache import DimensionCache, owner_filter
from models.impact import UXArtifact
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class CustomViewService:
    def __init__(self, client: TableauAPIClient, audit: AuditLogger, cache: DimensionCache):
        self._client = client
        self._audit = audit
        self._cache = cache

    async def get_user_custom_views(self, user_id: str, username: str) -> List[UXArtifact]:
        cv_ids = self._cache.get_ids("custom_views", filter_fn=owner_filter(user_id))
        cvs = []
        for cid in cv_ids:
            record = self._cache.get_record("custom_views", cid)
            view = record.attrs.get("view", {})
            workbook = record.attrs.get("workbook", {})
            cvs.append(UXArtifact(
                artifact_id=record.id,
                artifact_type="custom_view",
                content_type="view",
                content_id=view.get("id") if isinstance(view, dict) else None,
                content_name=record.name,
                details={
                    "workbook_id": workbook.get("id") if isinstance(workbook, dict) else None,
                    "workbook_name": workbook.get("name") if isinstance(workbook, dict) else None,
                    "view_name": view.get("name") if isinstance(view, dict) else None,
                },
            ))
        print_status("CACHE", f"Found {len(cvs)} custom views for {username}")
        return cvs

    async def _is_default_for_user(self, custom_view_id: str, user_id: str) -> bool:
        endpoint = f"/sites/{self._client.site_id}/customviews/{custom_view_id}/default/users"
        try:
            root = await self._client.get(endpoint)
            for user_el in _findall_any(root, "user"):
                if user_el.get("id") == user_id:
                    return True
        except Exception as e:
            logger.debug(f"Failed to check default users for custom view {custom_view_id}: {e}")
        return False

    async def _set_default_for_user(self, custom_view_id: str, user_id: str) -> None:
        endpoint = f"/sites/{self._client.site_id}/customviews/{custom_view_id}/default/users/{user_id}"
        await self._client.post(endpoint, "<tsRequest/>")

    async def _remove_default_for_user(self, custom_view_id: str, user_id: str) -> None:
        endpoint = f"/sites/{self._client.site_id}/customviews/{custom_view_id}/default/users/{user_id}"
        await self._client.delete(endpoint)

    async def clone_custom_views(
        self,
        old_user_id: str,
        old_username: str,
        new_user_id: str,
        new_username: str,
    ) -> int:
        print_status("START", f"Transferring custom view ownership: {old_username} -> {new_username}")
        custom_views = await self.get_user_custom_views(old_user_id, old_username)
        transferred = 0

        for cv in custom_views:
            was_default = await self._is_default_for_user(cv.artifact_id, old_user_id)

            endpoint = f"/sites/{self._client.site_id}/customviews/{cv.artifact_id}"
            payload = (
                f'<tsRequest><customView>'
                f'<owner id="{new_user_id}"/>'
                f'</customView></tsRequest>'
            )
            try:
                await self._client.put(endpoint, payload)
                transferred += 1

                if was_default:
                    try:
                        await self._set_default_for_user(cv.artifact_id, new_user_id)
                        await self._remove_default_for_user(cv.artifact_id, old_user_id)
                    except Exception as e:
                        logger.warning(f"Failed to transfer default status for custom view {cv.artifact_id}: {e}")

                self._audit.log_success(
                    AuditAction.CLONE_CUSTOM_VIEW,
                    old_username=old_username,
                    new_username=new_username,
                    object_type="custom_view",
                    object_name=cv.content_name,
                    object_id=cv.artifact_id,
                    details={"ownership_transferred": True, "default_transferred": was_default},
                )
            except Exception as e:
                logger.warning(f"Failed to transfer custom view {cv.artifact_id}: {e}")
                self._audit.log_failure(
                    AuditAction.CLONE_CUSTOM_VIEW,
                    error_message=str(e),
                    old_username=old_username,
                    new_username=new_username,
                    object_type="custom_view",
                    object_id=cv.artifact_id,
                )

        print_status("DONE", f"Transferred {transferred} custom views to {new_username}")
        return transferred

    async def remove_custom_views(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing custom views from {username}")
        custom_views = await self.get_user_custom_views(user_id, username)
        removed = 0

        for cv in custom_views:
            endpoint = f"/sites/{self._client.site_id}/customviews/{cv.artifact_id}"
            try:
                await self._client.delete(endpoint)
                removed += 1
                self._audit.log_success(AuditAction.REMOVE_CUSTOM_VIEW, old_username=username, object_type="custom_view", object_id=cv.artifact_id)
            except Exception as e:
                logger.warning(f"Failed to remove custom view: {e}")
                self._audit.log_failure(AuditAction.REMOVE_CUSTOM_VIEW, error_message=str(e), old_username=username, object_id=cv.artifact_id)

        print_status("DONE", f"Removed {removed} custom views from {username}")
        return removed

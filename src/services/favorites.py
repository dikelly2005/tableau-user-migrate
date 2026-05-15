from typing import List

from src.api.client import TableauAPIClient, _findall_any
from src.utils.cache import DimensionCache
from models.impact import UXArtifact
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_FAVORITE_CONTENT_TYPES = ("workbook", "view", "datasource", "project", "flow")


class FavoriteService:
    def __init__(self, client: TableauAPIClient, audit: AuditLogger, cache: DimensionCache):
        self._client = client
        self._audit = audit
        self._cache = cache

    async def get_user_favorites(self, user_id: str, username: str) -> List[UXArtifact]:
        records = self._cache.get_child_records("user_favorites", user_id)
        if records:
            favorites = []
            for r in records:
                for ct in _FAVORITE_CONTENT_TYPES:
                    content = r.attrs.get(ct)
                    if isinstance(content, dict) and content.get("id"):
                        favorites.append(UXArtifact(
                            artifact_id=content["id"],
                            artifact_type="favorite",
                            content_type=ct,
                            content_id=content["id"],
                            content_name=content.get("name") or r.name,
                        ))
                        break
            print_status("CACHE", f"Found {len(favorites)} favorites for {username}")
            return favorites

        endpoint = f"/sites/{self._client.site_id}/favorites/{user_id}"
        favorites = []
        try:
            root = await self._client.get(endpoint)
            for fav_type in _FAVORITE_CONTENT_TYPES:
                for elem in _findall_any(root, fav_type):
                    favorites.append(UXArtifact(
                        artifact_id=elem.get("id"),
                        artifact_type="favorite",
                        content_type=fav_type,
                        content_id=elem.get("id"),
                        content_name=elem.get("name"),
                    ))
        except Exception as e:
            logger.debug(f"Failed to fetch favorites for {username}: {e}")

        print_status("GET", f"Found {len(favorites)} favorites for {username}")
        return favorites

    async def clone_favorites(self, old_user_id: str, old_username: str, new_user_id: str, new_username: str) -> int:
        print_status("START", f"Cloning favorites: {old_username} -> {new_username}")
        favorites = await self.get_user_favorites(old_user_id, old_username)
        cloned = 0

        for fav in favorites:
            endpoint = f"/sites/{self._client.site_id}/favorites/{new_user_id}"
            label = fav.content_name or "Favorite"
            payload = (
                f'<tsRequest><favorite label="{label}">'
                f'<{fav.content_type} id="{fav.content_id}"/>'
                f'</favorite></tsRequest>'
            )

            try:
                await self._client.post(endpoint, payload)
                cloned += 1
                self._audit.log_success(AuditAction.CLONE_FAVORITE, new_username=new_username, object_type="favorite", object_name=fav.content_name)
            except Exception as e:
                if "409" in str(e) or "already exists" in str(e).lower():
                    self._audit.log_skipped(AuditAction.CLONE_FAVORITE, reason="Favorite already exists", new_username=new_username)
                    cloned += 1
                else:
                    logger.warning(f"Failed to clone favorite: {e}")
                    self._audit.log_failure(AuditAction.CLONE_FAVORITE, error_message=str(e), new_username=new_username)

        print_status("DONE", f"Cloned {cloned} favorites for {new_username}")
        return cloned

    async def remove_favorites(self, user_id: str, username: str) -> int:
        print_status("START", f"Removing favorites from {username}")
        favorites = await self.get_user_favorites(user_id, username)
        removed = 0

        for fav in favorites:
            endpoint = f"/sites/{self._client.site_id}/favorites/{user_id}/{fav.content_type}/{fav.content_id}"
            try:
                await self._client.delete(endpoint)
                removed += 1
                self._audit.log_success(AuditAction.REMOVE_FAVORITE, old_username=username, object_type="favorite", object_name=fav.content_name)
            except Exception as e:
                logger.warning(f"Failed to remove favorite: {e}")

        print_status("DONE", f"Removed {removed} favorites from {username}")
        return removed

from typing import Optional, Dict

from src.api.client import TableauAPIClient, _findall_any, _find_any, _strip_ns
from src.utils.cache import DimensionCache
from reporting.audit import AuditLogger, AuditAction
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class UserService:
    def __init__(self, client: TableauAPIClient, audit: AuditLogger, cache: Optional[DimensionCache] = None):
        self._client = client
        self._audit = audit
        self._cache = cache

    async def lookup_user(self, username: str, live: bool = False) -> Optional[Dict]:
        normalized = username.lower()

        if not live and self._cache and self._cache.has_dimension("users"):
            for r in self._cache.get_all_records("users"):
                if r.name and r.name.lower() == normalized:
                    return {
                        "id": r.id,
                        "name": r.name,
                        "site_role": r.type or "",
                        "auth_setting": r.attrs.get("authSetting"),
                    }

        endpoint = f"/sites/{self._client.site_id}/users?filter=name:eq:{username}"
        root = await self._client.get(endpoint)
        users = _findall_any(root, "user")

        if not users:
            return None

        user_elem = users[0]
        return {
            "id": user_elem.get("id"),
            "name": user_elem.get("name"),
            "site_role": user_elem.get("siteRole", ""),
            "auth_setting": user_elem.get("authSetting"),
        }

    async def create_user(self, username: str, site_role: str, auth_setting: Optional[str] = None) -> Dict:
        endpoint = f"/sites/{self._client.site_id}/users"
        auth_attr = f' authSetting="{auth_setting}"' if auth_setting else ""
        payload = (
            '<tsRequest>'
            f'<user name="{username}" siteRole="{site_role}"{auth_attr}/>'
            '</tsRequest>'
        )

        try:
            root = await self._client.post(endpoint, payload)
            user_elem = _find_any(root, "user")
            result = {
                "id": user_elem.get("id") if user_elem is not None else "",
                "name": username,
                "site_role": site_role,
                "created": True,
            }
            self._audit.log_success(
                AuditAction.USER_CREATE,
                new_username=username,
                details={"site_role": site_role},
            )
            print_status("POST", f"Created user: {username} ({site_role})")
            return result
        except Exception as e:
            if "409" in str(e) or "already exists" in str(e).lower():
                existing = await self.lookup_user(username, live=True)
                if existing:
                    needs_update = (
                        existing["site_role"] != site_role
                        or (auth_setting and existing.get("auth_setting") != auth_setting)
                    )
                    if needs_update:
                        await self.update_user(existing["id"], username, site_role, auth_setting)
                        existing["site_role"] = site_role
                        if auth_setting:
                            existing["auth_setting"] = auth_setting
                        self._audit.log_success(
                            AuditAction.USER_REUSE,
                            new_username=username,
                            details={"updated_role": site_role, "updated_auth": auth_setting},
                        )
                        print_status("PUT", f"Updated existing user: {username} (siteRole={site_role}, authSetting={auth_setting})")
                    else:
                        self._audit.log_success(
                            AuditAction.USER_REUSE,
                            new_username=username,
                            details={"existing_role": existing["site_role"]},
                        )
                        print_status("SKIP", f"User already exists: {username}")
                    return {**existing, "created": False}
            raise

    async def update_user(self, user_id: str, username: str, site_role: str, auth_setting: Optional[str] = None) -> None:
        endpoint = f"/sites/{self._client.site_id}/users/{user_id}"
        auth_attr = f' authSetting="{auth_setting}"' if auth_setting else ""
        payload = f'<tsRequest><user siteRole="{site_role}"{auth_attr}/></tsRequest>'
        await self._client.put(endpoint, payload)
        parts = [f"siteRole={site_role}"]
        if auth_setting:
            parts.append(f"authSetting={auth_setting}")
        print_status("PUT", f"Updated user {username}: {', '.join(parts)}")

    async def deactivate_user(self, user_id: str, username: str) -> None:
        await self.update_user(user_id, username, "Unlicensed")
        self._audit.log_success(
            AuditAction.USER_DEACTIVATE,
            old_username=username,
        )
        print_status("PUT", f"Deactivated (unlicensed): {username}")

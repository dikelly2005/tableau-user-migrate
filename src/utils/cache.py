# Tableau dimension cache with permission refresh support
# Co-authored with CoCo
import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from src.utils.exceptions import APIError
from src.utils.logging_config import get_logger, print_status
from src.utils.paths import resolve_endpoint_path, resolve_element_tag

logger = get_logger(__name__)


@dataclass
class DimensionRecord:
    id: str
    type: Optional[str] = None
    license_type: Optional[str] = None
    name: Optional[str] = None
    attrs: dict = field(default_factory=dict)


class DimensionCache:

    DEFAULT_ATTRIBUTE_MAPPING = {
        "id_fields": ["id", "luid"],
        "primary_key": None,
        "type_field": None,
        "license_field": None,
        "name_field": "name",
        "extra_attrs": [],
    }

    ATTRIBUTE_MAPPINGS = {
        "users": {
            "id_fields": ["id", "luid"],
            "type_field": "siteRole",
            "license_field": "licenseType",
            "name_field": "name",
            "extra_attrs": ["email", "fullName", "authSetting", "lastLogin"],
        },
        "groups": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": "minimumSiteRole",
            "name_field": "name",
            "extra_attrs": ["domainName"],
        },
        "projects": {
            "id_fields": ["id", "luid"],
            "type_field": "contentPermissions",
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["parentProjectId", "topLevelProject", "owner"],
        },
        "workbooks": {
            "id_fields": ["id", "luid"],
            "type_field": "contentUrl",
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["project", "owner", "createdAt", "updatedAt", "showTabs"],
        },
        "views": {
            "id_fields": ["id", "luid"],
            "type_field": "viewUrlName",
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["contentUrl", "workbook"],
        },
        "datasources": {
            "id_fields": ["id", "luid"],
            "type_field": "type",
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["project", "owner", "contentUrl", "isCertified"],
        },
        "flows": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["project", "owner", "createdAt", "updatedAt"],
        },
        "virtual_connections": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["project", "owner", "isCertified"],
        },
        "databases": {
            "id_fields": ["id", "luid"],
            "type_field": "type",
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["connectionType", "isEmbedded"],
        },
        "tables": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["schema", "isEmbedded"],
        },
        "collections": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["owner", "description", "ownerAlias"],
        },
        "custom_views": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["view", "workbook", "owner", "shared"],
        },
        "subscriptions": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": None,
            "name_field": "subject",
            "extra_attrs": ["user", "content", "schedule"],
        },
        "data_alerts": {
            "id_fields": ["id", "luid"],
            "type_field": None,
            "license_field": None,
            "name_field": "subject",
            "extra_attrs": ["owner", "view", "creatorId"],
        },
        "pulse_definitions": {
            "id_fields": ["id"],
            "type_field": None,
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["specification", "owner"],
        },
        "pulse_subscriptions": {
            "id_fields": ["id"],
            "type_field": None,
            "license_field": None,
            "name_field": None,
            "extra_attrs": ["metric_id", "user_id", "condition", "follower_id", "creator_id"],
        },
        "webhooks": {
            "id_fields": ["id"],
            "type_field": None,
            "license_field": None,
            "name_field": "name",
            "extra_attrs": ["owner", "event", "url"],
        },
        "pulse_alerts": {
            "id_fields": ["id"],
            "type_field": None,
            "license_field": None,
            "name_field": None,
            "extra_attrs": ["owner_id", "creator_id", "metric_id", "condition", "threshold"],
        },
    }

    CHILD_ATTRIBUTE_MAPPINGS = {
        "group_users": {
            "id_fields": ["id"],
            "name_field": "name",
            "extra_attrs": ["siteRole"],
        },
        "user_favorites": {
            "id_fields": ["label"],
            "name_field": "label",
            "extra_attrs": ["workbook", "view", "datasource", "project", "flow"],
        },
        "custom_view_default_users": {
            "id_fields": ["id"],
            "name_field": "name",
            "extra_attrs": ["siteRole"],
        },
    }

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        ttl_hours: int = 24,
        primary_endpoints: Optional[set] = None,
    ):
        self._dimensions: dict[str, dict[str, DimensionRecord]] = {}
        self._permissions: dict[str, list[dict]] = {}
        self._cache_path = cache_path
        self._ttl = timedelta(hours=ttl_hours)
        self._created_at: Optional[datetime] = None
        self._site_id: Optional[str] = None
        all_endpoints = set(self.ATTRIBUTE_MAPPINGS.keys()) | set(self.CHILD_ATTRIBUTE_MAPPINGS.keys())
        self._primary_endpoints = primary_endpoints or all_endpoints

    def populate(
        self,
        endpoint_name: str,
        items: list[dict],
        primary_key: Optional[str] = None,
    ) -> int:
        if endpoint_name not in self._primary_endpoints:
            return 0

        mapping = self.ATTRIBUTE_MAPPINGS.get(endpoint_name, self.DEFAULT_ATTRIBUTE_MAPPING)
        id_fields = mapping.get("id_fields", ["id", "luid"])
        config_pk = primary_key or mapping.get("primary_key")
        type_field = mapping.get("type_field")
        license_field = mapping.get("license_field")
        name_field = mapping.get("name_field", "name")
        extra_attrs = mapping.get("extra_attrs", [])

        if endpoint_name not in self._dimensions:
            self._dimensions[endpoint_name] = {}
        else:
            self._dimensions[endpoint_name].clear()

        count = 0
        for item in items:
            item_id = None

            if config_pk:
                item_id = item.get(config_pk)

            if not item_id:
                for id_field in id_fields:
                    if id_field in item:
                        item_id = item[id_field]
                        break

            if not item_id:
                continue

            record = DimensionRecord(
                id=item_id,
                type=item.get(type_field) if type_field else None,
                license_type=item.get(license_field) if license_field else None,
                name=item.get(name_field) if name_field else None,
                attrs={k: item.get(k) for k in extra_attrs if k in item},
            )
            self._dimensions[endpoint_name][item_id] = record
            count += 1

        self._created_at = datetime.now()
        print_status("CACHE", f"Populated {endpoint_name}: {count} records")
        return count

    def get_ids(
        self,
        endpoint_name: str,
        filter_fn: Optional[Callable[[DimensionRecord], bool]] = None,
    ) -> list[str]:
        if endpoint_name not in self._dimensions:
            return []
        records = self._dimensions[endpoint_name].values()
        if filter_fn:
            records = [r for r in records if filter_fn(r)]
        return [r.id for r in records]

    def get_record(self, endpoint_name: str, item_id: str) -> Optional[DimensionRecord]:
        return self._dimensions.get(endpoint_name, {}).get(item_id)

    def get_all_records(self, endpoint_name: str) -> list[DimensionRecord]:
        return list(self._dimensions.get(endpoint_name, {}).values())

    def filter_ids(
        self,
        endpoint_name: str,
        ids: list[str],
        filter_fn: Callable[[DimensionRecord], bool],
    ) -> list[str]:
        if endpoint_name not in self._dimensions:
            return ids
        result = []
        for item_id in ids:
            record = self._dimensions[endpoint_name].get(item_id)
            if record and filter_fn(record):
                result.append(item_id)
        return result

    def has_dimension(self, endpoint_name: str) -> bool:
        return endpoint_name in self._dimensions and len(self._dimensions[endpoint_name]) > 0

    def is_primary(self, endpoint_name: str) -> bool:
        return endpoint_name in self._primary_endpoints

    def count(self, endpoint_name: str) -> int:
        return len(self._dimensions.get(endpoint_name, {}))

    @property
    def total_records(self) -> int:
        return sum(len(records) for records in self._dimensions.values())

    def clear(self, endpoint_name: Optional[str] = None) -> None:
        if endpoint_name:
            self._dimensions.pop(endpoint_name, None)
        else:
            self._dimensions.clear()
            self._created_at = None

    def refresh(self) -> None:
        record_count = self.total_records
        perm_count = self.total_permissions
        self._dimensions.clear()
        self._permissions.clear()
        self._created_at = None
        print_status("CACHE", f"Cleared {record_count} records + {perm_count} permissions for refresh")

    def populate_child(
        self,
        endpoint_name: str,
        parent_id: str,
        items: list[dict],
    ) -> int:
        mapping = self.CHILD_ATTRIBUTE_MAPPINGS.get(endpoint_name, self.DEFAULT_ATTRIBUTE_MAPPING)
        id_fields = mapping.get("id_fields", ["id"])
        name_field = mapping.get("name_field", "name")
        extra_attrs = mapping.get("extra_attrs", [])

        if endpoint_name not in self._dimensions:
            self._dimensions[endpoint_name] = {}

        count = 0
        for idx, item in enumerate(items):
            item_id = None
            for id_field in id_fields:
                if id_field in item:
                    item_id = item[id_field]
                    break

            if not item_id:
                for attr in extra_attrs:
                    nested = item.get(attr)
                    if isinstance(nested, dict) and nested.get("id"):
                        item_id = nested["id"]
                        break

            if not item_id:
                item_id = f"_idx_{idx}"

            composite_id = f"{parent_id}:{item_id}"
            attrs = {k: item.get(k) for k in extra_attrs if k in item}
            attrs["_parent_id"] = parent_id

            record = DimensionRecord(
                id=composite_id,
                name=item.get(name_field),
                attrs=attrs,
            )
            self._dimensions[endpoint_name][composite_id] = record
            count += 1

        return count

    def get_child_ids(
        self,
        endpoint_name: str,
        parent_id: str,
    ) -> list[str]:
        if endpoint_name not in self._dimensions:
            return []
        return [
            r.id.split(":", 1)[1] if ":" in r.id else r.id
            for r in self._dimensions[endpoint_name].values()
            if r.attrs.get("_parent_id") == parent_id
        ]

    def get_child_records(
        self,
        endpoint_name: str,
        parent_id: str,
    ) -> list[DimensionRecord]:
        if endpoint_name not in self._dimensions:
            return []
        return [
            r for r in self._dimensions[endpoint_name].values()
            if r.attrs.get("_parent_id") == parent_id
        ]

    def get_parents_for_child(
        self,
        endpoint_name: str,
        child_id: str,
    ) -> list[DimensionRecord]:
        if endpoint_name not in self._dimensions:
            return []
        results = []
        for r in self._dimensions[endpoint_name].values():
            parts = r.id.split(":", 1)
            if len(parts) == 2 and parts[1] == child_id:
                results.append(r)
        return results

    def invalidate_owner(self, endpoint_name: str, item_id: str, new_owner_id: str) -> None:
        record = self._dimensions.get(endpoint_name, {}).get(item_id)
        if record and "owner" in record.attrs:
            if isinstance(record.attrs["owner"], dict):
                record.attrs["owner"]["id"] = new_owner_id
            else:
                record.attrs["owner"] = {"id": new_owner_id}

    def summary(self) -> dict[str, int]:
        result = {name: len(records) for name, records in self._dimensions.items()}
        if self._permissions:
            result["_permissions_total"] = self.total_permissions
        return result

    def populate_permissions(self, content_type: str, content_id: str, grants: list[dict], is_default: bool = False) -> int:
        if content_type not in self._permissions:
            self._permissions[content_type] = []
        count = 0
        for grant in grants:
            user_id = grant.get("user_id")
            group_id = grant.get("group_id")
            for cap in grant.get("capabilities", []):
                self._permissions[content_type].append({
                    "content_type": content_type,
                    "content_id": content_id,
                    "user_id": user_id,
                    "group_id": group_id,
                    "capability_name": cap.get("name"),
                    "capability_mode": cap.get("mode"),
                    "is_default": is_default,
                })
                count += 1
        return count

    def get_user_permissions(self, user_id: str) -> list[dict]:
        results = []
        for perms in self._permissions.values():
            for p in perms:
                if p.get("user_id") == user_id:
                    results.append(p)
        return results

    def get_user_default_permissions(self, user_id: str) -> list[dict]:
        return [p for p in self.get_user_permissions(user_id) if p.get("is_default")]

    def get_user_explicit_permissions(self, user_id: str) -> list[dict]:
        return [p for p in self.get_user_permissions(user_id) if not p.get("is_default")]

    @property
    def total_permissions(self) -> int:
        return sum(len(perms) for perms in self._permissions.values())

    @property
    def permissions_summary(self) -> dict[str, int]:
        return {ct: len(perms) for ct, perms in self._permissions.items()}

    def to_dict(self) -> dict:
        return {
            "site_id": self._site_id,
            "created_at": self._created_at.isoformat() if self._created_at else None,
            "dimensions": {
                name: {rid: asdict(rec) for rid, rec in records.items()}
                for name, records in self._dimensions.items()
            },
            "permissions": self._permissions,
        }

    @classmethod
    def from_dict(cls, data: dict, cache_path: Optional[Path] = None, ttl_hours: int = 24) -> "DimensionCache":
        cache = cls(cache_path=cache_path, ttl_hours=ttl_hours)
        cache._site_id = data.get("site_id")
        cache._created_at = (
            datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
        )
        for name, records in data.get("dimensions", {}).items():
            cache._dimensions[name] = {
                rid: DimensionRecord(**rec) for rid, rec in records.items()
            }
        cache._permissions = data.get("permissions", {})
        return cache

    def save(self, path: Optional[Path] = None) -> bool:
        save_path = path or self._cache_path
        if not save_path:
            return False
        self._created_at = self._created_at or datetime.now()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        print_status("CACHE", f"Saved {self.total_records} records to {save_path}")
        return True

    @classmethod
    def load(cls, path: Path, ttl_hours: int = 24, site_id: Optional[str] = None) -> Optional["DimensionCache"]:
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            cache = cls.from_dict(data, cache_path=path, ttl_hours=ttl_hours)
            if cache.is_expired():
                print_status("CACHE", f"Cache file expired (>{ttl_hours}h TTL)")
                return None
            if site_id and cache._site_id and cache._site_id != site_id:
                print_status("CACHE", f"Cache site_id mismatch (cached: {cache._site_id}, current: {site_id}) — rebuilding")
                return None
            print_status("CACHE", f"Loaded {cache.total_records} records from {path}")
            return cache
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to load cache file: {e}")
            return None

    def is_expired(self) -> bool:
        if not self._created_at:
            return True
        if self._ttl.total_seconds() == 0:
            return True
        return datetime.now() - self._created_at > self._ttl

    async def refresh_permissions(self, client=None, endpoints_config: Optional[dict] = None, max_concurrent: int = 10) -> None:
        """Re-fetch all permission data from the API, clearing stale permission cache."""
        if not client or not endpoints_config or not self._site_id:
            self._permissions.clear()
            print_status("CACHE", "Permission cache cleared (no client available for live refresh)")
            return

        self._permissions.clear()
        endpoints = endpoints_config.get("endpoints", {})
        sem = asyncio.Semaphore(max_concurrent)
        site_id = self._site_id

        perm_endpoints = {
            name: cfg for name, cfg in endpoints.items()
            if cfg.get("parent") and (name.endswith("_permissions") or "default_permissions" in name)
        }

        if not perm_endpoints:
            print_status("CACHE", "No permission endpoints configured — skipping refresh")
            return

        total_perm_records = 0

        locked_project_ids = set()
        for pid in self.get_ids("projects"):
            rec = self.get_record("projects", pid)
            if rec and rec.type and rec.type.lower() in ("lockedtoproject", "lockedtoprojectwithoutnested"):
                locked_project_ids.add(pid)
        project_content_parents = {"workbooks", "views", "datasources", "flows"}

        for perm_name, perm_config in perm_endpoints.items():
            parent_name = perm_config["parent"]
            if not self.has_dimension(parent_name):
                continue

            parent_ids = self.get_ids(parent_name)
            if not parent_ids:
                continue

            if parent_name in project_content_parents:
                filtered_ids = []
                for pid in parent_ids:
                    rec = self.get_record(parent_name, pid)
                    if not rec:
                        filtered_ids.append(pid)
                        continue
                    project_attr = rec.attrs.get("project", {})
                    project_id = project_attr.get("id") if isinstance(project_attr, dict) else None
                    if not project_id and parent_name == "views":
                        workbook_attr = rec.attrs.get("workbook", {})
                        wb_id = workbook_attr.get("id") if isinstance(workbook_attr, dict) else None
                        if wb_id:
                            wb_rec = self.get_record("workbooks", wb_id)
                            if wb_rec:
                                wb_project = wb_rec.attrs.get("project", {})
                                project_id = wb_project.get("id") if isinstance(wb_project, dict) else None
                    if not project_id:
                        continue
                    if project_id in locked_project_ids:
                        continue
                    filtered_ids.append(pid)
                parent_ids = filtered_ids

            path_tpl = perm_config["path"]
            is_default = "default_permissions" in perm_name
            perm_counts = {"total": 0}

            async def _fetch_perms(pid: str, _path_tpl=path_tpl, _perm_name=perm_name, _is_default=is_default) -> None:
                async with sem:
                    placeholders = {
                        "site_id": site_id,
                        "workbook_id": pid, "view_id": pid,
                        "datasource_id": pid, "flow_id": pid,
                        "project_id": pid, "project_luid": pid,
                        "virtual_connection_luid": pid,
                        "database_luid": pid, "table_id": pid,
                        "collection_luid": pid,
                    }
                    try:
                        raw_path = _path_tpl.format(**{k: v for k, v in placeholders.items() if f"{{{k}}}" in _path_tpl})
                        api_path = f"/{raw_path}" if not raw_path.startswith("/") else raw_path
                        root = await client.get(api_path)
                        grants = self._parse_permission_grants(root)
                        count = self.populate_permissions(_perm_name, pid, grants, is_default=_is_default)
                        perm_counts["total"] += count
                    except Exception as e:
                        logger.debug(f"Failed to refresh {_perm_name} for {pid}: {e}")

            await asyncio.gather(*[_fetch_perms(pid) for pid in parent_ids])
            total_perm_records += perm_counts["total"]

        print_status("CACHE", f"Permission cache refreshed: {total_perm_records} grants")


    async def _enrich_collections(self, client, endpoints_config: dict, site_id: str, sem: asyncio.Semaphore) -> None:
        if not self.has_dimension("collections") or not self.has_dimension("users"):
            return

        user_lookup = {}
        for record in self.get_all_records("users"):
            if record.name:
                user_lookup[record.name.lower()] = record.id

        enriched = 0
        for record in self.get_all_records("collections"):
            owner_alias = record.attrs.get("ownerAlias")
            if owner_alias:
                owner_id = user_lookup.get(owner_alias.lower())
                if owner_id:
                    record.attrs["owner"] = {"id": owner_id, "name": owner_alias}
                    enriched += 1

        print_status("CACHE", f"Enriched {enriched}/{self.count('collections')} collections with owner data")

    async def _enrich_virtual_connections(self, client, site_id: str, sem: asyncio.Semaphore) -> None:
        # Skip VC enrichment — the primary list endpoint already returns correct owner data.
        # The revisions endpoint returns the publisher (whoever last saved), not the current owner.
        return

    async def warmup(self, client, endpoints_config: dict, site_id: str, max_concurrent: int = 10) -> None:
        print_status("CACHE", "Warming dimension cache...")
        self._site_id = site_id
        endpoints = endpoints_config.get("endpoints", {})
        sem = asyncio.Semaphore(max_concurrent)

        primary = {k: v for k, v in endpoints.items() if v.get("cache") and not v.get("parent")}
        total_primary = len(primary)
        completed = {"n": 0}

        async def _fetch_primary(ep_name: str, ep_config: dict) -> None:
            async with sem:
                api_path = resolve_endpoint_path(ep_config["path"], site_id)
                params = ep_config.get("params")
                if params:
                    qs = "&".join(f"{k}={v}" for k, v in params.items())
                    api_path = f"{api_path}?{qs}"
                element_tag = resolve_element_tag(ep_config, ep_name)
                pk = ep_config.get("primary_key")
                fmt = ep_config.get("format", "xml")

                max_ep_retries = 3
                for ep_attempt in range(max_ep_retries):
                    try:
                        if fmt == "json":
                            json_headers = {"Accept": "application/json", "Content-Type": "application/json"}
                            rk = ep_config.get("response_key")
                            use_cursor = ep_config.get("pagination") == "cursor"

                            if use_cursor:
                                all_items: list[dict] = []
                                page_token = None
                                while True:
                                    paged_path = api_path
                                    sep = "&" if "?" in paged_path else "?"
                                    paged_path += f"{sep}page_size=100"
                                    if page_token:
                                        paged_path += f"&page_token={page_token}"
                                    response = await client._base.request("GET", paged_path, headers=json_headers)
                                    data = response.json()
                                    page_items = data.get(rk, []) if rk else (data if isinstance(data, list) else [])
                                    if not isinstance(page_items, list):
                                        page_items = [page_items] if page_items else []
                                    all_items.extend(page_items)
                                    page_token = data.get("next_page_token")
                                    if not page_token:
                                        break
                                self.populate(ep_name, all_items, primary_key=pk)
                            else:
                                response = await client._base.request("GET", api_path, headers=json_headers)
                                data = response.json()
                                if rk:
                                    items = data.get(rk, [])
                                elif isinstance(data, list):
                                    items = data
                                else:
                                    items = []
                                if not isinstance(items, list):
                                    items = [items] if items else []
                                self.populate(ep_name, items, primary_key=pk)
                        else:
                            elements = await client.paginate_items(api_path, element_tag)
                            items = self._elements_to_dicts(elements)
                            self.populate(ep_name, items, primary_key=pk)
                        break
                    except APIError as e:
                        if e.status_code in (401, 429, 500, 502, 503, 504) and ep_attempt < max_ep_retries - 1:
                            wait = 2 ** ep_attempt
                            logger.warning(f"Retrying {ep_name} after {e.status_code} (attempt {ep_attempt + 1}/{max_ep_retries})")
                            await asyncio.sleep(wait)
                            continue
                        logger.warning(f"Failed to cache {ep_name}: {e}")
                    except Exception as e:
                        logger.warning(f"Failed to cache {ep_name}: {e}")
                        break
                completed["n"] += 1
                print_status("CACHE", f"[{completed['n']}/{total_primary}] {ep_name} done")

        await asyncio.gather(*[
            _fetch_primary(name, cfg) for name, cfg in primary.items()
        ])

        print_status("CACHE", f"Primary endpoints cached: {self.summary()}")

        await self._enrich_collections(client, endpoints_config, site_id, sem)
        await self._enrich_virtual_connections(client, site_id, sem)

        child_endpoints = {
            name: cfg for name, cfg in endpoints.items()
            if cfg.get("parent") and name in self.CHILD_ATTRIBUTE_MAPPINGS
        }

        for ep_name, ep_config in child_endpoints.items():
            parent_name = ep_config["parent"]
            if not self.has_dimension(parent_name):
                logger.warning(f"Skipping child {ep_name}: parent {parent_name} not cached")
                continue

            parent_ids = self.get_ids(parent_name)
            path_tpl = ep_config["path"]
            element_tag = resolve_element_tag(ep_config, ep_name)
            fmt = ep_config.get("format", "xml")
            child_counts = {"total": 0}

            async def _fetch_child(pid: str) -> None:
                async with sem:
                    try:
                        placeholders = {
                            "site_id": site_id,
                            "user_id": pid,
                            "group_id": pid,
                            "custom_view_id": pid,
                        }
                        raw_path = path_tpl.format(**{k: v for k, v in placeholders.items() if f"{{{k}}}" in path_tpl})
                        api_path = f"/{raw_path}" if not raw_path.startswith("/") else raw_path

                        if fmt == "json":
                            response = await client._base.request("GET", api_path)
                            data = response.json()
                            rk = ep_config.get("response_key")
                            items = data.get(rk, []) if rk else (data if isinstance(data, list) else [])
                        else:
                            elements = await client.paginate_items(api_path, element_tag)
                            items = self._elements_to_dicts(elements)

                        count = self.populate_child(ep_name, pid, items)
                        child_counts["total"] += count
                    except Exception as e:
                        logger.debug(f"Failed to cache {ep_name} for parent {pid}: {e}")

            print_status("CACHE", f"Fetching {ep_name} across {len(parent_ids)} parents ({max_concurrent} concurrent)...")
            await asyncio.gather(*[_fetch_child(pid) for pid in parent_ids])
            print_status("CACHE", f"Populated {ep_name}: {child_counts['total']} records across {len(parent_ids)} parents")

        print_status("CACHE", f"Warmup complete: {self.total_records} total records cached")
        print_status("CACHE", f"Dimensions: {self.summary()}")

        perm_endpoints = {
            name: cfg for name, cfg in endpoints.items()
            if cfg.get("parent") and name.endswith("_permissions")
        }
        default_perm_endpoints = {
            name: cfg for name, cfg in endpoints.items()
            if cfg.get("parent") and "default_permissions" in name
        }
        all_perm_endpoints = {**perm_endpoints, **default_perm_endpoints}

        if all_perm_endpoints:
            print_status("CACHE", f"Building permission table ({len(all_perm_endpoints)} permission types)...")
            total_perm_records = 0

            locked_project_ids = set()
            for pid in self.get_ids("projects"):
                rec = self.get_record("projects", pid)
                if rec and rec.type and rec.type.lower() in ("lockedtoproject", "lockedtoprojectwithoutnested"):
                    locked_project_ids.add(pid)
            if locked_project_ids:
                print_status("CACHE", f"Found {len(locked_project_ids)} locked projects — skipping content permission scans for their children")
            else:
                all_types = set()
                for pid in self.get_ids("projects"):
                    rec = self.get_record("projects", pid)
                    if rec and rec.type:
                        all_types.add(rec.type)
                print_status("CACHE", f"Project contentPermissions types found: {all_types or 'none'}")

            project_content_parents = {"workbooks", "views", "datasources", "flows"}

            for perm_name, perm_config in all_perm_endpoints.items():
                parent_name = perm_config["parent"]
                if not self.has_dimension(parent_name):
                    continue

                parent_ids = self.get_ids(parent_name)
                if not parent_ids:
                    continue

                if parent_name in project_content_parents:
                    original_count = len(parent_ids)
                    filtered_ids = []
                    for pid in parent_ids:
                        rec = self.get_record(parent_name, pid)
                        if not rec:
                            filtered_ids.append(pid)
                            continue
                        project_attr = rec.attrs.get("project", {})
                        project_id = project_attr.get("id") if isinstance(project_attr, dict) else None
                        if not project_id and parent_name == "views":
                            workbook_attr = rec.attrs.get("workbook", {})
                            wb_id = workbook_attr.get("id") if isinstance(workbook_attr, dict) else None
                            if wb_id:
                                wb_rec = self.get_record("workbooks", wb_id)
                                if wb_rec:
                                    wb_project = wb_rec.attrs.get("project", {})
                                    project_id = wb_project.get("id") if isinstance(wb_project, dict) else None
                        if not project_id:
                            continue
                        if project_id in locked_project_ids:
                            continue
                        filtered_ids.append(pid)
                    skipped = original_count - len(filtered_ids)
                    if skipped:
                        print_status("CACHE", f"Skipping {skipped}/{original_count} {parent_name} (locked projects + Personal Space) for {perm_name}")
                    parent_ids = filtered_ids

                path_tpl = perm_config["path"]
                is_default = "default_permissions" in perm_name
                perm_counts = {"total": 0}

                async def _fetch_perms(pid: str, _path_tpl=path_tpl, _perm_name=perm_name, _parent_name=parent_name, _is_default=is_default) -> None:
                    async with sem:
                        placeholders = {
                            "site_id": site_id,
                            "workbook_id": pid,
                            "view_id": pid,
                            "datasource_id": pid,
                            "flow_id": pid,
                            "project_id": pid,
                            "project_luid": pid,
                            "virtual_connection_luid": pid,
                            "database_luid": pid,
                            "table_id": pid,
                            "collection_luid": pid,
                        }
                        try:
                            raw_path = _path_tpl.format(**{k: v for k, v in placeholders.items() if f"{{{k}}}" in _path_tpl})
                            api_path = f"/{raw_path}" if not raw_path.startswith("/") else raw_path
                            root = await client.get(api_path)
                            grants = self._parse_permission_grants(root)
                            count = self.populate_permissions(_perm_name, pid, grants, is_default=_is_default)
                            perm_counts["total"] += count
                        except Exception as e:
                            logger.debug(f"Failed to fetch {_perm_name} for {pid}: {e}")

                label = f"{perm_name} (default)" if is_default else f"{parent_name} permissions"
                print_status("CACHE", f"Scanning {label} ({len(parent_ids)} items, {max_concurrent} concurrent)...")
                await asyncio.gather(*[_fetch_perms(pid) for pid in parent_ids])
                total_perm_records += perm_counts["total"]

            print_status("CACHE", f"Permission table complete: {total_perm_records} grants cached")
            print_status("CACHE", f"Permissions by type: {self.permissions_summary}")

    @staticmethod
    def _parse_permission_grants(root) -> list[dict]:
        import xml.etree.ElementTree as ET
        ns = "http://tableau.com/api"
        grants = []

        for gc in root.iter():
            tag = gc.tag.split("}")[-1] if "}" in gc.tag else gc.tag
            if tag != "granteeCapabilities":
                continue

            user_id = None
            group_id = None
            capabilities = []

            for child in gc:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child_tag == "user":
                    user_id = child.get("id")
                elif child_tag == "group":
                    group_id = child.get("id")
                elif child_tag == "capabilities":
                    for cap in child:
                        cap_tag = cap.tag.split("}")[-1] if "}" in cap.tag else cap.tag
                        if cap_tag == "capability":
                            capabilities.append({
                                "name": cap.get("name"),
                                "mode": cap.get("mode"),
                            })

            if (user_id or group_id) and capabilities:
                grants.append({
                    "user_id": user_id,
                    "group_id": group_id,
                    "capabilities": capabilities,
                })

        return grants

    @staticmethod
    def _elements_to_dicts(elements: list) -> list[dict]:
        items = []
        for elem in elements:
            items.append(DimensionCache._xml_element_to_dict(elem))
        return items

    @staticmethod
    def _xml_element_to_dict(element) -> dict:
        result = dict(element.attrib)
        for child in element:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if len(child) == 0 and not child.attrib:
                result[child_tag] = child.text
            elif len(child) == 0 and child.attrib:
                result[child_tag] = dict(child.attrib)
                if child.text:
                    result[child_tag]["_text"] = child.text
            else:
                child_dict = DimensionCache._xml_element_to_dict(child)
                if child_tag in result:
                    if not isinstance(result[child_tag], list):
                        result[child_tag] = [result[child_tag]]
                    result[child_tag].append(child_dict)
                else:
                    result[child_tag] = child_dict
        return result


def owner_filter(user_id: str) -> Callable[[DimensionRecord], bool]:
    def _filter(record: DimensionRecord) -> bool:
        owner = record.attrs.get("owner")
        if isinstance(owner, dict):
            return owner.get("id") == user_id or owner.get("luid") == user_id
        return False
    return _filter


def user_filter(user_id: str) -> Callable[[DimensionRecord], bool]:
    def _filter(record: DimensionRecord) -> bool:
        user = record.attrs.get("user")
        if isinstance(user, dict):
            return user.get("id") == user_id
        return False
    return _filter

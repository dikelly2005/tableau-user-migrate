import json
from pathlib import Path
from typing import List, Dict

from src.utils.cache import DimensionCache, owner_filter
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)


class UserReportMixin:

    _SINGULAR_TO_PLURAL = {
        "workbook": "workbooks",
        "view": "views",
        "datasource": "datasources",
        "flow": "flows",
        "project": "projects",
        "collection": "collections",
        "virtual_connection": "virtual_connections",
        "custom_view": "custom_views",
    }

    _OWNED_CONTENT_TYPES = (
        "workbooks", "views", "datasources", "flows",
        "virtual_connections", "projects", "collections", "custom_views",
    )

    def _resolve_project_path(self, project_id: str | None) -> str | None:
        if not project_id:
            return None
        segments = []
        seen = set()
        current = project_id
        while current and current not in seen:
            seen.add(current)
            record = self._cache.get_record("projects", current)
            if not record:
                break
            segments.append(record.name or current)
            parent = record.attrs.get("parentProjectId")
            if isinstance(parent, dict):
                current = parent.get("id")
            elif isinstance(parent, str) and parent:
                current = parent
            else:
                break
        if not segments:
            return None
        segments.reverse()
        return "/".join(segments)

    def _resolve_content_path(self, content_type: str, content_id: str) -> str | None:
        if not content_id:
            return None

        if content_type == "projects":
            return self._resolve_project_path(content_id)

        record = self._cache.get_record(content_type, content_id)
        if not record:
            return None

        project_id = None
        project = record.attrs.get("project")
        if isinstance(project, dict):
            project_id = project.get("id")

        if not project_id and content_type == "views":
            wb = record.attrs.get("workbook")
            if isinstance(wb, dict) and wb.get("id"):
                wb_record = self._cache.get_record("workbooks", wb["id"])
                if wb_record:
                    wb_project = wb_record.attrs.get("project")
                    if isinstance(wb_project, dict):
                        project_id = wb_project.get("id")

        if not project_id and content_type == "custom_views":
            wb = record.attrs.get("workbook")
            if isinstance(wb, dict) and wb.get("id"):
                wb_record = self._cache.get_record("workbooks", wb["id"])
                if wb_record:
                    wb_project = wb_record.attrs.get("project")
                    if isinstance(wb_project, dict):
                        project_id = wb_project.get("id")

        project_path = self._resolve_project_path(project_id)
        content_name = record.name or content_id

        if project_path:
            return f"{project_path}/{content_name}"
        if not project_id:
            return f"Personal Space/{content_name}"
        return content_name

    def _get_all_owned_content(self, user_id: str, username: str) -> List[Dict]:
        owned = []
        for ep_name in self._OWNED_CONTENT_TYPES:
            if not self._cache.has_dimension(ep_name):
                continue
            item_ids = self._cache.get_ids(ep_name, filter_fn=owner_filter(user_id))
            for item_id in item_ids:
                record = self._cache.get_record(ep_name, item_id)
                owned.append({
                    "content_type": ep_name,
                    "content_id": item_id,
                    "content_name": record.name if record else None,
                    "path": self._resolve_content_path(ep_name, item_id),
                })
        print_status("CACHE", f"Found {len(owned)} total owned items for {username}")
        return owned

    def _resolve_content_name(self, content_type: str, content_id: str) -> str | None:
        if not content_id:
            return None
        record = self._cache.get_record(content_type, content_id)
        if record:
            return record.name
        return None

    def _build_permission_detail(self, perm: Dict) -> Dict:
        content_type = perm.get("content_type", "")
        content_id = perm.get("content_id", "")
        base_type = content_type.replace("_permissions", "").rstrip("s")
        parent_type = content_type
        if "default_permissions" in content_type:
            if content_type.startswith("database"):
                parent_type = "databases"
            else:
                parent_type = "projects"
        elif content_type.endswith("_permissions"):
            parts = content_type.split("_permissions")[0]
            parent_type = parts if parts in ("workbooks", "views", "datasources", "flows", "projects", "virtual_connections", "databases", "tables", "collections") else parts + "s"
        content_name = self._resolve_content_name(parent_type, content_id)
        return {
            "content_type": content_type,
            "content_id": content_id,
            "content_name": content_name,
            "path": self._resolve_content_path(parent_type, content_id),
            "capability_name": perm.get("capability_name"),
            "capability_mode": perm.get("capability_mode"),
            "is_default": perm.get("is_default", False),
        }

    def _group_permissions_by_content(self, perms: List[Dict]) -> List[Dict]:
        grouped: Dict[str, Dict] = {}
        for p in perms:
            detail = self._build_permission_detail(p)
            key = f"{detail['content_type']}:{detail['content_id']}"
            if key not in grouped:
                grouped[key] = {
                    "content_type": detail["content_type"],
                    "content_id": detail["content_id"],
                    "content_name": detail["content_name"],
                    "path": detail["path"],
                    "is_default": detail["is_default"],
                    "capabilities": [],
                }
            grouped[key]["capabilities"].append({
                "name": detail["capability_name"],
                "mode": detail["capability_mode"],
            })
        return list(grouped.values())

    def _build_user_report(
        self,
        old_username: str,
        new_username: str,
        old_user: Dict | None,
        new_user: Dict | None,
        explicit_perms: List[Dict],
        default_perms: List[Dict],
        groups: List[Dict],
        owned: List[Dict],
        favorites: List,
        subs: List,
        alerts: List,
        cvs: List,
        collections: List[Dict],
        pulse_subs: List[Dict],
        pulse_alerts: List[Dict],
        webhooks: List[Dict],
        validation_errors: List[str],
        validation_warnings: List[str],
    ) -> Dict:
        report = {
            "old_username": old_username,
            "new_username": new_username,
            "old_user": {
                "found": old_user is not None,
                "role": old_user.get("site_role") if old_user else None,
            },
            "new_user": {
                "found": new_user is not None,
                "role": new_user.get("site_role") if new_user else None,
            },
            "owned_content": [
                {
                    "content_type": item["content_type"],
                    "content_id": item["content_id"],
                    "content_name": item.get("content_name"),
                    "path": item.get("path"),
                }
                for item in owned
            ],
            "explicit_permissions": self._group_permissions_by_content(explicit_perms),
            "default_permissions": self._group_permissions_by_content(default_perms),
            "favorites": [
                {
                    "content_type": f.content_type,
                    "content_id": f.content_id,
                    "content_name": f.content_name,
                    "path": self._resolve_content_path(self._SINGULAR_TO_PLURAL.get(f.content_type, f.content_type), f.content_id),
                }
                for f in favorites
            ],
            "groups": [
                {"group_id": g["id"], "group_name": g["name"]}
                for g in groups
            ],
            "subscriptions": [
                {
                    "subscription_id": s.artifact_id,
                    "subject": s.details.get("subject") if s.details else None,
                    "content_type": s.content_type,
                    "content_id": s.content_id,
                    "path": self._resolve_content_path(self._SINGULAR_TO_PLURAL.get(s.content_type, s.content_type or ""), s.content_id),
                }
                for s in subs
            ],
            "alerts": [
                {
                    "alert_id": a.artifact_id,
                    "subject": a.details.get("subject") if a.details else None,
                    "view_id": a.content_id,
                    "path": self._resolve_content_path("views", a.content_id),
                }
                for a in alerts
            ],
            "custom_views": [
                {
                    "custom_view_id": cv.artifact_id,
                    "custom_view_name": cv.content_name,
                    "view_id": cv.content_id,
                    "workbook_id": cv.details.get("workbook_id") if cv.details else None,
                    "workbook_name": cv.details.get("workbook_name") if cv.details else None,
                    "path": self._resolve_content_path("custom_views", cv.artifact_id),
                }
                for cv in cvs
            ],
            "collections": [
                {
                    "collection_id": c["id"],
                    "collection_name": c["name"],
                    "description": c.get("description"),
                }
                for c in collections
            ],
            "pulse_subscriptions": [
                {
                    "subscription_id": ps.get("id"),
                    "metric_id": ps.get("metric_id"),
                    "condition": ps.get("condition"),
                }
                for ps in pulse_subs
            ],
            "pulse_alerts": [
                {
                    "alert_id": pa.get("id"),
                    "metric_id": pa.get("metric_id"),
                    "condition": pa.get("condition"),
                    "threshold": pa.get("threshold"),
                }
                for pa in pulse_alerts
            ],
            "webhooks": [
                {
                    "webhook_id": wh.get("webhook_id"),
                    "webhook_name": wh.get("webhook_name"),
                    "event": wh.get("event"),
                    "url": wh.get("url"),
                }
                for wh in webhooks
            ],
            "summary": {
                "owned_content_count": len(owned),
                "explicit_permission_count": len(explicit_perms),
                "default_permission_count": len(default_perms),
                "favorite_count": len(favorites),
                "group_count": len(groups),
                "subscription_count": len(subs),
                "alert_count": len(alerts),
                "custom_view_count": len(cvs),
                "collection_count": len(collections),
                "pulse_subscription_count": len(pulse_subs),
                "pulse_alert_count": len(pulse_alerts),
                "webhook_count": len(webhooks),
            },
            "validation_errors": validation_errors,
            "validation_warnings": validation_warnings,
        }
        return report

    @staticmethod
    def _safe_filename(username: str) -> str:
        return username.replace("@", "_at_").replace(".", "_").replace("/", "_").replace("\\", "_")

    async def _generate_user_report(
        self,
        old_username: str,
        new_username: str,
        user_reports_dir: Path,
    ) -> Dict:
        old_user = await self._users.lookup_user(old_username)
        new_user = await self._users.lookup_user(new_username)

        validation_errors = []
        validation_warnings = []

        empty_schedule = {
            "content_needing_credential_reauth": [],
        }

        if not old_user:
            validation_errors.append(f"Old user not found: {old_username}")
            report = self._build_user_report(
                old_username, new_username, old_user, new_user,
                [], [], [], [], [], [], [], [], [], [], [], [],
                validation_errors, validation_warnings,
            )
            report_path = user_reports_dir / f"{self._safe_filename(old_username)}.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            return {
                "old_username": old_username,
                "new_username": new_username,
                "old_user_exists": False,
                "new_user_exists": new_user is not None,
                "validation_errors": validation_errors,
                "report_file": report_path.name,
            }

        old_user_id = old_user["id"]

        explicit_perms, explicit_skipped = await self._permissions.get_user_permissions_with_skipped(old_user_id, old_username)
        default_perms, default_skipped = await self._permissions.get_user_default_permissions_with_skipped(old_user_id, old_username)
        all_perms = explicit_perms + default_perms
        tabbed_skipped = explicit_skipped + default_skipped

        if tabbed_skipped:
            validation_warnings.append(
                f"{tabbed_skipped} view permissions skipped — inherited from tabbed workbooks (showTabs=true)"
            )

        groups = await self._groups.get_user_groups(old_user_id, old_username)
        owned = self._get_all_owned_content(old_user_id, old_username)
        favorites = await self._favorites.get_user_favorites(old_user_id, old_username)
        subs = await self._subscriptions.get_user_subscriptions(old_user_id, old_username)
        alerts = await self._alerts.get_user_alerts(old_user_id, old_username)
        cvs = await self._custom_views.get_user_custom_views(old_user_id, old_username)
        collections = self._collections.get_user_collections(old_user_id, old_username)

        pulse_subs = []
        if hasattr(self, "_pulse") and self._pulse:
            pulse_subs = self._pulse.get_user_pulse_subscriptions(old_user_id, old_username)

        pulse_alerts = []
        if hasattr(self, "_pulse") and self._pulse:
            pulse_alerts = self._pulse.get_user_pulse_alerts(old_user_id, old_username)

        webhooks = []
        if hasattr(self, "_webhooks") and self._webhooks:
            webhooks = self._webhooks.get_user_webhooks(old_user_id, old_username)

        if alerts:
            validation_warnings.append(
                f"{len(alerts)} data alerts will have ownership transferred to new user"
            )

        if cvs:
            validation_warnings.append(
                f"{len(cvs)} custom views will have ownership transferred to new user"
            )

        if collections:
            validation_warnings.append(
                f"{len(collections)} collections will be cloned (recreated with items and permissions transferred to new user)"
            )

        if pulse_subs:
            validation_warnings.append(
                f"{len(pulse_subs)} Pulse subscriptions will be cloned to new user"
            )

        if pulse_alerts:
            validation_warnings.append(
                f"{len(pulse_alerts)} Pulse alerts will have ownership transferred to new user"
            )

        if webhooks:
            validation_warnings.append(
                f"{len(webhooks)} webhooks will have ownership transferred to new user"
            )

        _CREDENTIAL_CONTENT_TYPES = ("workbooks", "datasources", "flows", "virtual_connections")
        credential_content = [o for o in owned if o["content_type"] in _CREDENTIAL_CONTENT_TYPES]
        if credential_content:
            validation_warnings.append(
                f"{len(credential_content)} owned content items (workbooks, datasources, flows, virtual connections) "
                "may have embedded data connections — after migration, re-establish saved credentials "
                "and re-authenticate data connections on each item"
            )

        schedule_summary = {"content_needing_credential_reauth": []}

        if new_user:
            validation_warnings.append(
                f"New user already exists with role: {new_user.get('site_role')}"
            )

        validation_warnings.append(
            "Personal Access Tokens (PATs), Connected App tokens, OAuth saved credentials, "
            "and embedded datasource passwords cannot be migrated — new user must recreate all authentication credentials"
        )

        report = self._build_user_report(
            old_username, new_username, old_user, new_user,
            explicit_perms, default_perms, groups, owned,
            favorites, subs, alerts, cvs, collections, pulse_subs, pulse_alerts, webhooks,
            validation_errors, validation_warnings,
        )
        report_path = user_reports_dir / f"{self._safe_filename(old_username)}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        return {
            "old_username": old_username,
            "new_username": new_username,
            "old_user_exists": True,
            "new_user_exists": new_user is not None,
            "old_user_role": old_user.get("site_role"),
            "new_user_role": new_user.get("site_role") if new_user else None,
            "permission_count": len(all_perms),
            "group_count": len(groups),
            "content_count": len(owned),
            "favorite_count": len(favorites),
            "subscription_count": len(subs),
            "alert_count": len(alerts),
            "custom_view_count": len(cvs),
            "collection_count": len(collections),
            "pulse_subscription_count": len(pulse_subs),
            "pulse_alert_count": len(pulse_alerts),
            "webhook_count": len(webhooks),
            "content_needing_credential_reauth": schedule_summary["content_needing_credential_reauth"],
            "validation_errors": validation_errors,
            "validation_warnings": validation_warnings,
            "report_file": report_path.name,
        }

    async def _generate_all_user_reports(
        self,
        mappings: List[Dict],
        audit_dir: Path,
        mode: str,
    ) -> List[Dict]:
        user_reports_dir = audit_dir / f"user_reports_{mode}"
        user_reports_dir.mkdir(parents=True, exist_ok=True)

        impact_results = []
        for m in mappings:
            try:
                impact = await self._generate_user_report(
                    m["old_username"], m["new_username"], user_reports_dir,
                )
                impact_results.append(impact)
            except Exception as e:
                logger.error(f"Failed to generate report for {m['old_username']}: {e}")
                impact_results.append({
                    "old_username": m["old_username"],
                    "new_username": m["new_username"],
                    "error": str(e),
                })

        summary_path = audit_dir / "impact_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "impacts": impact_results}, f, indent=2)

        total_perms = sum(r.get("permission_count", 0) for r in impact_results)
        total_groups = sum(r.get("group_count", 0) for r in impact_results)
        total_content = sum(r.get("content_count", 0) for r in impact_results)
        total_tasks = sum(r.get("extract_refresh_task_count", 0) + r.get("flow_run_task_count", 0) for r in impact_results)
        errors = sum(1 for r in impact_results if r.get("validation_errors") or r.get("error"))

        print_status("REPORT", (
            f"User reports ({mode}): {len(mappings)} users | "
            f"{total_perms} permissions | {total_groups} groups | "
            f"{total_content} owned items | {total_tasks} scheduled tasks | {errors} errors"
        ))
        print_status("AUDIT", f"Impact summary: {summary_path}")
        print_status("AUDIT", f"Per-user reports: {user_reports_dir}/")

        return impact_results

from pathlib import Path
from typing import List, Dict
from datetime import datetime

from config.settings import Settings
from src.utils.cache import DimensionCache
from src.utils.checkpoint import CheckpointManager
from reporting.audit import AuditLogger, AuditAction, AuditResult
from src.utils.logging_config import get_logger, print_status
from src.workflows.report_mixin import UserReportMixin

logger = get_logger(__name__)


class DryRunWorkflow(UserReportMixin):
    def __init__(
        self,
        user_service,
        permission_service,
        group_service,
        ownership_service,
        favorite_service,
        subscription_service,
        alert_service,
        custom_view_service,
        collection_service,
        pulse_service,
        webhook_service,
        cache: DimensionCache,
        checkpoint: CheckpointManager,
        audit: AuditLogger,
        settings: Settings,
    ):
        self._users = user_service
        self._permissions = permission_service
        self._groups = group_service
        self._ownership = ownership_service
        self._favorites = favorite_service
        self._subscriptions = subscription_service
        self._alerts = alert_service
        self._custom_views = custom_view_service
        self._collections = collection_service
        self._pulse = pulse_service
        self._webhooks = webhook_service
        self._cache = cache
        self._checkpoint = checkpoint
        self._audit = audit
        self._settings = settings

    async def execute(self, mappings: List[Dict], audit_dir: Path) -> None:
        print_status("START", f"Dry run: analyzing {len(mappings)} user mappings")
        self._audit.log(AuditAction.DRY_RUN_START, result=AuditResult.SUCCESS)

        user_reports_dir = audit_dir / "user_reports_dry_run"
        user_reports_dir.mkdir(parents=True, exist_ok=True)

        impact_results = []
        for m in mappings:
            old_username = m["old_username"]
            self._checkpoint.mark_in_progress(old_username)
            try:
                impact = await self._generate_user_report(
                    old_username, m["new_username"],
                    user_reports_dir,
                )
                impact_results.append(impact)
                self._checkpoint.mark_completed(old_username)
            except Exception as e:
                self._checkpoint.mark_failed(old_username, str(e))
                logger.error(f"Dry run failed for {old_username}: {e}")
                impact_results.append({
                    "old_username": old_username,
                    "new_username": m["new_username"],
                    "error": str(e),
                })

        import json
        summary_path = audit_dir / "impact_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({"mode": "dry-run", "impacts": impact_results}, f, indent=2)

        total_perms = sum(r.get("permission_count", 0) for r in impact_results)
        total_groups = sum(r.get("group_count", 0) for r in impact_results)
        total_content = sum(r.get("content_count", 0) for r in impact_results)
        errors = sum(1 for r in impact_results if r.get("validation_errors") or r.get("error"))

        print_status("DONE", (
            f"Dry run complete: {len(mappings)} users | "
            f"{total_perms} permissions | {total_groups} groups | "
            f"{total_content} owned items | {errors} errors"
        ))
        print_status("AUDIT", f"Impact summary: {summary_path}")
        print_status("AUDIT", f"Per-user reports: {user_reports_dir}/")

        classification_path = self._generate_user_classification(impact_results, user_reports_dir, audit_dir)
        print_status("AUDIT", f"User classification: {classification_path}")

    def _get_latest_activity(self, old_username: str) -> str | None:
        user_record = None
        if self._cache.has_dimension("users"):
            for r in self._cache.get_all_records("users"):
                if r.name and r.name.lower() == old_username.lower():
                    user_record = r
                    break

        candidates = []

        if user_record:
            last_login = user_record.attrs.get("lastLogin")
            if last_login:
                candidates.append(last_login)

        if user_record:
            user_id = user_record.id
            for ep in ("workbooks", "flows", "datasources"):
                if not self._cache.has_dimension(ep):
                    continue
                from src.utils.cache import owner_filter
                for item_id in self._cache.get_ids(ep, filter_fn=owner_filter(user_id)):
                    rec = self._cache.get_record(ep, item_id)
                    if rec:
                        updated = rec.attrs.get("updatedAt")
                        if updated:
                            candidates.append(updated)

        if not candidates:
            return None

        return max(candidates)

    @staticmethod
    def _classify_user(impact: Dict, owned_projects: int, owned_datasources: int, default_perm_count: int) -> tuple[str, str]:
        perms = impact.get("permission_count", 0)
        groups = impact.get("group_count", 0)
        content = impact.get("content_count", 0)
        favs = impact.get("favorite_count", 0)
        subs = impact.get("subscription_count", 0)
        cvs = impact.get("custom_view_count", 0)
        collections = impact.get("collection_count", 0)
        alerts = impact.get("alert_count", 0)
        webhooks = impact.get("webhook_count", 0)

        if owned_projects > 0 or default_perm_count > 0:
            return "very_high", (
                "Governance role: owns projects or manages default permissions. "
                "Missteps can expose data or break broad access patterns."
            )

        publishable_content = content - owned_projects
        if publishable_content > 10 or owned_datasources > 0:
            return "high", (
                "Content producer: owns >10 items or published data sources. "
                "Their decisions affect many consumers."
            )

        has_ux_artifacts = favs >= 5 or cvs >= 1 or subs >= 1
        if (1 <= publishable_content <= 10) or (perms > 0 and has_ux_artifacts):
            return "moderate", (
                "Explorer/occasional publisher with meaningful personalization. "
                "Owns some content or has explicit permissions with active UX artifacts."
            )

        return "low", (
            "Content consumer with minimal personalization. "
            "Inherits access via groups; little to no governance footprint."
        )

    def _generate_user_classification(self, impact_results: List[Dict], user_reports_dir: Path, audit_dir: Path) -> Path:
        import json

        classifications = []

        for impact in impact_results:
            if impact.get("error") or not impact.get("old_user_exists"):
                classifications.append({
                    "old_username": impact.get("old_username"),
                    "new_username": impact.get("new_username"),
                    "complexity": "unknown",
                    "reason": impact.get("error", "User not found"),
                    "latest_activity": None,
                    "role": None,
                })
                continue

            report_file = impact.get("report_file")
            owned_projects = 0
            owned_datasources = 0
            default_perm_count = 0

            if report_file:
                report_path = user_reports_dir / report_file
                try:
                    with open(report_path, "r", encoding="utf-8") as f:
                        report = json.load(f)
                    for item in report.get("owned_content", []):
                        if item.get("content_type") == "projects":
                            owned_projects += 1
                        elif item.get("content_type") == "datasources":
                            owned_datasources += 1
                    default_perm_count = report.get("summary", {}).get("default_permission_count", 0)
                except Exception:
                    pass

            complexity, reason = self._classify_user(impact, owned_projects, owned_datasources, default_perm_count)
            latest_activity = self._get_latest_activity(impact["old_username"])

            classifications.append({
                "old_username": impact["old_username"],
                "new_username": impact["new_username"],
                "complexity": complexity,
                "reason": reason,
                "role": impact.get("old_user_role"),
                "latest_activity": latest_activity,
                "permission_count": impact.get("permission_count", 0),
                "group_count": impact.get("group_count", 0),
                "content_count": impact.get("content_count", 0),
                "owned_projects": owned_projects,
                "owned_datasources": owned_datasources,
                "default_permission_count": default_perm_count,
                "favorite_count": impact.get("favorite_count", 0),
                "subscription_count": impact.get("subscription_count", 0),
                "custom_view_count": impact.get("custom_view_count", 0),
                "collection_count": impact.get("collection_count", 0),
                "alert_count": impact.get("alert_count", 0),
                "webhook_count": impact.get("webhook_count", 0),
            })

        def _sort_key(entry):
            tier_order = {"very_high": 0, "high": 1, "moderate": 2, "low": 3, "unknown": 4}
            tier = tier_order.get(entry["complexity"], 4)
            activity = entry.get("latest_activity") or ""
            return (tier, activity == "", activity)

        classifications.sort(key=_sort_key, reverse=False)
        for i, c in enumerate(classifications):
            if c.get("latest_activity"):
                within_tier = [x for x in classifications if x["complexity"] == c["complexity"]]
                within_tier_with_activity = [x for x in within_tier if x.get("latest_activity")]
                within_tier_with_activity.sort(key=lambda x: x["latest_activity"], reverse=True)

        tier_sorted = []
        for tier in ("very_high", "high", "moderate", "low", "unknown"):
            tier_entries = [c for c in classifications if c["complexity"] == tier]
            with_activity = sorted(
                [c for c in tier_entries if c.get("latest_activity")],
                key=lambda x: x["latest_activity"],
                reverse=True,
            )
            without_activity = [c for c in tier_entries if not c.get("latest_activity")]
            tier_sorted.extend(with_activity)
            tier_sorted.extend(without_activity)

        tier_counts = {}
        for c in tier_sorted:
            tier_counts[c["complexity"]] = tier_counts.get(c["complexity"], 0) + 1

        output = {
            "summary": {
                "total_users": len(tier_sorted),
                "by_complexity": tier_counts,
            },
            "classifications": tier_sorted,
        }

        output_path = audit_dir / "user_classification.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2)

        csv_dir = audit_dir / "classification_csvs"
        csv_dir.mkdir(parents=True, exist_ok=True)
        for tier in ("very_high", "high", "moderate", "low", "unknown"):
            tier_entries = [c for c in tier_sorted if c["complexity"] == tier]
            if not tier_entries:
                continue
            csv_path = csv_dir / f"{tier}.csv"
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("old_username,new_username\n")
                for entry in tier_entries:
                    f.write(f"{entry['old_username']},{entry['new_username']}\n")
            print_status("CLASS", f"{tier}: {len(tier_entries)} users → {csv_path}")

        return output_path

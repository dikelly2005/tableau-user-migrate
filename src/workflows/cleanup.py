# Tableau cleanup workflow with pre/post verification and Personal Space relocation
# Co-authored with CoCo
import asyncio
import json
from pathlib import Path
from typing import List, Dict, Optional

from config.settings import Settings
from src.utils.cache import DimensionCache, owner_filter, user_filter
from src.utils.checkpoint import CheckpointManager
from reporting.audit import AuditLogger, AuditAction, AuditResult
from models.mapping import BatchResult, MappingState
from src.utils.logging_config import get_logger, print_status
from src.workflows.report_mixin import UserReportMixin
from src.workflows.cleanup_mixin import CleanupMixin

logger = get_logger(__name__)


class CleanupWorkflow(UserReportMixin, CleanupMixin):
    def __init__(
        self,
        user_service,
        permission_service,
        group_service,
        favorite_service,
        subscription_service,
        alert_service,
        custom_view_service,
        ownership_service,
        pulse_service,
        webhook_service,
        cache: DimensionCache,
        checkpoint: CheckpointManager,
        audit: AuditLogger,
        settings: Settings,
        authenticator=None,
        http_client=None,
    ):
        self._users = user_service
        self._permissions = permission_service
        self._groups = group_service
        self._favorites = favorite_service
        self._subscriptions = subscription_service
        self._alerts = alert_service
        self._custom_views = custom_view_service
        self._ownership = ownership_service
        self._pulse = pulse_service
        self._webhooks = webhook_service
        self._cache = cache
        self._checkpoint = checkpoint
        self._audit = audit
        self._settings = settings
        self._authenticator = authenticator
        self._http_client = http_client

    async def _ensure_token(self, phase_name: str) -> None:
        if self._authenticator and self._http_client:
            await self._authenticator.ensure_token_for_phase(self._http_client, phase_name)

    async def execute(self, mappings: List[Dict], audit_dir: Path) -> BatchResult:
        print_status("START", f"Cleanup workflow: {len(mappings)} users")
        self._audit.log(AuditAction.CLEANUP_START, result=AuditResult.SUCCESS)

        # Pre-cleanup: Generate baseline reports
        await self._ensure_token("baseline_reports")
        await self._generate_all_user_reports(mappings, audit_dir, "clean_only")

        result = BatchResult(total=len(mappings))
        pending = self._checkpoint.get_pending()
        pending_usernames = {cp.old_username for cp in pending}

        # Relocate Personal Space content before cleanup
        await self._ensure_token("relocate_personal_space")
        target_project_id = await self._ownership.ensure_migration_project(
            self._settings.migration_artifacts_project
        )

        user_map = {}
        for m in mappings:
            old_username = m["old_username"]
            new_username = m["new_username"]

            if pending_usernames and old_username not in pending_usernames:
                result.add_success()
                continue

            old_user = await self._users.lookup_user(old_username)
            if not old_user:
                self._checkpoint.mark_failed(old_username, f"User not found: {old_username}")
                state = MappingState(old_username=old_username, new_username=new_username)
                state.add_error(f"User not found: {old_username}")
                result.add_failure(state)
                continue

            old_user_id = old_user["id"]
            user_map[old_username] = {
                "old_user_id": old_user_id,
                "new_username": new_username,
            }

            # Relocate Personal Space content
            if not self._checkpoint.is_step_completed(old_username, "relocate_personal_space"):
                try:
                    await self._ownership.relocate_personal_space_content(
                        old_user_id, old_username, target_project_id
                    )
                    self._checkpoint.mark_step_completed(old_username, "relocate_personal_space")
                except Exception as e:
                    logger.warning(f"Personal Space relocation failed for {old_username}: {e}")

        # Cleanup phase
        await self._ensure_token("cleanup")
        for old_username, info in user_map.items():
            if self._checkpoint.is_failed(old_username):
                continue

            self._checkpoint.mark_in_progress(old_username)
            old_user_id = info["old_user_id"]

            try:
                await self._cleanup_user(old_user_id, old_username)
                self._checkpoint.mark_completed(old_username)

            except Exception as e:
                self._checkpoint.mark_failed(old_username, str(e))
                state = MappingState(old_username=old_username, new_username=info["new_username"])
                state.add_error(str(e))
                result.add_failure(state)
                logger.error(f"Cleanup failed for {old_username}: {e}")
                continue

        # Post-cleanup verification: refresh cache and verify all dimensions zeroed
        await self._ensure_token("verification")
        print_status("VERIFY", "Refreshing cache for post-cleanup verification")
        self._cache.refresh()
        client = self._ownership._client
        endpoints_config = {"endpoints": self._ownership._endpoints}
        await self._cache.warmup(client, endpoints_config, client.site_id)

        verification_results = {}
        for old_username, info in user_map.items():
            if self._checkpoint.is_failed(old_username):
                verification_results[old_username] = {"status": "fail", "reason": "cleanup_error"}
                continue

            old_user_id = info["old_user_id"]
            issues = []
            counts = {}

            # Verify user is deactivated
            user_record = self._cache.get_record("users", old_user_id)
            if user_record:
                counts["site_role"] = user_record.type
                if user_record.type != "Unlicensed":
                    issues.append(f"user_not_deactivated:role={user_record.type}")

            # Verify zero owned content
            owned_types = ("workbooks", "datasources", "flows", "projects", "virtual_connections")
            remaining_owned = []
            for ct in owned_types:
                ids = self._cache.get_ids(ct, filter_fn=owner_filter(old_user_id))
                remaining_owned.extend(ids)
            counts["remaining_owned"] = len(remaining_owned)
            if remaining_owned:
                issues.append(f"still_owns_{len(remaining_owned)}_items")

            # Verify zero collections
            remaining_collections = self._cache.get_ids("collections", filter_fn=owner_filter(old_user_id))
            counts["remaining_collections"] = len(remaining_collections)
            if remaining_collections:
                issues.append(f"still_owns_{len(remaining_collections)}_collections")

            # Verify zero custom views
            remaining_cvs = self._cache.get_ids("custom_views", filter_fn=owner_filter(old_user_id))
            counts["remaining_custom_views"] = len(remaining_cvs)
            if remaining_cvs:
                issues.append(f"still_owns_{len(remaining_cvs)}_custom_views")

            # Verify zero groups
            remaining_groups = self._cache.get_parents_for_child("group_users", old_user_id) if self._cache.has_dimension("group_users") else []
            counts["remaining_groups"] = len(remaining_groups)
            if remaining_groups:
                issues.append(f"still_in_{len(remaining_groups)}_groups")

            # Verify zero permissions
            remaining_perms = self._cache.get_user_permissions(old_user_id)
            counts["remaining_permissions"] = len(remaining_perms)
            if remaining_perms:
                issues.append(f"still_has_{len(remaining_perms)}_permissions")

            # Verify zero subscriptions
            remaining_subs = self._cache.get_ids("subscriptions", filter_fn=user_filter(old_user_id))
            counts["remaining_subscriptions"] = len(remaining_subs)
            if remaining_subs:
                issues.append(f"still_has_{len(remaining_subs)}_subscriptions")

            # Verify zero alerts owned
            remaining_alerts = self._cache.get_ids("data_alerts", filter_fn=owner_filter(old_user_id))
            counts["remaining_alerts"] = len(remaining_alerts)
            if remaining_alerts:
                issues.append(f"still_owns_{len(remaining_alerts)}_alerts")

            # Verify zero pulse subscriptions
            if self._cache.has_dimension("pulse_subscriptions"):
                def _pulse_filter(uid):
                    def _f(r):
                        return r.attrs.get("user_id") == uid or r.attrs.get("follower_id") == uid
                    return _f
                remaining_pulse = self._cache.get_ids("pulse_subscriptions", filter_fn=_pulse_filter(old_user_id))
                counts["remaining_pulse_subscriptions"] = len(remaining_pulse)
                if remaining_pulse:
                    issues.append(f"still_has_{len(remaining_pulse)}_pulse_subscriptions")

            # Verify zero pulse alerts
            if self._cache.has_dimension("pulse_alerts"):
                def _pulse_alert_filter(uid):
                    def _f(r):
                        return r.attrs.get("owner", {}).get("id") == uid if isinstance(r.attrs.get("owner"), dict) else False
                    return _f
                remaining_pulse_alerts = self._cache.get_ids("pulse_alerts", filter_fn=_pulse_alert_filter(old_user_id))
                counts["remaining_pulse_alerts"] = len(remaining_pulse_alerts)
                if remaining_pulse_alerts:
                    issues.append(f"still_has_{len(remaining_pulse_alerts)}_pulse_alerts")

            # Verify zero webhooks
            if self._cache.has_dimension("webhooks"):
                remaining_webhooks = self._cache.get_ids("webhooks", filter_fn=owner_filter(old_user_id))
                counts["remaining_webhooks"] = len(remaining_webhooks)
                if remaining_webhooks:
                    issues.append(f"still_has_{len(remaining_webhooks)}_webhooks")

            if issues:
                verification_results[old_username] = {"status": "fail", "issues": issues, **counts}
                logger.warning(f"Post-cleanup verification FAIL for {old_username}: {', '.join(issues)}")
            else:
                verification_results[old_username] = {"status": "pass", **counts}
                result.add_success()
                print_status("VERIFY", f"PASS: {old_username} — fully cleaned")

        # Write verification results
        verify_path = audit_dir / "verification_results.json"
        verify_path.write_text(json.dumps(verification_results, indent=2))

        pass_count = sum(1 for v in verification_results.values() if v.get("status") == "pass")
        fail_count = sum(1 for v in verification_results.values() if v.get("status") == "fail")
        print_status("VERIFY", f"Post-cleanup: {pass_count} pass, {fail_count} fail")

        self._audit.log(AuditAction.CLEANUP_COMPLETE, result=AuditResult.SUCCESS)
        print_status("DONE", result.summary())
        return result

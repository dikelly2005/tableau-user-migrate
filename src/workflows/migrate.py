# Tableau user migration workflow with verification-gated cleanup
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

_USER_CREATION_DELAY_SECONDS = 4
_OWNERSHIP_TRANSFER_DELAY_SECONDS = 2
_CUSTOM_VIEW_PROPAGATION_DELAY_SECONDS = 1.5
_ALERT_RECIPIENT_DELAY_SECONDS = 2
_COLLECTION_CREATION_DELAY_SECONDS = 1.5


class MigrateWorkflow(UserReportMixin, CleanupMixin):
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
        authenticator=None,
        http_client=None,
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
        self._authenticator = authenticator
        self._http_client = http_client

    async def _ensure_token(self, phase_name: str) -> None:
        if self._authenticator and self._http_client:
            await self._authenticator.ensure_token_for_phase(self._http_client, phase_name)

    async def execute(self, mappings: List[Dict], audit_dir: Path) -> BatchResult:
        print_status("START", f"Migrate workflow: {len(mappings)} users")
        self._audit.log(AuditAction.MIGRATE_START, result=AuditResult.SUCCESS)

        # Phase 4: Generate baseline reports
        await self._ensure_token("baseline_reports")
        await self._generate_all_user_reports(mappings, audit_dir, "migrate")

        result = BatchResult(total=len(mappings))
        pending = self._checkpoint.get_pending()
        pending_usernames = {cp.old_username for cp in pending}

        # Phase 5: User Creation
        await self._ensure_token("user_creation")
        user_map = {}
        for m in mappings:
            old_username = m["old_username"]
            new_username = m["new_username"]

            if pending_usernames and old_username not in pending_usernames:
                continue

            old_user = await self._users.lookup_user(old_username)
            if not old_user:
                continue

            old_user_id = old_user["id"]

            try:
                if not self._checkpoint.is_step_completed(old_username, "create_user"):
                    site_role = old_user["site_role"]
                    auth_setting = old_user.get("auth_setting")
                    new_user = await self._users.create_user(new_username, site_role, auth_setting=auth_setting)
                    self._checkpoint.mark_step_completed(old_username, "create_user")
                    await asyncio.sleep(_USER_CREATION_DELAY_SECONDS)
                else:
                    new_user = await self._users.lookup_user(new_username)
                    if not new_user:
                        site_role = old_user["site_role"]
                        auth_setting = old_user.get("auth_setting")
                        new_user = await self._users.create_user(new_username, site_role, auth_setting=auth_setting)
                        await asyncio.sleep(_USER_CREATION_DELAY_SECONDS)
            except Exception as e:
                self._checkpoint.mark_failed(old_username, str(e))
                state = MappingState(old_username=old_username, new_username=new_username)
                state.add_error(str(e))
                result.add_failure(state)
                logger.error(f"User creation failed for {old_username} → {new_username}: {e}")
                continue

            user_map[old_username] = {
                "old_user_id": old_user_id,
                "new_user_id": new_user["id"],
                "new_username": new_username,
                "site_role": old_user["site_role"],
            }

        # Phase 6: Access Cloning
        await self._ensure_token("access_cloning")
        for old_username, info in user_map.items():
            old_user_id = info["old_user_id"]
            new_user_id = info["new_user_id"]
            new_username = info["new_username"]
            self._checkpoint.mark_in_progress(old_username)

            try:
                if not self._checkpoint.is_step_completed(old_username, "clone_permissions"):
                    await self._permissions.clone_permissions(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_permissions")

                await self._permissions.check_cache_staleness()

                if not self._checkpoint.is_step_completed(old_username, "clone_groups"):
                    await self._groups.clone_groups(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_groups")
            except Exception as e:
                logger.error(f"Access cloning failed for {old_username}: {e}")
                self._checkpoint.mark_failed(old_username, str(e))

        # Phase 6.5: Relocate Personal Space content to shared project
        await self._ensure_token("relocate_personal_space")
        target_project_id = await self._ownership.ensure_migration_project(
            self._settings.migration_artifacts_project
        )
        for old_username, info in user_map.items():
            if self._checkpoint.is_failed(old_username):
                continue
            if self._checkpoint.is_step_completed(old_username, "relocate_personal_space"):
                continue
            old_user_id = info["old_user_id"]
            try:
                await self._ownership.relocate_personal_space_content(
                    old_user_id, old_username, target_project_id
                )
                self._checkpoint.mark_step_completed(old_username, "relocate_personal_space")
            except Exception as e:
                logger.error(f"Personal Space relocation failed for {old_username}: {e}")
                self._checkpoint.mark_failed(old_username, str(e))

        # Phase 7: Ownership Transfer
        await self._ensure_token("ownership_transfer")
        for old_username, info in user_map.items():
            if self._checkpoint.is_failed(old_username):
                continue
            await self._ensure_token(f"ownership_transfer:{old_username}")
            old_user_id = info["old_user_id"]
            new_user_id = info["new_user_id"]
            new_username = info["new_username"]

            try:
                if not self._checkpoint.is_step_completed(old_username, "transfer_ownership"):
                    await self._ownership.transfer_ownership(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "transfer_ownership")
                    await asyncio.sleep(_OWNERSHIP_TRANSFER_DELAY_SECONDS)

                if not self._checkpoint.is_step_completed(old_username, "transfer_collections"):
                    await self._collections.transfer_collection_ownership(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "transfer_collections")
                    await asyncio.sleep(_OWNERSHIP_TRANSFER_DELAY_SECONDS)

                if not self._checkpoint.is_step_completed(old_username, "clone_custom_views"):
                    await self._custom_views.clone_custom_views(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_custom_views")

                if not self._checkpoint.is_step_completed(old_username, "clone_custom_view_defaults"):
                    await self._custom_views.clone_custom_view_defaults(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_custom_view_defaults")

                await asyncio.sleep(_CUSTOM_VIEW_PROPAGATION_DELAY_SECONDS)
            except Exception as e:
                logger.error(f"Ownership transfer failed for {old_username}: {e}")
                self._checkpoint.mark_failed(old_username, str(e))

        # Phase 8: Artifact Cloning
        await self._ensure_token("artifact_cloning")
        for old_username, info in user_map.items():
            if self._checkpoint.is_failed(old_username):
                continue
            old_user_id = info["old_user_id"]
            new_user_id = info["new_user_id"]
            new_username = info["new_username"]

            try:
                if not self._checkpoint.is_step_completed(old_username, "clone_subscriptions"):
                    await self._subscriptions.clone_subscriptions(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_subscriptions")

                if not self._checkpoint.is_step_completed(old_username, "clone_alerts"):
                    await self._alerts.clone_alerts(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_alerts")

                if not self._checkpoint.is_step_completed(old_username, "clone_favorites"):
                    await self._favorites.clone_favorites(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_favorites")

                if not self._checkpoint.is_step_completed(old_username, "clone_pulse_subscriptions"):
                    await self._pulse.clone_pulse_subscriptions(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_pulse_subscriptions")

                if not self._checkpoint.is_step_completed(old_username, "clone_pulse_alerts"):
                    await self._pulse.clone_pulse_alerts(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_pulse_alerts")

                if not self._checkpoint.is_step_completed(old_username, "clone_webhooks"):
                    await self._webhooks.clone_webhooks(old_user_id, old_username, new_user_id, new_username)
                    self._checkpoint.mark_step_completed(old_username, "clone_webhooks")
            except Exception as e:
                logger.error(f"Artifact cloning failed for {old_username}: {e}")
                self._checkpoint.mark_failed(old_username, str(e))

        # Phase 9: Verification
        await self._ensure_token("verification")
        print_status("VERIFY", "Refreshing cache from API for post-migration verification")

        # Step 23: Refresh cache to capture post-mutation state from API
        self._cache.refresh()
        # Re-warmup requires the client — get it from ownership service
        client = self._ownership._client
        endpoints_config = {"endpoints": self._ownership._endpoints}
        await self._cache.warmup(client, endpoints_config, client.site_id)

        # Step 24-26: Compare baseline vs post-migration per user
        print_status("VERIFY", "Verifying migration outcomes before cleanup")
        verification_results = {}
        verification_details = {}

        for old_username, info in user_map.items():
            if self._checkpoint.is_failed(old_username):
                verification_results[old_username] = "fail"
                verification_details[old_username] = {"reason": "failed_in_earlier_phase"}
                continue

            old_user_id = info["old_user_id"]
            new_user_id = info["new_user_id"]
            new_username = info["new_username"]
            expected_role = info["site_role"]
            issues = []
            counts = {}

            # 1. Verify new user exists with correct site role and license
            new_user_record = self._cache.get_record("users", new_user_id)
            if not new_user_record:
                issues.append("new_user_not_found_in_cache")
            else:
                counts["new_user_site_role"] = new_user_record.type
                counts["new_user_license_type"] = new_user_record.license_type
                if new_user_record.type != expected_role:
                    issues.append(f"site_role_mismatch:expected={expected_role},actual={new_user_record.type}")

            # 2. Verify old user has no remaining owned content
            owned_types = ("workbooks", "datasources", "flows", "projects", "virtual_connections")
            old_remaining_owned = []
            for ct in owned_types:
                ids = self._cache.get_ids(ct, filter_fn=owner_filter(old_user_id))
                old_remaining_owned.extend(ids)
            counts["old_user_remaining_owned"] = len(old_remaining_owned)
            if old_remaining_owned:
                issues.append(f"old_user_still_owns_{len(old_remaining_owned)}_items")

            # 3. Verify new user owns transferred content
            new_owned = []
            for ct in owned_types:
                ids = self._cache.get_ids(ct, filter_fn=owner_filter(new_user_id))
                new_owned.extend(ids)
            counts["new_user_owned_content"] = len(new_owned)

            # 4. Verify old user has no remaining collections
            old_collections = self._cache.get_ids("collections", filter_fn=owner_filter(old_user_id))
            new_collections = self._cache.get_ids("collections", filter_fn=owner_filter(new_user_id))
            counts["old_user_remaining_collections"] = len(old_collections)
            counts["new_user_collections"] = len(new_collections)
            if old_collections:
                issues.append(f"old_user_still_owns_{len(old_collections)}_collections")

            # 5. Verify old user has no remaining custom views
            old_cvs = self._cache.get_ids("custom_views", filter_fn=owner_filter(old_user_id))
            new_cvs = self._cache.get_ids("custom_views", filter_fn=owner_filter(new_user_id))
            counts["old_user_remaining_custom_views"] = len(old_cvs)
            counts["new_user_custom_views"] = len(new_cvs)
            if old_cvs:
                issues.append(f"old_user_still_owns_{len(old_cvs)}_custom_views")

            # 6. Verify group memberships transferred
            new_groups = self._cache.get_parents_for_child("group_users", new_user_id) if self._cache.has_dimension("group_users") else []
            old_groups = self._cache.get_parents_for_child("group_users", old_user_id) if self._cache.has_dimension("group_users") else []
            counts["old_user_groups"] = len(old_groups)
            counts["new_user_groups"] = len(new_groups)
            if old_groups and not new_groups:
                issues.append("new_user_has_no_groups")
            elif old_groups and len(new_groups) < len(old_groups):
                issues.append(f"group_count_mismatch:expected={len(old_groups)},actual={len(new_groups)}")

            # 7. Verify permissions transferred
            new_perms = self._cache.get_user_permissions(new_user_id)
            old_perms = self._cache.get_user_permissions(old_user_id)
            counts["old_user_permissions"] = len(old_perms)
            counts["new_user_permissions"] = len(new_perms)
            if old_perms and not new_perms:
                issues.append("new_user_has_no_permissions")
            elif old_perms and len(new_perms) < len(old_perms) * 0.5:
                issues.append(f"permissions_significantly_fewer:expected~{len(old_perms)},actual={len(new_perms)}")

            # 8. Verify favorites transferred
            new_favorites = self._cache.get_child_records("user_favorites", new_user_id) if self._cache.has_dimension("user_favorites") else []
            old_favorites = self._cache.get_child_records("user_favorites", old_user_id) if self._cache.has_dimension("user_favorites") else []
            counts["old_user_favorites"] = len(old_favorites)
            counts["new_user_favorites"] = len(new_favorites)
            if old_favorites and not new_favorites:
                issues.append("new_user_has_no_favorites")

            # 9. Verify subscriptions transferred
            new_subs = self._cache.get_ids("subscriptions", filter_fn=user_filter(new_user_id))
            old_subs = self._cache.get_ids("subscriptions", filter_fn=user_filter(old_user_id))
            counts["old_user_subscriptions"] = len(old_subs)
            counts["new_user_subscriptions"] = len(new_subs)
            if old_subs and not new_subs:
                issues.append("new_user_has_no_subscriptions")

            # 10. Verify alerts transferred (new user should be owner or recipient)
            new_alerts = self._cache.get_ids("data_alerts", filter_fn=owner_filter(new_user_id))
            old_alerts = self._cache.get_ids("data_alerts", filter_fn=owner_filter(old_user_id))
            counts["old_user_alerts_owned"] = len(old_alerts)
            counts["new_user_alerts_owned"] = len(new_alerts)
            if old_alerts and not new_alerts:
                issues.append("new_user_owns_no_alerts")

            # 11. Verify pulse subscriptions
            if self._cache.has_dimension("pulse_subscriptions"):
                def _pulse_user_filter(uid):
                    def _f(r):
                        return r.attrs.get("user_id") == uid or r.attrs.get("follower_id") == uid
                    return _f
                new_pulse_subs = self._cache.get_ids("pulse_subscriptions", filter_fn=_pulse_user_filter(new_user_id))
                old_pulse_subs = self._cache.get_ids("pulse_subscriptions", filter_fn=_pulse_user_filter(old_user_id))
                counts["old_user_pulse_subscriptions"] = len(old_pulse_subs)
                counts["new_user_pulse_subscriptions"] = len(new_pulse_subs)
                if old_pulse_subs and not new_pulse_subs:
                    issues.append("new_user_has_no_pulse_subscriptions")

            # 12. Verify pulse alerts
            if self._cache.has_dimension("pulse_alerts"):
                def _pulse_alert_filter(uid):
                    def _f(r):
                        return r.attrs.get("owner", {}).get("id") == uid if isinstance(r.attrs.get("owner"), dict) else False
                    return _f
                new_pulse_alerts = self._cache.get_ids("pulse_alerts", filter_fn=_pulse_alert_filter(new_user_id))
                old_pulse_alerts = self._cache.get_ids("pulse_alerts", filter_fn=_pulse_alert_filter(old_user_id))
                counts["old_user_pulse_alerts"] = len(old_pulse_alerts)
                counts["new_user_pulse_alerts"] = len(new_pulse_alerts)
                if old_pulse_alerts and not new_pulse_alerts:
                    issues.append("new_user_has_no_pulse_alerts")

            # 13. Verify webhooks
            if self._cache.has_dimension("webhooks"):
                new_webhooks = self._cache.get_ids("webhooks", filter_fn=owner_filter(new_user_id))
                old_webhooks = self._cache.get_ids("webhooks", filter_fn=owner_filter(old_user_id))
                counts["old_user_webhooks"] = len(old_webhooks)
                counts["new_user_webhooks"] = len(new_webhooks)
                if old_webhooks and not new_webhooks:
                    issues.append("new_user_has_no_webhooks")

            # Determine pass/fail
            if issues:
                verification_results[old_username] = "fail"
                verification_details[old_username] = {"issues": issues, **counts}
                logger.warning(f"Verification FAIL for {old_username}: {', '.join(issues)}")
                self._audit.log_failure(
                    AuditAction.MIGRATE_COMPLETE,
                    error_message=f"Verification failed: {', '.join(issues)}",
                    old_username=old_username,
                    new_username=new_username,
                    details={"issues": issues, **counts},
                )
            else:
                verification_results[old_username] = "pass"
                verification_details[old_username] = counts
                print_status("VERIFY",
                    f"PASS: {old_username} → {new_username} "
                    f"(owned:{counts.get('new_user_owned_content', 0)} "
                    f"perms:{counts.get('new_user_permissions', 0)} "
                    f"groups:{counts.get('new_user_groups', 0)} "
                    f"favs:{counts.get('new_user_favorites', 0)} "
                    f"subs:{counts.get('new_user_subscriptions', 0)} "
                    f"alerts:{counts.get('new_user_alerts_owned', 0)})"
                )

        # Write verification summary
        verify_path = audit_dir / "verification_results.json"
        verify_data = {
            "results": verification_results,
            "details": verification_details,
        }
        verify_path.write_text(json.dumps(verify_data, indent=2))

        pass_count = sum(1 for v in verification_results.values() if v == "pass")
        fail_count = sum(1 for v in verification_results.values() if v == "fail")
        print_status("VERIFY", f"Results: {pass_count} pass, {fail_count} fail")

        # Phase 10: Cleanup (only "pass" users)
        await self._ensure_token("cleanup")
        for old_username, info in user_map.items():
            if verification_results.get(old_username) != "pass":
                state = MappingState(old_username=old_username, new_username=info["new_username"])
                state.add_error(f"Verification failed — cleanup skipped")
                result.add_failure(state)
                continue

            await self._ensure_token(f"cleanup:{old_username}")
            old_user_id = info["old_user_id"]
            new_username = info["new_username"]

            try:
                await self._cleanup_user(old_user_id, old_username, skip_steps={"remove_custom_views"})
                self._checkpoint.mark_completed(old_username)
                result.add_success()

            except Exception as e:
                self._checkpoint.mark_failed(old_username, str(e))
                state = MappingState(old_username=old_username, new_username=new_username)
                state.add_error(str(e))
                result.add_failure(state)
                logger.error(f"Cleanup failed for {old_username}: {e}")

        self._audit.log(AuditAction.MIGRATE_COMPLETE, result=AuditResult.SUCCESS)
        print_status("DONE", result.summary())
        return result

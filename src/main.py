import asyncio
import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

from config.settings import Settings
from src.api.auth import TableauAuthenticator
from src.api.base import BaseTableauClient
from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache
from src.utils.checkpoint import CheckpointManager
from src.utils.csv_loader import load_user_mappings
from src.utils.confirmations import ConfirmationManager
from src.utils.logging_config import setup_logging, print_status
from reporting.logger import MigrateLogger, setup_logger
from reporting.audit import AuditLogger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tableau Cloud User Migrate Tool v2")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "clone", "migrate", "clean-only"),
        default="dry-run",
        help="Execution mode (default: dry-run)",
    )
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation")
    parser.add_argument("--skip-validation", action="store_true", help="Skip CSV/config validation")
    parser.add_argument("--resume", type=str, default=None, help="Resume from checkpoint file path")
    parser.add_argument("--resume-latest", action="store_true", help="Resume latest incomplete checkpoint")
    parser.add_argument("--compare", type=str, default=None, help="Compare current dry-run against a previous run ID (e.g., 20260420_091546)")
    parser.add_argument("--compare-latest", action="store_true", help="Compare current dry-run against the most recent previous dry-run")
    parser.add_argument("--force-refresh", action="store_true", help="Deprecated — cache is always refreshed")
    return parser.parse_args()


def load_endpoints_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config" / "endpoints.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _find_previous_dry_run(log_location: Path, current_run_id: str) -> str | None:
    if not log_location or not log_location.exists():
        return None
    candidates = []
    for d in log_location.iterdir():
        if not d.is_dir() or not d.name.startswith("migrate_run_"):
            continue
        rid = d.name.replace("migrate_run_", "")
        if rid == current_run_id:
            continue
        summary = d / "impact_summary.json"
        if summary.exists():
            import json
            try:
                with open(summary) as f:
                    data = json.load(f)
                if data.get("mode") == "dry-run":
                    candidates.append(rid)
            except Exception:
                pass
    if not candidates:
        print_status("WARN", "No previous dry-run found for comparison")
        return None
    candidates.sort(reverse=True)
    print_status("COMPARE", f"Comparing against previous dry-run: {candidates[0]}")
    return candidates[0]


async def main():
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    settings = Settings.from_environment()
    settings.mode = args.mode

    if not args.skip_validation:
        settings.validate()

    setup_logging(settings)
    migrate_logger = setup_logger("tableau_user_migrate", settings.paths.log_location, run_id)
    audit_dir = settings.get_audit_dir(run_id)
    audit_logger = AuditLogger(audit_dir / "audit_log.jsonl", run_id)

    print_status("START", f"Tableau Cloud User Migrate Tool v2 — mode: {args.mode} — run: {run_id}")

    checkpoint = CheckpointManager()
    resuming = False

    if args.resume:
        checkpoint.load(Path(args.resume))
        resuming = True
    elif args.resume_latest and settings.paths.checkpoint_dir:
        latest = CheckpointManager.find_latest(settings.paths.checkpoint_dir)
        if latest:
            checkpoint.load(latest)
            resuming = True
            print_status("CHECKPOINT", f"Resuming from: {latest}")
        else:
            print_status("WARN", "No incomplete checkpoints found")

    if resuming:
        pending = checkpoint.get_pending()
        mappings = [{"old_username": cp.old_username, "new_username": cp.new_username} for cp in checkpoint.get_all()]
        print_status("CHECKPOINT", f"Resuming {len(pending)} pending users from {checkpoint.total} total")
    else:
        mappings = load_user_mappings(settings.paths.csv_location)
        print_status("GET", f"Loaded {len(mappings)} user mappings from {settings.paths.csv_location}")

    if not args.yes and not resuming:
        confirm = ConfirmationManager(migrate_logger)
        confirmed = False
        if args.mode == "dry-run":
            confirmed = True
        elif args.mode == "clone":
            confirmed = confirm.confirm_clone(len(mappings))
        elif args.mode == "migrate":
            confirmed = confirm.confirm_migrate(len(mappings))
        elif args.mode == "clean-only":
            confirmed = confirm.confirm_cleanup(len(mappings))

        if not confirmed:
            print_status("DONE", "Operation cancelled by user")
            return

    auth = TableauAuthenticator(settings.auth, settings.api)
    base_client = BaseTableauClient(auth, settings)

    try:
        await auth.authenticate(base_client.http_client)
        client = TableauAPIClient(base_client)

        await client.negotiate_api_version()

        endpoints_config = load_endpoints_config()

        cache = DimensionCache()
        cache_file = settings.cache.cache_dir / "dimension_cache.json" if settings.cache.cache_dir else None

        print_status("CACHE", "Building fresh cache from API")
        await cache.warmup(client, endpoints_config, auth.site_id)
        if cache_file:
            cache.save(cache_file)

        if not resuming:
            checkpoint_dir = settings.paths.checkpoint_dir or audit_dir
            checkpoint.initialize(mappings, args.mode, run_id, checkpoint_dir)

        from src.services.users import UserService
        from src.services.permissions import PermissionService
        from src.services.groups import GroupService
        from src.services.ownership import OwnershipService
        from src.services.favorites import FavoriteService
        from src.services.subscriptions import SubscriptionService
        from src.services.alerts import AlertService
        from src.services.custom_views import CustomViewService
        from src.services.collections import CollectionService
        from src.services.pulse import PulseService
        from src.services.webhooks import WebhookService

        user_svc = UserService(client, audit_logger, cache)
        perm_svc = PermissionService(client, audit_logger, cache, endpoints_config)
        group_svc = GroupService(client, audit_logger, cache)
        ownership_svc = OwnershipService(client, audit_logger, cache, endpoints_config)
        fav_svc = FavoriteService(client, audit_logger, cache)
        sub_svc = SubscriptionService(client, audit_logger, cache)
        alert_svc = AlertService(client, audit_logger, cache)
        cv_svc = CustomViewService(client, audit_logger, cache)
        collection_svc = CollectionService(client, audit_logger, cache)
        pulse_svc = PulseService(client, audit_logger, cache)
        webhook_svc = WebhookService(client, audit_logger, cache)

        if args.mode == "dry-run":
            from src.workflows.dry_run import DryRunWorkflow
            workflow = DryRunWorkflow(
                user_svc, perm_svc, group_svc, ownership_svc,
                fav_svc, sub_svc, alert_svc, cv_svc, collection_svc,
                pulse_svc, webhook_svc, cache, checkpoint, audit_logger, settings,
            )
            await workflow.execute(mappings, audit_dir)

            compare_run_id = args.compare
            if args.compare_latest:
                compare_run_id = _find_previous_dry_run(settings.paths.log_location, run_id)
            if compare_run_id:
                from src.workflows.comparison import generate_comparison_report
                baseline_dir = settings.paths.log_location / f"migrate_run_{compare_run_id}"
                generate_comparison_report(baseline_dir, audit_dir)

        elif args.mode == "clone":
            from src.workflows.clone import CloneWorkflow
            workflow = CloneWorkflow(
                user_svc, perm_svc, group_svc,
                fav_svc, sub_svc, alert_svc, cv_svc, collection_svc,
                pulse_svc, webhook_svc, cache, checkpoint, audit_logger, settings,
            )
            result = await workflow.execute(mappings, audit_dir)
            if result.has_failures:
                print_status("WARN", f"{result.failed} users failed — check audit log")

        elif args.mode == "migrate":
            from src.workflows.migrate import MigrateWorkflow
            workflow = MigrateWorkflow(
                user_svc, perm_svc, group_svc, ownership_svc,
                fav_svc, sub_svc, alert_svc, cv_svc, collection_svc,
                pulse_svc, webhook_svc, cache, checkpoint, audit_logger, settings,
            )
            result = await workflow.execute(mappings, audit_dir)
            if result.has_failures:
                print_status("WARN", f"{result.failed} users failed — check audit log")

        elif args.mode == "clean-only":
            from src.workflows.cleanup import CleanupWorkflow
            workflow = CleanupWorkflow(
                user_svc, perm_svc, group_svc,
                fav_svc, sub_svc, alert_svc, cv_svc, pulse_svc, webhook_svc,
                checkpoint, audit_logger, settings,
            )
            result = await workflow.execute(mappings)
            if result.has_failures:
                print_status("WARN", f"{result.failed} users failed — check audit log")

        print_status("AUDIT", f"Audit log: {audit_dir / 'audit_log.jsonl'}")
        print_status("CHECKPOINT", checkpoint.summary())
        print_status("DONE", f"Run {run_id} complete — API stats: {base_client.stats}")

    finally:
        await base_client.close()


if __name__ == "__main__":
    asyncio.run(main())

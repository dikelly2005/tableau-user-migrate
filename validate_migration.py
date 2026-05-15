#!/usr/bin/env python3
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from config.settings import Settings
from src.api.auth import TableauAuthenticator
from src.api.base import BaseTableauClient
from src.api.client import TableauAPIClient
from src.utils.cache import DimensionCache, owner_filter
from src.utils.csv_loader import load_user_mappings
from src.utils.logging_config import setup_logging, print_status


async def main():
    settings = Settings.from_environment()
    settings.validate()
    setup_logging(settings)

    csv_path = settings.paths.csv_location
    mappings = load_user_mappings(csv_path)

    print_status("START", f"Post-migration validation: {len(mappings)} users")

    auth = TableauAuthenticator(settings.auth, settings.api)
    base_client = BaseTableauClient(auth, settings)

    try:
        await auth.authenticate(base_client.http_client)
        client = TableauAPIClient(base_client)
        await client.negotiate_api_version()

        config_path = Path(__file__).resolve().parent / "config" / "endpoints.yaml"
        with open(config_path) as f:
            endpoints_config = yaml.safe_load(f)

        cache = DimensionCache()
        await cache.warmup(client, endpoints_config, auth.site_id)

        results = []
        issues_found = 0

        for m in mappings:
            old_username = m["old_username"]
            new_username = m["new_username"]

            result = {
                "old_username": old_username,
                "new_username": new_username,
                "checks": [],
                "issues": [],
            }

            old_user = None
            for r in cache.get_all_records("users"):
                if r.name and r.name.lower() == old_username.lower():
                    old_user = r
                    break

            new_user = None
            for r in cache.get_all_records("users"):
                if r.name and r.name.lower() == new_username.lower():
                    new_user = r
                    break

            if not new_user:
                result["issues"].append(f"New user not found: {new_username}")
                result["checks"].append({"check": "new_user_exists", "status": "FAIL"})
                issues_found += 1
                results.append(result)
                continue
            result["checks"].append({"check": "new_user_exists", "status": "PASS", "role": new_user.type})

            if old_user:
                if old_user.type and old_user.type != "Unlicensed":
                    result["issues"].append(f"Old user still licensed: {old_username} (role: {old_user.type})")
                    result["checks"].append({"check": "old_user_unlicensed", "status": "FAIL", "role": old_user.type})
                    issues_found += 1
                else:
                    result["checks"].append({"check": "old_user_unlicensed", "status": "PASS"})

                old_user_id = old_user.id
                owned_content_types = []
                for ep_name, ep_config in endpoints_config.get("endpoints", {}).items():
                    if not ep_config.get("ownership_transferable"):
                        continue
                    if cache.has_dimension(ep_name):
                        owned = cache.get_ids(ep_name, filter_fn=owner_filter(old_user_id))
                        if owned:
                            owned_content_types.append({"type": ep_name, "count": len(owned)})

                if owned_content_types:
                    total_owned = sum(c["count"] for c in owned_content_types)
                    result["issues"].append(
                        f"Old user still owns {total_owned} items: "
                        + ", ".join(f"{c['count']} {c['type']}(s)" for c in owned_content_types)
                    )
                    result["checks"].append({"check": "old_user_zero_ownership", "status": "FAIL", "owned": owned_content_types})
                    issues_found += 1
                else:
                    result["checks"].append({"check": "old_user_zero_ownership", "status": "PASS"})
            else:
                result["checks"].append({"check": "old_user_unlicensed", "status": "SKIP", "reason": "User not found on site"})
                result["checks"].append({"check": "old_user_zero_ownership", "status": "SKIP", "reason": "User not found on site"})

            new_user_id = new_user.id
            new_groups = cache.get_child_records("group_users", new_user_id) if cache.has_dimension("group_users") else []
            result["checks"].append({"check": "new_user_has_groups", "status": "PASS" if new_groups else "WARN", "count": len(new_groups)})

            results.append(result)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = settings.paths.log_location / f"validation_{timestamp}"
        output_dir.mkdir(parents=True, exist_ok=True)

        report_path = output_dir / "validation_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp,
                "total_users": len(mappings),
                "issues_found": issues_found,
                "results": results,
            }, f, indent=2)

        csv_path = output_dir / "validation_summary.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["old_username", "new_username", "new_user_exists", "old_user_unlicensed", "old_user_zero_ownership", "issues"])
            for r in results:
                checks = {c["check"]: c["status"] for c in r["checks"]}
                writer.writerow([
                    r["old_username"],
                    r["new_username"],
                    checks.get("new_user_exists", "N/A"),
                    checks.get("old_user_unlicensed", "N/A"),
                    checks.get("old_user_zero_ownership", "N/A"),
                    "; ".join(r["issues"]) if r["issues"] else "",
                ])

        print_status("DONE", f"Validation complete: {len(mappings)} users checked, {issues_found} issues found")
        print_status("AUDIT", f"Report: {report_path}")
        print_status("AUDIT", f"Summary CSV: {csv_path}")

        if issues_found > 0:
            print_status("WARN", f"{issues_found} issues require attention:")
            for r in results:
                for issue in r["issues"]:
                    print_status("WARN", f"  {r['old_username']}: {issue}")

    finally:
        await base_client.close()

    sys.exit(0 if issues_found == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

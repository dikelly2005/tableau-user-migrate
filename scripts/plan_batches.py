# Batch planning script: scores users by migration complexity and generates per-batch CSVs
# Co-authored with CoCo
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROLE_WEIGHTS = {
    "Creator": 10,
    "Explorer": 5,
    "ExplorerCanPublish": 7,
    "SiteAdministratorCreator": 12,
    "SiteAdministratorExplorer": 10,
    "Viewer": 1,
    "Unlicensed": 0,
}

TIER_FLOOR = {
    "very_high": 50,
    "high": 30,
}


def compute_score(entry: dict) -> float:
    role = entry.get("role") or "Viewer"
    role_weight = ROLE_WEIGHTS.get(role, 3)

    owns_projects = entry.get("owned_projects", 0)
    default_perms = entry.get("default_permission_count", 0)
    content_count = entry.get("content_count", 0)
    owned_datasources = entry.get("owned_datasources", 0)
    permission_count = entry.get("permission_count", 0)
    subscription_count = entry.get("subscription_count", 0)
    alert_count = entry.get("alert_count", 0)
    custom_view_count = entry.get("custom_view_count", 0)
    collection_count = entry.get("collection_count", 0)
    favorite_count = entry.get("favorite_count", 0)
    webhook_count = entry.get("webhook_count", 0)

    recency = _recency_weight(entry.get("latest_activity"))

    score = (
        role_weight
        + (owns_projects * 20)
        + (default_perms * 15)
        + (owned_datasources * 8)
        + (content_count * 3)
        + (permission_count * 1)
        + (subscription_count * 2)
        + (alert_count * 5)
        + (custom_view_count * 3)
        + (collection_count * 8)
        + (favorite_count * 0.5)
        + (webhook_count * 3)
        + recency
    )

    complexity = entry.get("complexity", "low")
    floor = TIER_FLOOR.get(complexity)
    if floor and score < floor:
        score = floor

    return round(score, 1)


def _recency_weight(latest_activity: str | None) -> float:
    if not latest_activity:
        return 0

    try:
        ts = datetime.fromisoformat(latest_activity.replace("Z", "+00:00"))
        days_ago = (datetime.now(timezone.utc) - ts).days
    except (ValueError, TypeError):
        return 0

    if days_ago > 365:
        return 0
    elif days_ago > 180:
        return 2
    elif days_ago > 90:
        return 5
    elif days_ago > 30:
        return 10
    else:
        return 15


_COUNTABLE_FIELDS = (
    "permission_count", "group_count", "content_count",
    "favorite_count", "subscription_count", "alert_count",
    "custom_view_count", "collection_count", "webhook_count",
)


def _is_empty(entry: dict) -> bool:
    return all(entry.get(f, 0) == 0 for f in _COUNTABLE_FIELDS)


def load_classification(dry_run_dir: Path) -> list[dict]:
    path = dry_run_dir / "user_classification.json"
    if not path.exists():
        print(f"ERROR: user_classification.json not found in {dry_run_dir}")
        print("Run a dry-run first: python -m src.main --mode dry-run")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("classifications", [])


def find_latest_dry_run(audit_dir: Path) -> Path | None:
    if not audit_dir.exists():
        return None
    runs = sorted(
        [d for d in audit_dir.iterdir() if d.is_dir() and d.name.startswith("migrate_run_")],
        key=lambda p: p.name,
        reverse=True,
    )
    for run_dir in runs:
        if (run_dir / "user_classification.json").exists():
            return run_dir
    return None


def assign_batches(scored_users: list[dict], batch_size: int) -> list[dict]:
    scored_users.sort(key=lambda u: u["score"])

    batch_num = 1
    count_in_batch = 0

    for user in scored_users:
        if count_in_batch >= batch_size:
            batch_num += 1
            count_in_batch = 0
        user["batch"] = batch_num
        count_in_batch += 1

    return scored_users


def write_batch_csvs(scored_users: list[dict], output_dir: Path) -> dict[int, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    batches: dict[int, list[dict]] = {}
    for user in scored_users:
        batches.setdefault(user["batch"], []).append(user)

    paths = {}
    for batch_num, users in sorted(batches.items()):
        csv_path = output_dir / f"batch_{batch_num:02d}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["old_username", "new_username"])
            for u in users:
                writer.writerow([u["old_username"], u["new_username"]])
        paths[batch_num] = csv_path

    return paths


def write_batch_plan(scored_users: list[dict], output_dir: Path) -> Path:
    plan_path = output_dir / "batch_plan.csv"
    with open(plan_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "batch", "old_username", "new_username", "score",
            "complexity", "role", "last_activity",
            "owned_content", "permissions", "alerts",
            "custom_views", "collections", "subscriptions",
        ])
        for u in scored_users:
            writer.writerow([
                u["batch"],
                u["old_username"],
                u["new_username"],
                u["score"],
                u.get("complexity", ""),
                u.get("role", ""),
                u.get("latest_activity", ""),
                u.get("content_count", 0),
                u.get("permission_count", 0),
                u.get("alert_count", 0),
                u.get("custom_view_count", 0),
                u.get("collection_count", 0),
                u.get("subscription_count", 0),
            ])
    return plan_path


def print_batch_summary(scored_users: list[dict], batch_paths: dict[int, Path]) -> None:
    batches: dict[int, list[dict]] = {}
    for u in scored_users:
        batches.setdefault(u["batch"], []).append(u)

    print(f"\n{'='*60}")
    print(f"  BATCH PLAN — {len(scored_users)} users across {len(batches)} batches")
    print(f"  Strategy: Low-risk first (ascending complexity score)")
    print(f"{'='*60}\n")

    for batch_num in sorted(batches):
        users = batches[batch_num]
        scores = [u["score"] for u in users]
        tiers = {}
        for u in users:
            tiers[u.get("complexity", "unknown")] = tiers.get(u.get("complexity", "unknown"), 0) + 1

        tier_str = ", ".join(f"{t}={c}" for t, c in sorted(tiers.items()))
        print(f"  Batch {batch_num:02d}: {len(users):3d} users | "
              f"score {min(scores):5.1f}–{max(scores):5.1f} | {tier_str}")
        print(f"           → {batch_paths[batch_num]}")

    print(f"\n  Run each batch with:")
    print(f"    python -m src.main --mode migrate --csv <batch_file.csv> --yes")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Generate per-batch migration CSVs from dry-run output (low-risk first)"
    )
    parser.add_argument(
        "--dry-run-dir",
        type=Path,
        help="Path to dry-run audit directory (e.g. audit/migrate_run_20260420_091546). "
             "If omitted, uses the latest dry-run in audit/.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of users per batch (default: 25)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for batch CSVs (default: data/batches/)",
    )
    parser.add_argument(
        "--exclude-empty",
        action="store_true",
        help="Exclude users with nothing to clean (all counts zero)",
    )
    args = parser.parse_args()

    if args.dry_run_dir:
        dry_run_dir = args.dry_run_dir
    else:
        dry_run_dir = find_latest_dry_run(Path("audit"))
        if not dry_run_dir:
            print("ERROR: No dry-run output found in audit/. Run a dry-run first.")
            sys.exit(1)
        print(f"Using latest dry-run: {dry_run_dir}")

    output_dir = args.output_dir or Path("data/batches")

    classifications = load_classification(dry_run_dir)
    if not classifications:
        print("ERROR: No user classifications found in dry-run output.")
        sys.exit(1)

    scored_users = []
    for entry in classifications:
        if entry.get("complexity") == "unknown":
            continue
        scored = {**entry, "score": compute_score(entry)}
        scored_users.append(scored)

    if args.exclude_empty:
        before = len(scored_users)
        scored_users = [u for u in scored_users if not _is_empty(u)]
        excluded = before - len(scored_users)
        if excluded:
            print(f"Excluded {excluded} empty users (nothing to clean)")

    if not scored_users:
        print("ERROR: No scoreable users found (all unknown/errored or empty).")
        sys.exit(1)

    scored_users = assign_batches(scored_users, args.batch_size)
    batch_paths = write_batch_csvs(scored_users, output_dir)
    plan_path = write_batch_plan(scored_users, output_dir)
    print_batch_summary(scored_users, batch_paths)
    print(f"  Full plan: {plan_path}")
    print()


if __name__ == "__main__":
    main()

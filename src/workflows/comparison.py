import json
from pathlib import Path
from typing import Dict, List

from src.utils.logging_config import print_status

_COMPARE_FIELDS = [
    "permission_count",
    "group_count",
    "content_count",
    "favorite_count",
    "subscription_count",
    "alert_count",
    "custom_view_count",
    "custom_view_default_count",
    "collection_count",
    "pulse_subscription_count",
    "pulse_alert_count",
    "webhook_count",
]


def _load_impact_summary(audit_dir: Path) -> Dict[str, Dict]:
    summary_path = audit_dir / "impact_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Impact summary not found: {summary_path}")
    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    lookup = {}
    for impact in data.get("impacts", []):
        old = impact.get("old_username")
        if old:
            lookup[old.lower()] = impact
    return lookup


def _classify_delta(field: str, before: int, after: int) -> str:
    if before == 0 and after == 0:
        return "unchanged"
    if before > 0 and after == 0:
        return "zeroed"
    if before == after:
        return "unchanged"
    if after < before:
        return "reduced"
    if after > before:
        return "increased"
    return "unchanged"


def _user_status(deltas: List[Dict]) -> str:
    statuses = {d["status"] for d in deltas}
    if all(s in ("zeroed", "unchanged") for s in statuses) and "zeroed" in statuses:
        return "fully_migrated"
    if "increased" in statuses:
        return "anomaly"
    if "reduced" in statuses and "zeroed" not in statuses:
        return "partial"
    if all(s == "unchanged" for s in statuses):
        all_zero = all(d["before"] == 0 for d in deltas)
        return "empty" if all_zero else "unchanged"
    return "mixed"


def generate_comparison_report(baseline_dir: Path, current_dir: Path) -> Path:
    if not baseline_dir.exists():
        print_status("WARN", f"Baseline directory not found: {baseline_dir}")
        return current_dir / "comparison_report.json"

    baseline_run_id = baseline_dir.name.replace("migrate_run_", "")
    current_run_id = current_dir.name.replace("migrate_run_", "")

    print_status("COMPARE", f"Loading baseline: {baseline_run_id}")
    baseline = _load_impact_summary(baseline_dir)
    print_status("COMPARE", f"Loading current: {current_run_id}")
    current = _load_impact_summary(current_dir)

    all_usernames = sorted(set(list(baseline.keys()) + list(current.keys())))

    user_diffs = []
    aggregate_before = {f: 0 for f in _COMPARE_FIELDS}
    aggregate_after = {f: 0 for f in _COMPARE_FIELDS}
    status_counts = {}

    for username in all_usernames:
        b = baseline.get(username, {})
        c = current.get(username, {})

        deltas = []
        for field in _COMPARE_FIELDS:
            before = b.get(field, 0)
            after = c.get(field, 0)
            aggregate_before[field] += before
            aggregate_after[field] += after
            delta = after - before
            status = _classify_delta(field, before, after)
            deltas.append({
                "field": field,
                "before": before,
                "after": after,
                "delta": delta,
                "status": status,
            })

        user_stat = _user_status(deltas)
        status_counts[user_stat] = status_counts.get(user_stat, 0) + 1

        anomalies = [d for d in deltas if d["status"] == "increased"]

        entry = {
            "old_username": b.get("old_username") or c.get("old_username", username),
            "new_username": b.get("new_username") or c.get("new_username"),
            "status": user_stat,
            "role_before": b.get("old_user_role"),
            "role_after": c.get("old_user_role"),
            "in_baseline": username in baseline,
            "in_current": username in current,
            "deltas": deltas,
        }

        if anomalies:
            entry["anomalies"] = anomalies

        user_diffs.append(entry)

    aggregate_deltas = {}
    for field in _COMPARE_FIELDS:
        aggregate_deltas[field] = {
            "before": aggregate_before[field],
            "after": aggregate_after[field],
            "delta": aggregate_after[field] - aggregate_before[field],
        }

    report = {
        "baseline_run": baseline_run_id,
        "current_run": current_run_id,
        "summary": {
            "total_users": len(all_usernames),
            "users_in_baseline_only": sum(1 for u in all_usernames if u in baseline and u not in current),
            "users_in_current_only": sum(1 for u in all_usernames if u not in baseline and u in current),
            "by_status": status_counts,
            "aggregate_deltas": aggregate_deltas,
        },
        "users": user_diffs,
    }

    output_path = current_dir / "comparison_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print_status("COMPARE", f"Comparison: {len(all_usernames)} users analyzed")
    for status, count in sorted(status_counts.items()):
        print_status("COMPARE", f"  {status}: {count}")

    anomaly_users = [u for u in user_diffs if u["status"] == "anomaly"]
    if anomaly_users:
        print_status("WARN", f"{len(anomaly_users)} users have unexpected increases:")
        for u in anomaly_users[:10]:
            fields = [a["field"] for a in u.get("anomalies", [])]
            print_status("WARN", f"  {u['old_username']}: {', '.join(fields)}")

    print_status("AUDIT", f"Comparison report: {output_path}")
    return output_path

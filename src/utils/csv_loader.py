import csv
import re
from pathlib import Path
from typing import List, Set, Dict
from src.utils.exceptions import ValidationError
from src.utils.logging_config import print_status


EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
MAX_CHAIN_DEPTH = 5


def load_user_mappings(csv_path: Path) -> List[Dict[str, str]]:
    if not csv_path.exists():
        raise ValidationError(f"CSV file not found: {csv_path}")
    if not csv_path.is_file():
        raise ValidationError(f"CSV path is not a file: {csv_path}")

    mappings = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValidationError("CSV file is empty or has no header row")

        required_cols = {"old_username", "new_username"}
        actual_cols = {c.strip().lower() for c in reader.fieldnames}
        missing = required_cols - actual_cols
        if missing:
            raise ValidationError(
                f"CSV missing required columns: {', '.join(sorted(missing))}. "
                f"Found: {', '.join(reader.fieldnames)}"
            )

        for row_num, row in enumerate(reader, start=2):
            old = row.get("old_username", "").strip()
            new = row.get("new_username", "").strip()

            if not old and not new:
                continue

            if not old or not new:
                raise ValidationError(
                    f"Row {row_num}: both old_username and new_username are required"
                )

            mappings.append({"old_username": old.lower(), "new_username": new.lower()})

    skipped = []
    filtered = []
    for m in mappings:
        if m["old_username"] == m["new_username"]:
            skipped.append(m["old_username"])
        else:
            filtered.append(m)

    if skipped:
        print_status("WARN", f"Skipped {len(skipped)} identical old/new mappings: {', '.join(skipped)}")

    if not filtered:
        raise ValidationError("CSV file contains no actionable user mappings (all identical)")

    _validate_mappings(filtered)
    return filtered


def _validate_mappings(mappings: List[Dict[str, str]]) -> None:
    seen_old: Set[str] = set()
    seen_new: Set[str] = set()

    for i, m in enumerate(mappings, start=1):
        old = m["old_username"]
        new = m["new_username"]

        if not EMAIL_PATTERN.match(old):
            raise ValidationError(f"Mapping {i}: invalid email format for old_username: {old!r}")
        if not EMAIL_PATTERN.match(new):
            raise ValidationError(f"Mapping {i}: invalid email format for new_username: {new!r}")

        if old.lower() == new.lower():
            continue

        old_lower = old.lower()
        new_lower = new.lower()

        if old_lower in seen_old:
            raise ValidationError(f"Mapping {i}: duplicate old_username: {old!r}")
        if new_lower in seen_new:
            raise ValidationError(f"Mapping {i}: duplicate new_username: {new!r}")

        seen_old.add(old_lower)
        seen_new.add(new_lower)

    _check_circular_references(mappings)


def _check_circular_references(mappings: List[Dict[str, str]]) -> None:
    forward: Dict[str, str] = {}
    for m in mappings:
        forward[m["old_username"].lower()] = m["new_username"].lower()

    for start in forward:
        visited = {start}
        current = start
        depth = 0

        while current in forward and depth < MAX_CHAIN_DEPTH:
            next_user = forward[current]
            if next_user in visited:
                raise ValidationError(
                    f"Circular reference detected involving: {start}"
                )
            visited.add(next_user)
            current = next_user
            depth += 1

        if depth >= MAX_CHAIN_DEPTH:
            raise ValidationError(
                f"Chain depth exceeds {MAX_CHAIN_DEPTH} starting from: {start}"
            )

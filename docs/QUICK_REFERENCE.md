# Tableau Cloud User Migrate Tool v2 — Quick Reference

## CLI Commands

```bash
python -m src.main --mode dry-run                # Impact analysis (no changes)
python -m src.main --mode clone                   # Clone users + access
python -m src.main --mode migrate                 # Full migration + deactivate
python -m src.main --mode clean-only              # Strip access + deactivate
python -m src.main --mode migrate --yes           # Skip confirmation
python -m src.main --resume-latest                # Resume last failed run
python -m src.main --resume path/to/file.json     # Resume specific checkpoint
python -m src.main --mode dry-run --compare-latest # Compare against previous dry-run
python -m src.main --mode dry-run --compare <id>   # Compare against specific run ID
python validate_setup.py                          # Pre-flight check
python validate_migration.py                      # Post-migration validation
```

## Modes at a Glance

| Mode | Creates Users | Clones Access | Transfers Ownership | Deactivates Old | User Reports | Confirmation |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| dry-run | - | - | - | - | `user_reports_dry_run/` + classification | None |
| clone | Yes | Yes | No | No | `user_reports_clone/` | Single |
| migrate | Yes | Yes | Yes | Yes | `user_reports_migrate/` | Single |
| clean-only | No | No | No | Yes | - | Double |

## Required Environment Variables

```
SERVER_URL=https://your-site.online.tableau.com
SITE_NAME=your-site-name
CSV_LOCATION=./data/user_mappings.csv
LOG_LOCATION=./audit
```

Plus **one of**: JWT credentials (`TABLEAU_CONNECTED_APP_*`) or PAT (`TOKEN_NAME` + `TOKEN_SECRET`)

## Optional Tuning Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_DELAY_MS` | 100 | Delay between API calls (ms) |
| `MAX_RETRIES` | 3 | Max retries on failure |
| `RETRY_BACKOFF_BASE` | 2.0 | Exponential backoff multiplier |
| `RETRY_JITTER` | true | Randomize retry waits |
| `RATE_LIMIT_RPS` | 10 | Max requests per second |
| `TOKEN_REFRESH_THRESHOLD_SECONDS` | 300 | Re-auth before token expiry |
| `DIMENSION_CACHE_TTL_HOURS` | 24 | Cache file expiry |
| `DIMENSION_CACHE_ENABLED` | true | Enable dimension cache |

## CSV Format

```csv
old_username,new_username
jane.doe@old.com,jane.doe@new.com
```

Usernames are **case-insensitive** — normalized to lowercase at load time.

## API Version

Auto-negotiated via `/serverinfo` at startup. Fallback: 3.19.

## Auth Priority

1. **JWT** (Connected App) — tried first
2. **PAT** — automatic fallback on 401/403

## Output Structure

```
audit/migrate_run_YYYYMMDD_HHMMSS/
├── audit_log.jsonl              # Every action logged
├── impact_summary.json          # Aggregate report with report_file refs
├── user_classification.json     # 4-tier complexity classification (dry-run)
├── classification_csvs/         # Per-tier user mapping CSVs (dry-run)
│   ├── very_high.csv
│   ├── high.csv
│   ├── moderate.csv
│   └── low.csv
├── comparison_report.json       # Dry-run diff (when --compare used)
├── execution.log                # Debug log
├── user_reports_dry_run/        # dry-run mode per-user reports
│   └── <username>.json
├── user_reports_clone/          # clone mode per-user reports
│   └── <username>.json
└── user_reports_migrate/        # migrate mode per-user reports
    └── <username>.json

audit/checkpoints/
└── checkpoint_YYYYMMDD_HHMMSS.json  # Resumable state

audit/cache/
└── dimension_cache.json         # Cached content IDs
```

## Terminal Log Prefixes

| Prefix | Meaning |
|--------|---------|
| `AUTH` | Authentication (JWT, PAT fallback, refresh) |
| `CACHE` | Dimension cache (warmup, load, save) |
| `GET` | API read with record count |
| `PUT`/`POST`/`DELETE` | API mutation |
| `SKIP` | Idempotent skip (already exists) |
| `RETRY` | Retry with backoff seconds |
| `REPORT` | User report generation |
| `CLASS` | User complexity classification |
| `COMPARE` | Dry-run comparison |
| `WARN` | Non-fatal issue |
| `CHECKPOINT` | Checkpoint saved/resumed |
| `START`/`DONE` | Lifecycle events |

## Execution Flow

1. Parse CLI args + load settings
2. Authenticate (JWT primary, PAT fallback)
3. Negotiate API version via `/serverinfo`
4. Load endpoints registry
5. Build dimension cache (three-pass: primary + enrichment + child endpoints)
6. Load CSV mappings (case-insensitive)
7. Initialize/resume checkpoints
8. Confirm with user
9. Generate per-user reports (all modes except clean-only — captures pre-mutation state)
10. Execute workflow (all reads from cache, mutations via API)
11. Output audit + checkpoint summary

## Clone Workflow Steps (per user, 9 steps)

1. Create new user (or reuse existing)
2. Clone permissions (explicit + default)
3. Clone groups
4. Clone favorites (POST per favorite)
5. Clone subscriptions (create new per subscription)
6. Clone alerts (add as recipient + transfer ownership via PUT)
7. Clone custom views (transfer ownership via PUT + migrate default user status)
8. Clone collections (create new + add items + clone permissions + delete old)

## Migrate Workflow Steps (per user, 16 steps)

1. Create new user (or reuse existing)
2. Clone permissions (explicit + default)
3. Clone groups
4. Transfer ownership (workbooks, datasources, flows, projects, virtual connections)
5. Clone favorites
6. Clone subscriptions
7. Clone alerts (add as recipient + transfer ownership)
8. Clone custom views (transfer ownership + migrate default user status)
9. Clone collections (clone-and-replace)
10. Remove old permissions (explicit + default)
11. Remove old groups
12. Remove old favorites
13. Remove old subscriptions
14. Remove old alerts (remove as recipient)
15. Deactivate old user (set to Unlicensed)

Each step is checkpointed. On resume, completed steps are skipped.

## Clean-Only Workflow Steps (per user, 7 steps)

1. Remove permissions
2. Remove groups
3. Remove favorites
4. Remove subscriptions
5. Remove alerts
6. Remove custom views
7. Deactivate (Unlicensed)

# Tableau Cloud User Migrate Tool v2 — Implementation Summary

## What This Tool Does

Migrates Tableau Cloud users from one username (email) to another. Because Tableau Cloud usernames are immutable, the tool automates the workaround: **create new user → clone access → transfer ownership → deactivate old user**.

## v2 Architecture

```
CSV Mappings
    │
    v
┌─────────────┐     ┌──────────────────┐
│  Settings    │────>│  Authenticator   │  JWT primary + PAT fallback
│  (.env)      │     │  (src/api/auth)  │
└─────────────┘     └────────┬─────────┘
                             │
                             v
                    ┌──────────────────┐
                    │  BaseClient      │  Rate limiter, retry, Retry-After
                    │  (src/api/base)  │
                    └────────┬─────────┘
                             │
                             v
                    ┌──────────────────┐
                    │  API Client      │  XML pagination, JSON support
                    │  (src/api/client)│
                    └────────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          v                  v                  v
   ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
   │  Dimension   │  │ 10 Services  │  │  Checkpoint  │
   │  Cache       │  │  (users,     │  │  Manager     │
   │              │  │   perms,     │  │              │
   │  Pre-fetches │  │   groups,    │  │  Per-user,   │
   │  all content │  │   ownership, │  │  per-step    │
   │  at startup  │  │   favorites, │  │  JSON files  │
   │              │  │   subs,      │  │              │
   │  O(types)    │  │   alerts,    │  │  --resume    │
   │  not O(users)│  │   cv, coll,  │  │              │
   │              │  │   pulse,     │  │              │
   │              │  │   webhooks)  │  │              │
   └─────────────┘  └──────┬───────┘  └──────────────┘
                           │
                           v
                  ┌──────────────────┐
                  │  UserReportMixin │  Shared report generation
                  │  + 4 Workflows   │  (all modes get per-user
                  │  dry_run, clone, │   reports before mutations)
                  │  migrate, clean  │
                  └────────┬─────────┘
                           │
                           v
                  ┌──────────────────┐
                  │  Audit Output    │  JSONL + per-user JSON
                  │  + mode-specific │  reports in separate
                  │  report folders  │  directories
                  └──────────────────┘
```

## Key Components

### Authentication (`src/api/auth.py`)
- **JWT (Connected App)**: HS256-signed tokens via `PyJWT`. 5-minute JWT lifespan, exchanged for 2-hour Tableau session.
- **PAT fallback**: Automatic on 401/403 — single retry, then fail.
- **Proactive refresh**: Re-authenticates before token expiry threshold (default: 300s before expiry).
- Ported from: Tableau Metadata Extractor's `TableauAuthenticator`.

### Rate Limiting & Retry (`src/api/base.py`)
- **RateLimiter**: asyncio semaphore (max concurrent) + token bucket (RPS). Shared across all service calls.
- **Retry-After**: 429 responses read the header for exact wait seconds. No guessing.
- **Exponential backoff**: `base^attempt * (0.5 + random())` for 500-series errors, capped at 120s.
- **HTTP/1.1 only**: HTTP/2 disabled to avoid stream exhaustion.
- Ported from: Extractor's `BaseTableauClient`.

### Dimension Cache (`src/utils/cache.py`)
Cloned from the Tableau Metadata Extractor's `DimensionCache`.
- **`DimensionRecord`**: id, type, license_type, name, attrs dict (stores nested elements like owner, project, user, view as dicts).
- **`ATTRIBUTE_MAPPINGS`**: Per-endpoint config defining which XML attributes to extract (id_fields, type_field, name_field, extra_attrs). Defined for 14 endpoint types.
- **`CHILD_ATTRIBUTE_MAPPINGS`**: For `group_users` and `user_favorites` — per-parent child endpoints.
- **Three-pass warmup**: Pass 1 fetches all primary endpoints with `cache: true` (XML and JSON formats). Pass 2 runs enrichment passes for endpoints where the list API omits owner data — collections (`ownerAlias` from list response resolved to user LUID via users cache, zero additional API calls) and virtual connections (`GET .../revisions/` with `current="true"`). Pass 3 iterates parent IDs from cache and fetches child endpoints (group members, user favorites).
- **Filter functions**: `owner_filter(user_id)` matches `record.attrs["owner"]["id"]` with `record.attrs["owner"]["luid"]` as fallback for JSON API endpoints. `user_filter(user_id)` matches `record.attrs["user"]["id"]`.
- **`get_child_records(endpoint, parent_id)`**: For child endpoints stored with composite keys `{parent_id}:{child_id}`.
- **Zero per-user list calls**: All service reads use cache. Only mutations hit the API per user. Exception: `CustomViewService._is_default_for_user()` makes a targeted GET per custom view.
- **Persistence**: JSON file with TTL-based expiry. Cache can be warm across runs.

### Checkpoint System (`src/utils/checkpoint.py`)
- **Granularity**: Per-user, per-step. Migrate has 16 checkpointed steps, clone has 9.
- **Persistence**: JSON file saved on status transitions. Step completions batched — marked dirty and flushed on the next status transition.
- **Resume**: `--resume-latest` finds most recent incomplete checkpoint. `--resume <path>` for specific file.
- **Step tracking**: `is_step_completed()` / `mark_step_completed()` enables mid-user resume.

### Services (`src/services/`)
9 services, each following the same pattern:
- Accept `TableauAPIClient` + `AuditLogger` + `DimensionCache`
- All methods are `async`
- **All reads from cache** — `get_ids(filter_fn)`, `get_child_records()`, `get_all_records()`
- 409/conflict handled as idempotent skips (logged as SKIPPED, not FAILURE)
- Audit logging on every mutation (success, failure, or skip)

| Service | Key Operations | API Format |
|---------|---------------|------------|
| `UserService` | Lookup, create, deactivate | XML |
| `PermissionService` | Clone/remove explicit + default (17 permission types) | XML |
| `GroupService` | Clone/remove group memberships | XML |
| `OwnershipService` | Transfer ownership for `ownership_transferable` content | XML |
| `FavoriteService` | Clone (POST) / remove (DELETE) per user | XML |
| `SubscriptionService` | Create new for new user / delete old | XML |
| `AlertService` | Add recipient (POST) + transfer ownership (PUT) + retry with backoff | XML |
| `CustomViewService` | Transfer ownership (PUT) + migrate default user status | XML |
| `CollectionService` | Clone-and-replace: create + items + permissions + delete | JSON + XML |
| `PulseService` | Clone/remove Pulse subscriptions + transfer alert ownership | JSON |
| `WebhookService` | Clone/transfer webhook ownership | XML |

### UserReportMixin (`src/workflows/report_mixin.py`)
Shared base class for all workflows that generate per-user reports:
- Path resolution (project hierarchy, content paths)
- Permission grouping by content with capability lists
- Owned content scan across all cached types
- Default permission resolution (maps to project/database names)
- Validation warnings (alerts, custom views, collections, credentials, PATs)
- Writes per-user JSON to mode-specific directories
- **User classification** (dry-run only): 4-tier complexity model (low/moderate/high/very_high) based on governance footprint, with per-tier CSV mapping files for phased migration
- **Dry-run comparison** (`--compare` / `--compare-latest`): Compares two dry-run impact summaries to produce per-user deltas, status classification (fully_migrated, anomaly, unchanged, etc.), and aggregate delta report

### Workflows (`src/workflows/`)
Each workflow:
- Inherits from `UserReportMixin` (except CleanupWorkflow)
- Generates per-user reports **before** any mutations (pre-mutation audit trail)
- Iterates user mappings, checking checkpoints for already-completed users
- Executes steps in order, checkpointing after each
- Returns `BatchResult` with success/failure/skip counts

## Configuration (`config/settings.py`)
- Nested dataclass hierarchy: `AuthConfig`, `ApiConfig`, `CacheConfig`, `PathConfig`
- `from_environment()` classmethod loads from `.env` via python-dotenv
- `validate()` checks auth credentials, URL format, paths, numeric ranges
- Centralized endpoint registry in `config/endpoints.yaml`
- API version auto-negotiated via `/serverinfo` at startup
- Usernames case-insensitive — normalized to lowercase in CSV loader

## Audit Trail (`reporting/`)
- **`audit_log.jsonl`**: One JSON line per operation. Fields: run_id, timestamp, action, result, usernames, object details, error.
- **`impact_summary.json`**: Aggregate report with per-user counts and `report_file` references.
- **`user_classification.json`**: 4-tier complexity classification with per-tier CSV mapping files (dry-run only).
- **`comparison_report.json`**: Per-user deltas between two dry-runs with status flags and anomaly detection.
- **`user_reports_{mode}/`**: Mode-specific per-user JSON reports with full pre-mutation state.
- **`execution.log`**: Full debug log with stack traces for errors.
- **`MigrateLogger`**: Structured logger with automatic redaction of sensitive kwargs (token, secret, password).

## Performance Characteristics

| Metric | v1 | v2 |
|--------|----|----|
| Content list calls | O(users × types) | O(types) + O(parents) at warmup, then zero |
| Auth method | PAT only | JWT + PAT fallback |
| Rate limiting | Fixed delay | Adaptive (Retry-After) |
| Resume on failure | Restart from scratch | Per-step checkpoint |
| HTTP client | sync requests | async httpx |
| Throughput | ~2-5 req/s | ~10 req/s (configurable) |
| Reporting | dry-run only | All modes (pre-mutation) |
| Collections | Not handled | Clone-and-replace |
| Custom views | Not handled | Ownership transfer + defaults |

## File Count

| Category | Files |
|----------|-------|
| Config | 3 (settings.py, endpoints.yaml, __init__.py) |
| API layer | 4 (auth.py, base.py, client.py, __init__.py) |
| Services | 12 (11 services + __init__.py) |
| Workflows | 7 (report_mixin.py, comparison.py, 4 workflows, __init__.py) |
| Utils | 8 (cache, checkpoint, csv_loader, confirmations, exceptions, logging_config, paths, __init__.py) |
| Models | 3 (mapping.py, impact.py, __init__.py) |
| Reporting | 3 (audit.py, logger.py, __init__.py) |
| Entry/docs/tests | ~20 |
| **Total** | **~62 files** |

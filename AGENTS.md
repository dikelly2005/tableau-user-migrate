# Tableau Cloud User Migrate Tool v2 — AGENTS.md

## Overview

Migrates Tableau Cloud users from one username (email) to another. Because Tableau Cloud usernames are immutable, this tool automates the workaround: **clone → migrate → cleanup**.

**Use case**: Domain migrations, email renames, M&A consolidation — any scenario where a Tableau Cloud user's email/username must change.

**Project name**: `tableau-user-migrate`

---

## Execution Flow

1. Parse CLI args + load `Settings.from_environment()` from `.env`
2. Authenticate via JWT (primary) or PAT (fallback)
3. Negotiate API version via `/serverinfo` (fallback: 3.19)
4. Load `config/endpoints.yaml`
5. Build dimension cache (three-pass warmup: primary endpoints, enrichment passes, then child endpoints)
6. Load CSV mappings (case-insensitive — normalized to lowercase at load time)
7. Initialize checkpoints (or resume from existing)
8. Confirm with user (unless `--yes` or `--resume`)
9. Generate per-user reports (all modes — captures pre-mutation state)
10. Execute workflow per mode (all reads from cache, mutations via API)
11. Output audit trail + checkpoint summary

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Tableau Cloud REST API (XML + JSON, version auto-negotiated)│
└──────────────────────────┬──────────────────────────────────┘
                           │
                           v
┌─────────────────────────────────────────────────────────────┐
│  Python Migrate Tool (src/main.py)                          │
│  - JWT/UAT + PAT fallback auth                              │
│  - API version negotiation via /serverinfo                  │
│  - Rate limiting, retry with Retry-After                    │
│  - Dimension cache (primary + enrichment + child endpoints) │
│  - All service reads from cache, zero per-user list calls   │
│  - UserReportMixin: shared reporting across all modes       │
│  - Resumable per-user, per-step checkpoints                 │
│  - Case-insensitive username matching                       │
│  - JSONL audit trail + permission diffs                     │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           v
┌─────────────────────────────────────────────────────────────┐
│  Audit Output (audit/migrate_run_<timestamp>/)              │
│  - audit_log.jsonl       (every action: success/fail/skip)  │
│  - impact_summary.json   (aggregate report w/ report refs)  │
│  - user_reports_dry_run/ (dry-run per-user JSON reports)    │
│  - user_reports_clone/   (clone per-user JSON reports)      │
│  - user_reports_migrate/ (migrate per-user JSON reports)    │
│  - execution.log         (debug log)                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
tableau_user_migrate/
├── AGENTS.md
├── .env.example
├── .gitignore
├── requirements.txt
├── validate_setup.py                # Pre-flight validator
├── validate_migration.py            # Post-migration validator
│
├── config/
│   ├── settings.py                  # Nested dataclass config from .env
│   └── endpoints.yaml               # Centralized endpoint registry
│
├── src/
│   ├── main.py                      # Async CLI entrypoint + orchestration
│   ├── api/
│   │   ├── auth.py                  # JWT/UAT + PAT fallback auth
│   │   ├── base.py                  # RateLimiter, retry, Retry-After, httpx
│   │   └── client.py               # XML pagination, version negotiation
│   ├── services/
│   │   ├── users.py                 # Lookup (cache-first), create, deactivate
│   │   ├── permissions.py           # Clone/remove explicit + default permissions
│   │   ├── groups.py                # Clone/remove — reads group_users from cache
│   │   ├── ownership.py             # Transfer — iterates endpoints with ownership_transferable
│   │   ├── favorites.py             # Clone (POST) / remove (DELETE) per user
│   │   ├── subscriptions.py         # Create new for new user (POST) / delete old (DELETE)
│   │   ├── alerts.py                # Add recipient (POST) + transfer ownership (PUT) + retry with backoff
│   │   ├── custom_views.py          # Transfer ownership (PUT) + default user status migration
│   │   └── collections.py           # Clone-and-replace: create new + add items + clone perms + delete old (JSON API)
│   ├── workflows/
│   │   ├── report_mixin.py          # UserReportMixin: shared report generation for all modes
│   │   ├── dry_run.py               # Impact analysis + classification → user_reports_dry_run/
│   │   ├── comparison.py            # Compare two dry-runs → comparison_report.json
│   │   ├── clone.py                 # Reports → user_reports_clone/ then clone access (9 steps)
│   │   ├── migrate.py               # Reports → user_reports_migrate/ then clone + transfer + cleanup (16 steps)
│   │   └── cleanup.py               # Strip access + deactivate (7 steps)
│   └── utils/
│       ├── cache.py                 # DimensionCache: ATTRIBUTE_MAPPINGS, three-pass warmup,
│       │                            #   enrichment passes, populate/get_ids/filter_fn, child endpoints, save/load/TTL
│       ├── checkpoint.py            # Per-user, per-step JSON checkpoints
│       ├── csv_loader.py            # CSV validation + case-insensitive normalization
│       ├── exceptions.py            # TableauMigrateError hierarchy
│       ├── confirmations.py         # Interactive CLI prompts
│       ├── paths.py                 # Endpoint path + element tag resolution
│       └── logging_config.py        # get_logger() + print_status()
│
├── models/
│   ├── mapping.py                   # UserMapping, BatchResult, MappingState
│   └── impact.py                    # ImpactAnalysis, Permission, UXArtifact
│
├── reporting/
│   ├── logger.py                    # MigrateLogger with sensitive-value redaction
│   └── audit.py                     # JSONL audit trail (AuditAction/AuditResult enums)
│
├── docs/                            # 7 documentation files
├── tests/                           # Unit tests for cache, checkpoint, settings, paths, csv_loader
├── audit/                           # Per-run output directories
├── output/
├── data/                            # User mapping CSVs
└── REFERENCE/                       # Read-only v1 code (DO NOT MODIFY)
```

---

## Service API Methods

Each service's REST API methods, verified against the Tableau Cloud REST API:

### UserService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| List/filter users | GET | `/sites/{site_id}/users?filter=name:eq:{username}` |
| Create user | POST | `/sites/{site_id}/users` |
| Update site role | PUT | `/sites/{site_id}/users/{user_id}` |
| Deactivate (unlicense) | PUT | `/sites/{site_id}/users/{user_id}` (siteRole=Unlicensed) |

### PermissionService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| Get permissions | GET | `/sites/{site_id}/{content_type}/{id}/permissions` |
| Add permissions | PUT | `/sites/{site_id}/{content_type}/{id}/permissions` |
| Delete permission | DELETE | `/sites/{site_id}/{content_type}/{id}/permissions/{grantee_type}/{grantee_id}/{capability}/{mode}` |

### GroupService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| List user groups | GET | `/sites/{site_id}/users/{user_id}/groups` |
| Add user to group | POST | `/sites/{site_id}/groups/{group_id}/users` |
| Remove user from group | DELETE | `/sites/{site_id}/groups/{group_id}/users/{user_id}` |

### OwnershipService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| Transfer ownership | PUT | `/sites/{site_id}/{content_type}/{content_id}` with `<owner id="..."/>` |

### FavoriteService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| Add favorite | POST | `/sites/{site_id}/favorites/{user_id}` |
| Delete favorite | DELETE | `/sites/{site_id}/favorites/{user_id}/{content_type}/{content_id}` |

### SubscriptionService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| Create subscription | POST | `/sites/{site_id}/subscriptions` |
| Delete subscription | DELETE | `/sites/{site_id}/subscriptions/{subscription_id}` |

### AlertService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| Add user to alert | POST | `/sites/{site_id}/dataAlerts/{alert_id}/users` |
| Transfer ownership | PUT | `/sites/{site_id}/dataAlerts/{alert_id}` with `<owner id="..."/>` |
| Remove user from alert | DELETE | `/sites/{site_id}/dataAlerts/{alert_id}/users/{user_id}` |

### CustomViewService
| Operation | Method | Endpoint |
|-----------|--------|----------|
| Transfer ownership | PUT | `/sites/{site_id}/customviews/{cv_id}` with `<owner id="..."/>` |
| Get default users | GET | `/sites/{site_id}/customviews/{cv_id}/default/users` |
| Set default for user | POST | `/sites/{site_id}/customviews/{cv_id}/default/users/{user_id}` |
| Remove default for user | DELETE | `/sites/{site_id}/customviews/{cv_id}/default/users/{user_id}` |
| Delete custom view | DELETE | `/sites/{site_id}/customviews/{cv_id}` |

### CollectionService (JSON API)
| Operation | Method | Endpoint | Format |
|-----------|--------|----------|--------|
| Create collection | POST | `/-/collections` | JSON |
| Get collection items | GET | `/-/collections/{id}/items` | JSON |
| Add items to collection | POST | `/-/collections/{id}/items` | JSON |
| Delete collection | DELETE | `/-/collections/{id}` | JSON |
| Get collection permissions | GET | `/sites/{site_id}/collections/{id}/permissions` | XML |
| Add collection permissions | PUT | `/sites/{site_id}/collections/{id}/permissions` | XML |

---

## Dimension Cache Design

Cloned from the Tableau Metadata Extractor's `DimensionCache`.

### DimensionRecord
```python
@dataclass
class DimensionRecord:
    id: str
    type: Optional[str] = None          # siteRole, contentUrl, etc.
    license_type: Optional[str] = None  # licenseType, minimumSiteRole
    name: Optional[str] = None
    attrs: dict = field(default_factory=dict)  # owner, project, user, view, etc.
```

### ATTRIBUTE_MAPPINGS
Per-endpoint config that defines which XML attributes to extract:
- `id_fields`: which attribute holds the ID (id, luid)
- `type_field`: role/type attribute (siteRole, contentUrl, type)
- `name_field`: display name (name, subject)
- `extra_attrs`: nested elements to capture (owner, project, user, view, workbook, etc.)

Defined for: `users`, `groups`, `projects` (incl. owner), `workbooks`, `views`, `datasources`, `flows`, `virtual_connections`, `databases`, `tables`, `collections`, `custom_views`, `subscriptions`, `data_alerts`, `pulse_definitions`, `pulse_subscriptions`, `webhooks`, `pulse_alerts`

### CHILD_ATTRIBUTE_MAPPINGS
For per-parent child endpoints:
- `group_users` (parent: groups) — who is in each group
- `user_favorites` (parent: users) — what each user has favorited

### Three-Pass Warmup
1. **Primary pass**: Fetch all endpoints with `cache: true` and no `parent`. Populates `DimensionRecord` per item. JSON endpoints use `format: json` with `response_key`.
2. **Enrichment passes**: Post-process endpoints where the list API omits owner data:
   - **Collections**: The list endpoint (`GET /-/collections`) returns `ownerAlias` (username) but no owner LUID. The enrichment pass resolves `ownerAlias` to a user LUID via the already-cached users dimension. Zero additional API calls.
   - **Virtual connections**: `GET /sites/{site_id}/virtualconnections/{luid}/revisions/` per VC — the list endpoint omits `owner`/`project`. The revision with `current="true"` provides `publisher.id` which is stored as `owner.id`.
3. **Child pass**: For endpoints in `CHILD_ATTRIBUTE_MAPPINGS` with a `parent`, iterate parent IDs from cache and fetch children. Stores composite key `{parent_id}:{child_id}`.

### Filter Functions
Services query cache using `get_ids(endpoint, filter_fn)`:
- `owner_filter(user_id)` — matches `record.attrs["owner"]["id"]` or `record.attrs["owner"]["luid"]` as fallback (used by alerts, custom_views, ownership, collections, dry-run owned content scan). The `luid` fallback ensures correctness for JSON API endpoints that may return `luid` instead of `id`.
- `user_filter(user_id)` — matches `record.attrs["user"]["id"]` (used by subscriptions)
- `get_child_records(endpoint, parent_id)` — matches `record.attrs["_parent_id"]` (used by groups, favorites)

### Zero Per-User List Calls
Every service reads exclusively from cache during workflow execution. Only mutation API calls (POST/PUT/DELETE) hit the server per user. The one exception is `CustomViewService._is_default_for_user()` which makes a targeted GET per custom view to check default user status.

---

## Endpoints Registry (`config/endpoints.yaml`)

Single flat `endpoints:` key. Each entry declares capabilities via flags:

| Flag | Purpose | Consumers |
|------|---------|-----------|
| `cache: true` | Pre-fetch into dimension cache at startup | `cache.warmup()` |
| `permissions_endpoint` | Enable permission cloning/removal | `PermissionService` |
| `ownership_transferable: true` | Enable ownership transfer via PUT | `OwnershipService` |
| `parent: <name>` | Child endpoint — fetch per parent ID | `cache.warmup()` child pass |
| `format: json` | Use JSON instead of XML for API calls | `cache.warmup()` |

### Endpoint Categories
- **Primary content** (cached): users, groups, projects (ownership_transferable), workbooks, views, datasources, flows, virtual_connections, databases, tables, collections, subscriptions, data_alerts, custom_views, pulse_definitions, pulse_subscriptions, webhooks, pulse_alerts
- **Child endpoints**: group_users, user_favorites, user_personal_access_tokens, custom_view_default_users
- **Permission endpoints**: workbook/view/datasource/flow/project/vc/database/table/collection permissions
- **Default permission endpoints**: 7 project-level + 1 database-level

---

## Key Technical Details

- **API version**: Auto-negotiated via `/api/3.19/serverinfo` → `restApiVersion`. All subsequent calls use the negotiated version. Fallback: 3.19.
- **Auth**: JWT (HS256, Connected App) primary. PAT fallback on 401/403. Proactive token refresh before expiry.
- **HTTP**: async httpx, HTTP/1.1 only. RateLimiter: asyncio semaphore + token bucket.
- **Retry**: Retry-After header for 429. Exponential backoff + jitter for 500-series. Max 120s wait.
- **Usernames**: Case-insensitive. Normalized to lowercase at CSV load time. Cache lookups use `.lower()`.
- **Idempotent**: Safe to re-run. 409/conflict = skip (not failure).
- **Audit**: Every mutation logged to JSONL. Every step checkpointed.
- **Content paths**: Full project hierarchy paths resolved from cache (walks `parentProjectId` chain). Content without a project is labeled `Personal Space/`.

---

## Execution Modes

| Mode | Steps | Behavior |
|------|-------|----------|
| `dry-run` | Report only | Read-only impact analysis. Per-user reports → `user_reports_dry_run/`. User classification + per-tier CSVs. No changes made. |
| `clone` | 9 steps | Reports → `user_reports_clone/`. Creates new user, clones all access. Old user stays active. |
| `migrate` | 16 steps | Reports → `user_reports_migrate/`. Clone + transfer ownership + deactivate old user. |
| `clean-only` | 7 steps | Strips all access + deactivates. No reports generated. |

### Dry-Run Comparison (`--compare` / `--compare-latest`)
Compares the current dry-run against a previous dry-run to verify migration outcomes. Intended workflow:

1. `dry-run` (baseline — pre-migration state)
2. `migrate` or `clone` + `clean-only`
3. `dry-run --compare-latest` (verify old users zeroed, detect anomalies)

Per-user status classification:
| Status | Meaning |
|--------|---------|
| `fully_migrated` | All non-zero counts dropped to zero |
| `empty` | Was already zero across the board |
| `unchanged` | Counts identical (nothing happened) |
| `reduced` | Some counts decreased but not all zeroed |
| `partial` | Mix of zeroed and non-zeroed fields |
| `anomaly` | One or more counts **increased** unexpectedly |
| `mixed` | Combination of changes |

Output: `comparison_report.json` with aggregate deltas, per-user diffs, and anomaly flags.

### Clone Steps (9)
1. create_user
2. clone_permissions (explicit + default)
3. clone_groups
4. clone_favorites
5. clone_subscriptions
6. clone_alerts (recipient only — ownership not transferable)
7. clone_custom_views (ownership transfer + default user migration)
8. clone_collections (create new + add items + clone permissions + delete old)

### Migrate Steps (16)
1. create_user
2. clone_permissions (explicit + default)
3. clone_groups
4. transfer_ownership (workbooks, datasources, flows, projects, VCs)
5. clone_favorites
6. clone_subscriptions
7. clone_alerts (recipient only)
8. clone_custom_views (ownership transfer + default user migration)
9. clone_collections (clone-and-replace)
10. remove_permissions
11. remove_groups
12. remove_favorites
13. remove_subscriptions
14. remove_alerts
15. deactivate (set to Unlicensed)

### Clean-Only Steps (7)
1. remove_permissions
2. remove_groups
3. remove_favorites
4. remove_subscriptions
5. remove_alerts
6. remove_custom_views
7. deactivate

---

## UserReportMixin — Shared Report Generation

All modes (dry-run, clone, migrate) generate per-user JSON reports **before** any mutations. This provides a full pre-mutation audit trail. Reports are written to mode-specific folders.

### Report Generation Flow
1. Lookup old and new user from cache
2. Fetch explicit + default permissions (with tabbed workbook skip count)
3. Fetch groups, owned content, favorites, subscriptions, alerts, custom views, collections, pulse subscriptions, pulse alerts, webhooks
4. Build validation warnings (credentials, alerts, custom views, collections, PATs)
5. Write per-user JSON report to `user_reports_{mode}/`
6. Write aggregate `impact_summary.json`

### Audit Output Structure
```
audit/migrate_run_<timestamp>/
├── impact_summary.json          # Aggregate counts + report_file refs
├── user_classification.json     # 4-tier complexity classification (dry-run only)
├── classification_csvs/         # Per-tier CSV mapping files (dry-run only)
│   ├── very_high.csv
│   ├── high.csv
│   ├── moderate.csv
│   └── low.csv
├── comparison_report.json       # Dry-run diff (when --compare or --compare-latest used)
├── audit_log.jsonl              # Every mutation: success/fail/skip
├── execution.log                # Debug log
├── user_reports_dry_run/        # dry-run mode
│   └── <username>.json
├── user_reports_clone/          # clone mode
│   └── <username>.json
└── user_reports_migrate/        # migrate mode
    └── <username>.json
```

### Per-User Report Schema
```json
{
  "old_username": "jane.doe@old-domain.com",
  "new_username": "jane.doe@new-domain.com",
  "old_user": { "found": true, "role": "Explorer" },
  "new_user": { "found": false, "role": null },
  "owned_content": [
    {
      "content_type": "workbooks",
      "content_id": "...",
      "content_name": "Q4 Dashboard",
      "path": "Finance/Dashboards/Q4 Dashboard"
    }
  ],
  "explicit_permissions": [
    {
      "content_type": "workbook_permissions",
      "content_id": "...",
      "content_name": "Revenue Report",
      "path": "Finance/Reports/Revenue Report",
      "is_default": false,
      "capabilities": [
        { "name": "Read", "mode": "Allow" },
        { "name": "ExportData", "mode": "Deny" }
      ]
    }
  ],
  "default_permissions": [ /* same structure, is_default: true, path = project path */ ],
  "favorites": [
    {
      "content_type": "workbook",
      "content_id": "...",
      "content_name": "Sales Overview",
      "path": "Sales/Sales Overview"
    }
  ],
  "groups": [
    { "group_id": "...", "group_name": "Marketing Team" }
  ],
  "subscriptions": [
    {
      "subscription_id": "...",
      "subject": "Weekly Sales Report",
      "content_type": "View",
      "content_id": "...",
      "path": "Sales/Reports/Weekly Sales"
    }
  ],
  "alerts": [
    {
      "alert_id": "...",
      "subject": "Revenue threshold",
      "view_id": "...",
      "path": "Finance/Dashboards/Revenue Alert View"
    }
  ],
  "custom_views": [
    {
      "custom_view_id": "...",
      "custom_view_name": "My Filter Set",
      "view_id": "...",
      "workbook_id": "...",
      "workbook_name": "Sales Dashboard",
      "path": "Sales/Dashboards/My Filter Set"
    }
  ],
  "collections": [
    {
      "collection_id": "...",
      "collection_name": "Q4 Reports",
      "description": "Quarterly finance collection"
    }
  ],
  "summary": {
    "owned_content_count": 5,
    "explicit_permission_count": 12,
    "default_permission_count": 3,
    "favorite_count": 8,
    "group_count": 4,
    "subscription_count": 2,
    "alert_count": 1,
    "custom_view_count": 3,
    "collection_count": 1,
    "pulse_subscription_count": 0,
    "pulse_alert_count": 0,
    "webhook_count": 0
  },
  "validation_errors": [],
  "validation_warnings": [
    "1 data alerts — new user will be added as recipient but ownership cannot be transferred via REST API",
    "3 custom views will have ownership transferred to new user",
    "1 collections will be cloned (recreated with items and permissions transferred to new user)",
    "5 owned content items ... may have embedded data connections ...",
    "Personal Access Tokens (PATs), Connected App tokens, OAuth saved credentials, and embedded datasource passwords cannot be migrated — new user must recreate all authentication credentials"
  ]
}
```

### Content Path Resolution
- **Projects**: Walks `parentProjectId` chain → `Top Level/Mid Level/Leaf Project`
- **Workbooks, datasources, flows, virtual connections, collections**: Resolves project from `project.id` attr → `Project Path/Content Name`
- **Views**: Resolves via parent workbook's project → `Project Path/View Name`
- **Custom views**: Resolves via parent workbook's project → `Project Path/Custom View Name`
- **Default permissions**: Resolves to project path (content_id = project ID)
- **No project**: Content in Personal Space → `Personal Space/Content Name`

### Owned Content Scan
The report scans **all** cached content types with `owner` attributes — not just `ownership_transferable` endpoints. This includes: `workbooks`, `views`, `datasources`, `flows`, `virtual_connections`, `projects`, `collections`, `custom_views`.

### User Classification (dry-run only)
Generated as `user_classification.json` alongside the impact summary. Categorizes each user into one of four complexity tiers based on their governance footprint:

| Tier | Signals | Risk |
|------|---------|------|
| **very_high** | Owns projects OR has default permission grants | Governance role — missteps break broad access |
| **high** | Owns >10 content items OR owns published data sources | Content producer — decisions affect many consumers |
| **moderate** | Owns 1–10 items, OR has explicit permissions + UX artifacts (≥5 favorites, ≥1 custom view, ≥1 subscription) | Explorer/occasional publisher with meaningful personalization |
| **low** | No owned content, no/few explicit permissions, minimal UX artifacts | Content consumer — inherits access via groups |

Users are sorted by tier (very_high first), then within each tier by **most recent activity** descending. Activity is the latest of: `lastLogin`, or `updatedAt` on any owned workbook/flow/datasource. Users with no activity data sort last within their tier.

---

## Known API Limitations

| Artifact | Limitation | Handling |
|----------|-----------|----------|
| **Data alerts** | Ownership transferred via PUT after adding new user as recipient. Retry with backoff on transient failures. |
| **Collections** | No ownership update endpoint | Clone-and-replace: create new collection, add items, clone permissions, delete old. |
| **PATs** | Cannot be cloned or transferred (secrets) | Warning in user report. User must recreate. |
| **Connected App tokens** | Cannot be migrated | Warning in user report. |
| **OAuth / embedded credentials** | Cannot be cloned via API | Warning in user report. |
| **Custom view defaults** | Requires per-view API call (not cached) | `GET .../default/users` checked per custom view during clone/migrate. |

---

## CLI

```bash
python -m src.main --mode dry-run
python -m src.main --mode clone --yes
python -m src.main --mode migrate --yes
python -m src.main --mode clean-only --yes
python -m src.main --resume-latest
python -m src.main --mode dry-run --compare-latest    # Compare against previous dry-run
python -m src.main --mode dry-run --compare 20260420_091546  # Compare against specific run
python -m src.main --force-refresh
python validate_setup.py
```

---

## Configuration (.env)

### Required
- `SERVER_URL`, `SITE_NAME`, `CSV_LOCATION`, `LOG_LOCATION`

### Auth — JWT (Primary)
- `TABLEAU_CONNECTED_APP_CLIENT_ID`, `TABLEAU_CONNECTED_APP_SECRET_ID`, `TABLEAU_CONNECTED_APP_SECRET_VALUE`, `TABLEAU_USERNAME`

### Auth — PAT (Fallback)
- `TOKEN_NAME`, `TOKEN_SECRET`

### Optional Tuning
- `API_DELAY_MS` (100), `MAX_RETRIES` (3), `RETRY_BACKOFF_BASE` (2.0), `RETRY_JITTER` (true)
- `RATE_LIMIT_RPS` (10), `TOKEN_REFRESH_THRESHOLD_SECONDS` (300)
- `DIMENSION_CACHE_TTL_HOURS` (24), `DIMENSION_CACHE_ENABLED` (true)

---

## Conventions

- **Async**: All I/O uses asyncio. Services are async.
- **HTTP**: httpx, HTTP/1.1 only
- **Logging**: `get_logger(__name__)` for debug, `print_status(PREFIX, msg)` for terminal
- **Types**: Type hints on all public functions
- **Config**: `.env` → `config/settings.py` (nested dataclasses)
- **Endpoints**: Single source of truth in `config/endpoints.yaml`
- **Cache**: All reads from `DimensionCache`. Zero per-user list API calls (except custom view default check). Enrichment passes for collections (detail endpoint) and virtual connections (revisions endpoint) run during warmup.
- **No comments in code** unless explicitly requested
- **Dataclasses** for models, no ORMs
- **Custom exception hierarchy** rooted at `TableauMigrateError`
- **XML payloads** as f-string literals (services using XML API)
- **JSON payloads** via `json.dumps()` (CollectionService using JSON API)

---

## Security

- Never log or commit secrets
- `.env` never committed
- `MigrateLogger` auto-redacts sensitive kwargs
- HTTPS only, HTTP/1.1 only

---

## REFERENCE/ Folder

Read-only. **Do not modify.**

| Path | Purpose |
|------|---------|
| `REFERENCE/tableau_cloud_user_rekey/` | v1 of this project |
| `REFERENCE/tableau_metadata_extractor/` | Source of auth, retry, cache, checkpoint patterns |

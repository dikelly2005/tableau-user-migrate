# Migration Workflow — Operational Guide

This document is the single operational reference for running the Tableau Cloud User Migrate Tool. It combines the execution flow, CLI usage, verification criteria, and recovery procedures into one place.

---

## Quick Start

```bash
# 1. Dry-run (impact analysis, no changes)
python -m src.main --mode dry-run

# 2. Migrate (full workflow with verification gate)
python -m src.main --mode migrate --yes

# 3. Resume a failed run
python -m src.main --resume-latest

# 4. Migrate a specific batch
python -m src.main --mode migrate --csv data/batches/batch_01.csv --yes

# 5. Skip endpoint discovery (faster for resumes)
python -m src.main --mode migrate --resume-latest
```

---

## Execution Phases

The tool executes in 12 phases. Each phase has a token refresh check. Failed users are excluded from subsequent phases.

| Phase | Name | Actions |
|-------|------|---------|
| 0 | Pre-Flight | Load `.env`, validate CSV mappings |
| 1 | Discovery | Run `discover_tableau_endpoints.py` → update `endpoints.yaml`, `endpoints_full.yaml`, `scopes.yaml` |
| 2 | Authentication | JWT (primary) or PAT (fallback), negotiate API version |
| 3 | Cache Warmup | Fetch all list endpoints → enrichment → child endpoints |
| 4 | Analysis | Generate baseline per-user reports, classify complexity, init checkpoints |
| 5 | User Creation | Create new user accounts (3-5s delay per user for propagation) |
| 6 | Access Cloning | Clone permissions + groups to new user |
| 6.5 | Personal Space Relocation | Move workbooks/datasources/flows from Personal Space → "User Migration Artifacts" project |
| 7 | Ownership Transfer | Transfer workbooks, datasources, flows, projects, VCs, collections, custom views |
| 8 | Artifact Cloning | Clone subscriptions, alerts, favorites, pulse subscriptions, webhooks |
| 9 | Verification | Refresh cache from API, verify all 13 dimensions per user, gate cleanup on pass/fail |
| 10 | Cleanup | Remove old user's access (permissions, groups, favorites, subscriptions, alerts, CV defaults, pulse subs, pulse alerts, webhooks) → deactivate |
| 11 | Final Audit | Write audit trail, verification results, checkpoint summary |

---

## Phase 9: Verification Checks

After all mutations complete, the cache is fully refreshed from the API and each user is verified across 13 dimensions:

| # | Dimension | Pass condition | Fail condition |
|---|-----------|---------------|----------------|
| 1 | Site role & license | New user's siteRole matches expected | Mismatch |
| 2 | Owned content (old) | Zero remaining items | Old user still owns content |
| 3 | Owned content (new) | New user owns items | *(counted only)* |
| 4 | Collections | Old user owns zero | Still owns collections |
| 5 | Custom views | Old user owns zero | Still owns custom views |
| 6 | Groups | New user count ≥ old user count | Zero or count mismatch |
| 7 | Permissions | New user has ≥50% of old user's count | Zero or significantly fewer |
| 8 | Favorites | Present if old user had any | Zero when expected |
| 9 | Subscriptions | Present if old user had any | Zero when expected |
| 10 | Alerts | New user owns alerts | Zero ownership when expected |
| 11 | Pulse subscriptions | Present if old user had any | Zero when expected |
| 12 | Pulse alerts | New user owns pulse alerts | Zero when expected |
| 13 | Webhooks | New user owns webhooks | Zero when expected |

**Only "Pass" users proceed to cleanup. "Fail" users are blocked and logged for manual review.**

Output: `audit/migrate_run_<id>/verification_results.json`

---

## CLI Reference

```bash
python -m src.main [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--mode {dry-run,clone,migrate,clean-only}` | Execution mode (default: dry-run) |
| `--yes` | Skip interactive confirmation |
| `--csv PATH` | Override CSV_LOCATION from .env |
| `--batch-size N` | Process users in batches of N |
| `--resume PATH` | Resume from specific checkpoint file |
| `--resume-latest` | Resume latest incomplete checkpoint |
| `--compare ID` | Compare dry-run against specific run |
| `--compare-latest` | Compare against most recent dry-run |
| `--rollback PATH` | Rollback using audit log file |
| `--rollback-delete-users` | Also deactivate new users during rollback |

---

## Modes

| Mode | What it does | Destructive? |
|------|-------------|--------------|
| `dry-run` | Impact analysis + user classification. No API mutations. | No |
| `clone` | Creates new users, clones all access. Old users stay active. | Creates users |
| `migrate` | Clone + relocate Personal Space + transfer ownership + verify (13 dimensions) + cleanup + deactivate. | Yes |
| `clean-only` | Pre-cleanup reports + relocate Personal Space + strip all access + deactivate + post-cleanup verification (13 dimensions). | Yes |

---

## Required Waits

| After | Delay | Reason |
|-------|-------|--------|
| User creation | 3-5s | New user ID propagation in Tableau Cloud |
| Ownership transfer (same type) | 2-3s | Avoid 409 conflict errors |
| Custom view ownership PUT | 1-2s | Owner must propagate before default user POST |
| Alert recipient → ownership | 2s | Recipient must exist before ownership transfer |

---

## Known Limitations

### Cannot Be Migrated (API restrictions)
- **Personal Access Tokens** — Secrets cannot be read or cloned
- **OAuth saved credentials** — Tied to user's credential store
- **Embedded datasource passwords** — Must be re-entered by new user
- **Connected App tokens** — Must be recreated
- **Historical activity** — View history, access logs tied to immutable user ID

### Design Choices
- **Collections** — Ownership transferred via PUT with `ownerLuid` (batch capable). If PUT fails, falls back to clone-and-replace (new LUID, old bookmarks break).
- **Serial processing** — Users processed one at a time for safety. Use `--batch-size` for progress visibility.
- **No automatic rollback** — Destructive operations are intentional. Use `--rollback` with audit log for manual reversal.

### External Coordination Required
- **SAML/OIDC** — Update IdP assertions in parallel with migration
- **Credential re-auth** — Communicate to users that they must reconnect data sources post-migration (see `docs/PRE_MIGRATION_CHECKLIST.md`)

---

## Recovery Procedures

### Resume a Failed Run
```bash
python -m src.main --resume-latest
```
Picks up from the last incomplete checkpoint. Safe to re-run — all steps are idempotent (409/conflict = skip).

### Investigate Verification Failures
```bash
cat audit/migrate_run_<id>/verification_results.json | python -m json.tool
```
Look at `details.<username>.issues` for specific failure codes. Common issues:
- `old_user_still_owns_N_items` — Ownership transfer failed silently. Re-run will retry.
- `site_role_mismatch` — User's role was changed externally. Update CSV or fix in Tableau.
- `new_user_has_no_groups` — Group clone failed (group may have been deleted between phases).

### Rollback a Completed Migration
```bash
python -m src.main --rollback audit/migrate_run_<id>/audit_log.jsonl
python -m src.main --rollback audit/migrate_run_<id>/audit_log.jsonl --rollback-delete-users
```
Reverses ownership transfers and re-activates old users. Use `--rollback-delete-users` to also deactivate the newly created users.

### Manual Recovery (last resort)
1. Read `audit/migrate_run_<id>/user_reports_migrate/<user>.json` for pre-mutation state
2. Use the per-user report to manually restore permissions, groups, ownership via Tableau Cloud UI or API

---

## Audit Output Structure

```
audit/migrate_run_<timestamp>/
├── audit_log.jsonl              # Every mutation: action, result, timestamp
├── impact_summary.json          # Aggregate counts + report file refs
├── verification_results.json    # Pass/fail + dimension counts per user
├── user_reports_migrate/        # Pre-mutation per-user JSON reports
│   └── <username>.json
└── execution.log                # Debug log
```

---

## Configuration (.env)

### Required
| Variable | Purpose |
|----------|---------|
| `SERVER_URL` | Tableau Cloud URL (e.g., `https://10ax.online.tableau.com`) |
| `SITE_NAME` | Site content URL identifier |
| `CSV_LOCATION` | Path to user mapping CSV |
| `LOG_LOCATION` | Path to audit output directory |

### Auth — JWT (Primary)
| Variable | Purpose |
|----------|---------|
| `TABLEAU_CONNECTED_APP_CLIENT_ID` | Connected App client ID |
| `TABLEAU_CONNECTED_APP_SECRET_ID` | Secret ID |
| `TABLEAU_CONNECTED_APP_SECRET_VALUE` | Secret value |
| `TABLEAU_USERNAME` | Admin username for JWT sub claim |

### Auth — PAT (Fallback)
| Variable | Purpose |
|----------|---------|
| `TOKEN_NAME` | PAT name |
| `TOKEN_SECRET` | PAT secret |

### Optional Tuning
| Variable | Default | Purpose |
|----------|---------|---------|
| `MIGRATION_ARTIFACTS_PROJECT` | User Migration Artifacts | Project name for relocated Personal Space content |
| `API_DELAY_MS` | 100 | Delay between API calls |
| `RATE_LIMIT_RPS` | 10 | Max requests per second |
| `MAX_RETRIES` | 3 | Retry count for failed requests |
| `TOKEN_REFRESH_THRESHOLD_SECONDS` | 300 | Refresh token N seconds before expiry |
| `DIMENSION_CACHE_TTL_HOURS` | 24 | Cache expiry |

---

## Related Documents

- `docs/optimal_execution_flow.md` — Detailed phase-by-phase technical design with parallelism and wait annotations
- `docs/PRE_MIGRATION_CHECKLIST.md` — Communication template for users about credential re-authentication
- `docs/KNOWN_LIMITATIONS_AND_RECOVERY.md` — Full limitation details and recovery procedures
- `docs/TEST_ENVIRONMENT.md` — Test environment setup for validation
- `AGENTS.md` — CoCo instruction file with full architecture reference

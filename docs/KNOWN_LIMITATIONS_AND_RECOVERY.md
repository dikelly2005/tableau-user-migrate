# Tableau Cloud User Migrate Tool v2 — Known Limitations & Recovery

## Current Limitations

### 1. Collections Cannot Have Ownership Transferred
**Status**: Tableau REST API limitation — no `PUT` endpoint for collection ownership
**Impact**: Tool uses clone-and-replace: creates a new collection with the same name, items, and permissions, then deletes the old one.
**Mitigations**:
- All collection items (content references) are preserved
- All explicit permissions are cloned to the new collection
- Old user's grants are remapped to new user during permission clone
**Side effect**: The collection gets a new LUID. Any external bookmarks or links to the old collection URL will break.

### 2. Authentication Credentials Cannot Be Migrated
**Status**: Tableau platform security design
**Impact**: The following must be manually recreated by the new user after migration:
- Personal Access Tokens (PATs)
- Connected App direct trust tokens
- OAuth saved credentials (Google, Salesforce, Snowflake, etc.)
- Embedded datasource passwords
- Service account connections using per-user credential stores
**Mitigations**:
- User reports include a standard warning about credential recreation
- See `docs/PRE_MIGRATION_CHECKLIST.md` for a communication template

### 3. Serial User Processing
**Status**: By design for safety
**Impact**: Users are processed one at a time. For large batches (500+ users), this can take hours.
**Mitigations**:
- Dimension cache eliminates redundant API calls (v2 improvement)
- Rate limiter maximizes throughput within API limits
- Checkpoint system allows interruption and resume

### 4. Historical Activity Not Migrated
**Status**: Tableau platform limitation
**Impact**: View history, access logs, and usage metrics are tied to immutable internal user IDs.
**Workaround**: None. Historical activity remains associated with the old user ID in Tableau's system.

### 5. Federated Auth (SAML/OIDC) Coordination Required
**Status**: Out of scope for this tool
**Impact**: This tool updates Tableau Cloud user records but cannot modify IdP claims.
**Workaround**: Coordinate with your IdP team to update SAML/OIDC assertions in parallel with the migration operation.

### 6. No Automatic Rollback
**Status**: By design — destructive operations are intentionally irreversible
**Impact**: Once migrate or clean-only completes, old user permissions/groups are removed.
**Mitigations**:
- Pre-mutation user reports generated for all modes (dry-run, clone, migrate)
- Interactive confirmation prompts (double confirmation for clean-only)
- Full audit trail enables manual recovery (see Recovery section below)

## Resolved from v1

| v1 Limitation | v2 Resolution |
|--------------|---------------|
| Custom views not migrated | Ownership transferred via `PUT /customviews/{id}` + default user status migrated |
| Data alert ownership not transferable | Ownership transferred via `PUT /dataAlerts/{id}` + new user added as recipient |
| No resume/checkpoint — interruptions restart from beginning | Per-user, per-step checkpoints. Resume with `--resume-latest`. |
| No rate limiting — 429 errors caused unpredictable failures | `Retry-After` header respected. `RateLimiter` with configurable RPS. |
| PAT-only auth — shared credentials, shorter sessions | JWT primary + PAT fallback. Connected App for service identity. |
| Redundant API calls — every user re-fetched all content lists | Dimension cache: O(content_types) warmup + O(mutations) per user. |
| No pre-mutation reporting for clone/migrate | All modes now generate per-user reports before any mutations. |
| Collections not handled | Clone-and-replace workflow: create new + add items + clone permissions + delete old. |

## Recovery Procedures

### Scenario 1: Run Interrupted (Ctrl+C, network drop, crash)

1. Checkpoints are saved after each user completes
2. Resume:
   ```bash
   python -m src.main --resume-latest
   ```
3. Completed users are skipped. Partially-completed users resume from last completed step.

### Scenario 2: Users Failed During Execution

1. Check audit log for failure details:
   ```bash
   # Find failures in the JSONL log
   grep '"result":"FAILURE"' audit/migrate_run_*/audit_log.jsonl
   ```
2. Check checkpoint file for failed users:
   ```bash
   grep '"status":"failed"' audit/checkpoints/checkpoint_*.json
   ```
3. Fix the underlying issue (permissions, user not found, etc.)
4. Resume — failed users are automatically retried (up to 3 attempts; after that they are permanently skipped):
   ```bash
   python -m src.main --resume-latest
   ```

### Scenario 3: Need to Reverse a Migration

There is no automated rollback. Manual recovery:

1. **Run a post-migration dry-run with comparison** to understand current state:
   ```bash
   python -m src.main --mode dry-run --compare-latest
   ```
2. **Review `comparison_report.json`** — identifies which users still have residual access
3. **Review pre-mutation reports** — `user_reports_migrate/` contains full state before changes
2. **Re-create old user** (if deactivated):
   - Create a new CSV: `old_username=new_username, new_username=old_username`
   - Run clone mode to recreate the old user with permissions
3. **Permissions**: The audit log and user reports contain every permission grant. Use this to manually restore.
4. **Ownership**: Ownership transfers are logged. Reverse them with a targeted script or manual UI changes.
5. **Groups**: Group memberships are logged. Re-add users to groups via UI or API.
6. **Collections**: Old collections were deleted during clone-and-replace. The pre-mutation report lists all items — recreate manually.

### Scenario 4: Dimension Cache Is Stale

If content was added/removed between cache warmup and execution:
1. Delete the cache file: `rm audit/cache/dimension_cache.json`
2. Re-run — cache will be rebuilt from scratch
3. Or use `--force-refresh` to force a cache rebuild
4. Or set `DIMENSION_CACHE_ENABLED=false` to disable caching entirely

### Scenario 5: Auth Token Expires Mid-Run

The tool handles this automatically:
- Proactive refresh when token is within `TOKEN_REFRESH_THRESHOLD_SECONDS` of expiry
- On 401/403: re-authenticate (JWT first, then PAT fallback)
- All in-flight requests are retried after re-auth

### Scenario 6: Rate Limited by Tableau Cloud

The tool handles this automatically:
- 429 responses: reads `Retry-After` header and waits exactly that many seconds
- If no `Retry-After` header: exponential backoff with jitter
- Configurable via `RATE_LIMIT_RPS` and `MAX_RETRIES`

### Scenario 7: Alert Recipient Addition Fails Transiently

The alert service has its own retry logic:
- Up to 3 attempts with exponential backoff (1s, 2s, 4s) for the recipient addition step
- Only retries on 5xx errors (transient server issues)
- 409 conflicts treated as success (idempotent)
- Each retry logged to the audit trail
- Ownership transfer via PUT happens after successful recipient addition

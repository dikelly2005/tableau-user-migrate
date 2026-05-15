# Tableau Cloud User Migrate Tool v2 — Troubleshooting

## How the Checkpoint System Works

Every workflow (clone, migrate, clean-only) tracks progress at **two levels**:

1. **User level** — each user mapping is marked `pending → in_progress → completed | failed`
2. **Step level** — within each user, individual operations are tracked (e.g., `create_user`, `clone_permissions`)

Checkpoints are saved to JSON after **every state change**, so even a hard crash loses at most one in-flight API call.

### Checkpoint File Location

```
audit/checkpoints/checkpoint_YYYYMMDD_HHMMSS.json
```

### Checkpoint File Structure

```json
{
  "run_id": "20260413_120000",
  "mode": "migrate",
  "updated_at": "2026-04-13T12:05:23.456789+00:00",
  "checkpoints": [
    {
      "old_username": "alice@old.com",
      "new_username": "alice@new.com",
      "status": "completed",
      "steps_completed": ["create_user", "clone_permissions", "clone_groups", ...],
      "started_at": "2026-04-13T12:00:01+00:00",
      "updated_at": "2026-04-13T12:02:15+00:00",
      "error": null
    },
    {
      "old_username": "bob@old.com",
      "new_username": "bob@new.com",
      "status": "failed",
      "steps_completed": ["create_user", "clone_permissions"],
      "started_at": "2026-04-13T12:02:16+00:00",
      "updated_at": "2026-04-13T12:03:45+00:00",
      "error": "API error 500: Internal server error"
    },
    {
      "old_username": "charlie@old.com",
      "new_username": "charlie@new.com",
      "status": "pending",
      "steps_completed": [],
      "started_at": null,
      "updated_at": null,
      "error": null
    }
  ]
}
```

---

## Resume Commands

### Resume the most recent incomplete run

```bash
python -m src.main --resume-latest
```

Finds the newest `checkpoint_*.json` in `audit/checkpoints/` that has any users with status `pending`, `in_progress`, or `failed`. Skips fully completed users, retries everything else.

### Resume a specific checkpoint file

```bash
python -m src.main --resume audit/checkpoints/checkpoint_20260413_120000.json
```

Use this when you have multiple checkpoint files and want to target a specific run.

### What happens on resume

| User Status in Checkpoint | Behavior on Resume |
|--------------------------|-------------------|
| `completed` | Skipped entirely — no API calls |
| `pending` | Processed from the beginning |
| `in_progress` | Resumes from last completed step |
| `failed` | Retried from last completed step |

For `in_progress` and `failed` users, the step-level tracking ensures we don't repeat work:

```
Example: bob@old.com failed after clone_permissions

Steps already done (skipped on resume):
  ✓ create_user
  ✓ clone_permissions

Steps that will execute on resume:
  → clone_groups
  → transfer_ownership
  → clone_favorites
  → clone_subscriptions
  → clone_alerts
  → clone_custom_views
  → remove_permissions
  → remove_groups
  → remove_favorites
  → remove_subscriptions
  → remove_alerts
  → deactivate
```

---

## Workflow Step Reference

### Migrate Workflow (16 steps per user)

| # | Step Name | Operation | Reversible? |
|---|-----------|-----------|:-----------:|
| 1 | `create_user` | Create new user (or reuse existing) | Yes — deactivate new user |
| 2 | `clone_permissions` | Copy all explicit + default permissions to new user | Yes — remove from new user |
| 3 | `clone_groups` | Add new user to same groups | Yes — remove from groups |
| 4 | `transfer_ownership` | Change owner on workbooks/datasources/flows/projects/VCs | Yes — transfer back |
| 5 | `clone_favorites` | Copy favorites to new user | Yes — remove favorites |
| 6 | `clone_subscriptions` | Create matching subscriptions for new user | Yes — delete subscriptions |
| 7 | `clone_alerts` | Add new user to data alerts + transfer ownership | Yes — remove from alerts |
| 8 | `clone_custom_views` | Transfer ownership + migrate default user status | Yes — transfer back |
| 9 | `clone_collections` | Clone-and-replace (new collection + items + perms) | Old collection deleted |
| 10 | `clone_pulse_subscriptions` | Clone Pulse subscriptions to new user | Yes — delete |
| 11 | `clone_pulse_alerts` | Transfer Pulse alert ownership to new user | Yes — transfer back |
| 12 | `clone_webhooks` | Transfer webhook ownership to new user | Yes — transfer back |
| 13 | `remove_permissions` | Strip old user's explicit + default permissions | Requires re-grant |
| 14 | `remove_groups` | Remove old user from groups | Requires re-add |
| 15 | `remove_favorites` | Delete old user's favorites | Requires re-add |
| 16 | `remove_subscriptions` | Delete old user's subscriptions | Requires re-create |
| 17 | `remove_alerts` | Remove old user from alerts | Requires re-add |
| 18 | `deactivate` | Set old user to Unlicensed | Re-license manually |

### Clone Workflow (11 steps per user)

| # | Step Name | Operation |
|---|-----------|-----------|
| 1 | `create_user` | Create new user |
| 2 | `clone_permissions` | Copy explicit + default permissions |
| 3 | `clone_groups` | Copy group memberships |
| 4 | `clone_favorites` | Copy favorites |
| 5 | `clone_subscriptions` | Copy subscriptions |
| 6 | `clone_alerts` | Copy alert memberships + transfer ownership |
| 7 | `clone_custom_views` | Transfer ownership + migrate default user status |
| 8 | `clone_collections` | Clone-and-replace (new collection + items + perms) |
| 9 | `clone_pulse_subscriptions` | Clone Pulse subscriptions |
| 10 | `clone_pulse_alerts` | Transfer Pulse alert ownership |
| 11 | `clone_webhooks` | Transfer webhook ownership |

### Cleanup Workflow (10 steps per user)

| # | Step Name | Operation |
|---|-----------|-----------|
| 1 | `remove_permissions` | Strip permissions |
| 2 | `remove_groups` | Remove group memberships |
| 3 | `remove_favorites` | Delete favorites |
| 4 | `remove_subscriptions` | Delete subscriptions |
| 5 | `remove_alerts` | Remove from alerts |
| 6 | `remove_custom_views` | Delete custom views |
| 7 | `remove_pulse_subscriptions` | Remove Pulse subscriptions |
| 8 | `remove_pulse_alerts` | Remove Pulse alerts |
| 9 | `remove_webhooks` | Remove webhooks |
| 10 | `deactivate` | Unlicense user |

---

## Common Scenarios

### Scenario 1: Run crashed mid-batch (Ctrl+C, OOM, network)

**Symptom**: Process exited unexpectedly. Some users completed, some didn't.

**Fix**:
```bash
python -m src.main --resume-latest
```

The tool picks up exactly where it left off. Completed users are skipped. The user that was in-progress resumes from its last completed step.

### Scenario 2: One user fails, rest succeed

**Symptom**: Terminal shows `[WARN] 1 users failed — check audit log`. Run completed for all other users.

**Diagnose**:
```bash
# Find the failure in the audit log
grep '"result":"FAILURE"' audit/migrate_run_*/audit_log.jsonl

# Or check the checkpoint directly
cat audit/checkpoints/checkpoint_*.json | python -m json.tool | grep -A5 '"failed"'
```

**Fix**: Resolve the root cause (user not found, permission denied, etc.), then:
```bash
python -m src.main --resume-latest
```

Only the failed user is retried. Everyone else stays completed.

### Scenario 3: User stuck at `transfer_ownership` step

**Symptom**: Checkpoint shows user with `steps_completed: ["create_user", "clone_permissions", "clone_groups"]` and status `failed` with an ownership transfer error.

**Diagnose**: This usually means:
- The content type isn't supported for ownership transfer
- The new user doesn't have the required site role
- The content was deleted between cache warmup and transfer

**Fix**:
1. Check if the new user exists and has the right role
2. If dimension cache is stale, delete it: `rm audit/cache/dimension_cache.json`
3. Resume:
   ```bash
   python -m src.main --resume-latest
   ```
   The tool skips `create_user`, `clone_permissions`, and `clone_groups` (already done) and retries from `transfer_ownership`.

### Scenario 4: Permissions cloned but old user NOT deactivated yet

**Symptom**: Run interrupted after step 9 (`clone_collections`) but before step 13 (`remove_permissions`). Both users now have the same access.

**Impact**: Low risk — both users have access. No data loss. The old user simply hasn't been cleaned up yet.

**Fix**:
```bash
python -m src.main --resume-latest
```

Steps 1-9 are skipped (already completed). Execution resumes at step 13 (`remove_permissions`) and continues through deactivation.

### Scenario 11: Verifying migration outcomes with comparison

**Symptom**: Migration completed, need to verify all old users were properly cleaned up.

**Fix**: Run a new dry-run and compare:
```bash
python -m src.main --mode dry-run --compare-latest
```

Review `comparison_report.json`:
- `fully_migrated` — old user's counts zeroed (expected)
- `anomaly` — counts increased unexpectedly (investigate)
- `unchanged` — nothing changed (may indicate the user wasn't processed)

### Scenario 5: Want to resume but with a different mode

**Not supported.** The checkpoint file stores the original mode. If you need to change modes:

1. Complete or abandon the current checkpoint
2. Start a fresh run with the new mode:
   ```bash
   python -m src.main --mode clone    # New run, new checkpoint
   ```

### Scenario 6: Checkpoint file is corrupted

**Symptom**: `--resume-latest` fails with a JSON parse error.

**Fix**:
1. Check if there's an earlier valid checkpoint: `ls -la audit/checkpoints/`
2. Resume from that one: `python -m src.main --resume audit/checkpoints/checkpoint_<earlier>.json`
3. Or start fresh — the tool is idempotent:
   - Already-created users will be reused (not duplicated)
   - Already-granted permissions will be skipped (409 handled)
   - Ownership that was already transferred stays transferred

### Scenario 7: Want to skip a specific user

**Method**: Edit the checkpoint JSON directly.

1. Open the checkpoint file
2. Find the user entry
3. Change `"status": "failed"` to `"status": "completed"`
4. Save and resume:
   ```bash
   python -m src.main --resume audit/checkpoints/checkpoint_YYYYMMDD.json
   ```

The tool treats it as already done and moves on.

### Scenario 8: Dimension cache causing stale data issues

**Symptom**: Permissions or ownership operations reference content that was recently added/deleted.

**Fix options**:

```bash
# Option A: Delete cache and resume (cache rebuilds on next run)
rm audit/cache/dimension_cache.json
python -m src.main --resume-latest

# Option B: Disable cache entirely for this run
DIMENSION_CACHE_ENABLED=false python -m src.main --resume-latest
```

### Scenario 9: Rate limiting causing excessive retries

**Symptom**: Terminal full of `[RETRY] 429 rate limited` messages. Run is slow.

**Fix**: Lower the request rate:
```bash
# In .env
RATE_LIMIT_RPS=5       # Down from default 10
API_DELAY_MS=200       # Add more delay between calls
```

Or set inline for a single run:
```bash
RATE_LIMIT_RPS=5 python -m src.main --resume-latest
```

### Scenario 10: Auth keeps failing on resume

**Symptom**: `[AUTH] JWT auth failed, falling back to PAT` followed by `PAT auth failed`.

**Diagnose**:
- JWT: Connected App secret may have expired or been rotated
- PAT: Token may have expired (Tableau Cloud PATs have configurable expiry)

**Fix**:
1. Generate new credentials in Tableau Cloud
2. Update `.env` with new values
3. Resume — auth happens fresh on every run start:
   ```bash
   python -m src.main --resume-latest
   ```

---

## Inspecting Checkpoint State

### Quick summary

```bash
cat audit/checkpoints/checkpoint_*.json | python3 -c "
import sys, json
data = json.load(sys.stdin)
statuses = {}
for cp in data['checkpoints']:
    s = cp['status']
    statuses[s] = statuses.get(s, 0) + 1
print(f'Mode: {data[\"mode\"]}  Run: {data[\"run_id\"]}')
for s, c in sorted(statuses.items()):
    print(f'  {s}: {c}')
"
```

### List failed users with errors

```bash
cat audit/checkpoints/checkpoint_*.json | python3 -c "
import sys, json
data = json.load(sys.stdin)
for cp in data['checkpoints']:
    if cp['status'] == 'failed':
        print(f'{cp[\"old_username\"]} -> {cp[\"new_username\"]}')
        print(f'  Error: {cp[\"error\"]}')
        print(f'  Steps done: {cp[\"steps_completed\"]}')
        print()
"
```

### List users still pending

```bash
cat audit/checkpoints/checkpoint_*.json | python3 -c "
import sys, json
data = json.load(sys.stdin)
pending = [cp for cp in data['checkpoints'] if cp['status'] in ('pending', 'in_progress', 'failed')]
print(f'{len(pending)} users remaining:')
for cp in pending:
    print(f'  [{cp[\"status\"]}] {cp[\"old_username\"]} -> {cp[\"new_username\"]}')
"
```

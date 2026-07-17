# Rollback Procedures

Step-by-step instructions for reverting failed or unwanted migrations using the audit trail and checkpoint system.

---

## When to Rollback

Rollback is appropriate when:
- Verification (Phase 9) reveals widespread failures across multiple users
- A migration was executed against the wrong batch of users
- Business requirements changed after migration completed
- Content access is broken in ways that can't be resolved by re-running

Rollback is **not needed** when:
- A few users failed verification → They were automatically blocked from cleanup. Fix and `--resume-latest`.
- The run was interrupted mid-execution → `--resume-latest` picks up where it left off.

---

## Prerequisites

Before rolling back, confirm:
- [ ] You have the audit log file: `audit/migrate_run_<id>/audit_log.jsonl`
- [ ] The Tableau Cloud site is accessible
- [ ] Your `.env` credentials are valid (JWT or PAT)
- [ ] You have Site Administrator access

---

## Rollback Commands

### Standard Rollback (re-activate old users + reverse ownership)

```bash
python -m src.main --rollback audit/migrate_run_<timestamp>/audit_log.jsonl
```

This will:
1. Re-activate old users (set siteRole back from Unlicensed to their original role)
2. Transfer ownership back to old users (reverses every successful ownership transfer)
3. Remove new users from groups they were added to

### Full Rollback (also deactivate new users)

```bash
python -m src.main --rollback audit/migrate_run_<timestamp>/audit_log.jsonl --rollback-delete-users
```

Adds:
4. Deactivate (Unlicense) all newly created users from this run

---

## What Rollback Reverses

| Artifact | Reversed? | Method |
|----------|-----------|--------|
| Ownership transfers | ✅ Yes | PUT with old owner ID |
| User deactivation | ✅ Yes | Re-license with original siteRole |
| Group memberships (new user) | ✅ Yes | Remove new user from groups |
| New user accounts | ✅ Optional | Deactivate (with `--rollback-delete-users`) |
| Permissions (new user) | ❌ No | Must be manually removed or will be orphaned on deactivation |
| Favorites (new user) | ❌ No | Harmless — orphaned on user deactivation |
| Subscriptions (new user) | ❌ No | Will fail delivery after deactivation |
| Alerts (new user) | ❌ No | Ownership was transferred — rollback reverses this |
| Custom view ownership | ✅ Yes | Reversed via ownership transfer |
| Collection ownership | ✅ Yes | Reversed via ownership transfer |
| Personal Space relocation | ❌ No | Content stays in "User Migration Artifacts" project — must be manually moved back |

---

## Step-by-Step Rollback Process

### 1. Identify the Run to Rollback

```bash
ls audit/migrate_run_*
```

Each directory is named with a timestamp. Find the run you want to revert.

### 2. Review What Was Done

```bash
# Count successful mutations
grep -c '"result":"success"' audit/migrate_run_<id>/audit_log.jsonl

# See which users were affected
grep '"action":"user_deactivate"' audit/migrate_run_<id>/audit_log.jsonl | python -m json.tool

# Check verification results
cat audit/migrate_run_<id>/verification_results.json | python -m json.tool
```

### 3. Execute Rollback

```bash
python -m src.main --rollback audit/migrate_run_<id>/audit_log.jsonl
```

Monitor output for errors. The rollback logs its own actions to the current run's audit trail.

### 4. Verify Rollback Success

```bash
# Run a dry-run to check old users' state
python -m src.main --mode dry-run
```

Confirm old users have their content, permissions, and groups restored.

### 5. Handle Remaining Artifacts (if needed)

If you used `--rollback-delete-users`, the new users are deactivated and their cloned permissions/subscriptions become inert. No further action needed.

If you did **not** deactivate new users, they still have cloned access. Either:
- Run `python -m src.main --mode clean-only` with a CSV of the new usernames to strip their access
- Or deactivate them: `python -m src.main --rollback <log> --rollback-delete-users`

---

## Partial Rollback (Specific Users)

The rollback workflow processes all successful events in the audit log. To rollback specific users only:

1. Copy the audit log
2. Filter to only events for target users:
   ```bash
   grep '"old_username":"target@domain.com"' audit/migrate_run_<id>/audit_log.jsonl > /tmp/filtered_audit.jsonl
   ```
3. Run rollback against the filtered log:
   ```bash
   python -m src.main --rollback /tmp/filtered_audit.jsonl
   ```

---

## Using Checkpoints for Resume (Not Rollback)

If a migration failed mid-run but you want to **continue** (not revert):

```bash
# Resume from latest checkpoint
python -m src.main --resume-latest

# Resume from specific checkpoint file
python -m src.main --resume audit/checkpoints/<file>.json
```

The checkpoint system tracks:
- Which users have been processed
- Which steps completed per user (create_user, clone_permissions, clone_groups, relocate_personal_space, transfer_ownership, etc.)
- Which users failed and why

On resume, completed steps are skipped. Failed users are retried. All operations are idempotent (409/conflict = success).

---

## Rollback Limitations

1. **Personal Space content relocation cannot be auto-reversed** — Workbooks/datasources moved to "User Migration Artifacts" stay there. You must manually move them back via Tableau Cloud UI or a targeted PUT call.

2. **Subscriptions created for new user persist** — They'll fail delivery after the new user is deactivated, but won't auto-delete. Clean up via Tableau Cloud UI or a clean-only run.

3. **Audit log is the source of truth** — If the audit log is corrupted or incomplete, rollback will be incomplete. Always preserve audit output directories.

4. **Rollback does not restore deleted collections** — If the original clone-and-replace pattern was used (old code path), the old collection was deleted. The new collection (owned by new user) will have its ownership reversed, but the original collection LUID is gone.

5. **IdP/SAML changes are out of scope** — If you updated SAML assertions in your IdP alongside the migration, you must revert those manually.

---

## Emergency Recovery (No Audit Log)

If you've lost the audit log but have the pre-mutation user reports:

1. Open `audit/migrate_run_<id>/user_reports_migrate/<username>.json`
2. This contains the complete pre-mutation state: owned content, permissions, groups, favorites, subscriptions, alerts, custom views, collections
3. Manually restore each dimension using the Tableau Cloud REST API or UI:
   - Re-license the old user
   - Transfer each owned item back (`PUT` with `<owner id="old_user_id"/>`)
   - Re-add to each group listed in the report
   - Re-grant each permission listed

This is labor-intensive but the reports provide a complete blueprint of what needs restoring.

# Optimal Execution Flow

## Phase 0: Pre-Flight

1. **Load configuration** — `.env` → Settings dataclass validation
2. **Validate CSV mappings** — Parse, deduplicate, normalize to lowercase, reject malformed rows

## Phase 1: Discovery

3. **Load endpoints.yaml** — Single source of truth for all endpoint metadata
4. **Load scopes.yaml** — Builds scopes used for JWT authentication

## Phase 2: Authentication

> **🔄 Token refresh trigger** — Initial token acquisition. Store expiry timestamp for proactive refresh checks.

5. **Authenticate (JWT primary, PAT fallback)** — Obtain token, store expiry for proactive refresh
6. **Negotiate API version** — `GET /api/3.19/serverinfo` → extract `restApiVersion`

## Phase 3: Cache Warmup

> **🔄 Token refresh trigger** — Check token expiry before starting bulk fetches. Refresh if within threshold.

7. **Primary cache warmup (parallel)** — Fetch all `cache: true` endpoints concurrently (users, groups, projects, workbooks, views, datasources, flows, VCs, collections, subscriptions, alerts, custom views, etc.)
   - *These are independent list calls — run all in parallel, respecting rate limits*
8. **Enrichment passes (sequential, depends on #7)** — Collections owner resolution (username→LUID from cached users), VC revisions (per-VC GET for owner data)
   - *Collections enrichment requires users cache from step 7*
   - *VC enrichment requires VCs cache from step 7*
9. **Child endpoint warmup (parallel per parent, depends on #7)** — group_users, user_favorites
   - *Requires parent IDs from step 7, but individual child fetches are parallelizable*

## Phase 4: Analysis & Planning

10. **Generate per-user impact reports (baseline)** — All reads from cache, zero API calls. Produces per-user JSON with owned content, permissions, groups, favorites, subscriptions, alerts, custom views, collections
11. **Classify users by complexity tier** — very_high → high → moderate → low, sorted by recent activity
12. **Checkpoint initialization** — Create new checkpoint state

## Phase 5: User Creation

> **🔄 Token refresh trigger** — Check token expiry before mutation phase begins.

13. **Create new user accounts (sequential per user)** — `POST /sites/{site_id}/users`
    - **⏸ WAIT 3–5 seconds after each user creation** — Tableau Cloud needs propagation time before the new user ID is valid for permission/ownership assignment. Without this, subsequent PUT/POST calls may return 404 or 400.

## Phase 6: Access Cloning (parallelizable across dimensions, sequential per user)

> **🔄 Token refresh trigger** — Check token expiry before starting per-user cloning loop.

14. **Clone explicit permissions** — `PUT` per content item, ignore if inherited from project
15. **Clone default permissions** — `PUT` per project
    - *Steps 14–15 can run in parallel with each other per user*
16. **Clone group memberships** — `POST` per group
    - *Can run in parallel with 14–15 (independent API surface)*

## Phase 6.5: Relocate Personal Space Content

> **🔄 Token refresh trigger** — Check token expiry before relocation.

16b. **Ensure "User Migration Artifacts" project exists** — Check cache for top-level project by name. If not found, `POST /sites/{site_id}/projects` to create it. Idempotent.
16c. **Move Personal Space workbooks, datasources, flows** — For each user, find owned content with no project association (Personal Space). `PUT` each item with `<project id="{migration_project_id}"/>` to relocate.
    - *Must happen before ownership transfer* — Once ownership changes, content in Personal Space becomes inaccessible to both old and new user.
    - *Configurable via `MIGRATION_ARTIFACTS_PROJECT` env var (default: "User Migration Artifacts")*

## Phase 7: Ownership Transfer (sequential, depends on #13 propagation)

> **🔄 Token refresh trigger** — Check token expiry before each content type batch. Ownership transfers are slow and high-stakes.

17. **Transfer ownership of content** — `PUT` per workbook, datasource, flow, project, VC
    - **⏸ WAIT 2–3 seconds between ownership transfers on the same content type** — Rapid ownership changes on the same project tree can cause conflict errors (409)
    - *Different content types (workbooks vs. datasources vs. flows) can transfer in parallel*
18. **Transfer ownership of collections** — `POST` per collection via `batch_update_collections` with `ownerLuid` in array
    - **⏸ WAIT 2–3 seconds between batch calls** — Rapid ownership changes can cause conflict errors (409)
    - *Collections can have ownership updated in batch by passing ownerLuid*
19. **Transfer custom view ownership** — `PUT` per custom view + migrate default user status
    - **⏸ WAIT 1–2 seconds after ownership PUT before setting default users** — The custom view must fully reflect new owner before default user POST succeeds

## Phase 8: Artifact Cloning (depends on new user existing, parallelizable)

> **🔄 Token refresh trigger** — Check token expiry before artifact cloning loop.

20. **Clone subscriptions** — `POST` per subscription (creates new, referencing new user)
    - *Independent of ownership — can run in parallel with Phase 7*
21. **Clone alerts** — `POST` to add new user as recipient, then `PUT` to transfer ownership + retry with backoff
    - **⏸ WAIT 2 seconds between adding recipient and transferring ownership on the same alert** — Recipient must be confirmed before ownership transfer succeeds
22. **Clone favorites** — `POST` per favorite
    - *Can run in parallel with 20–21*
23. **Clone pulse subscriptions** — Clone metric subscriptions to new user
24. **Clone pulse alerts** — Clone metric alert configurations to new user
25. **Clone webhooks** — Clone webhook registrations to new user

## Phase 9: Verification (gates cleanup)

> **🔄 Token refresh trigger** — Check token expiry before re-fetching state from API.

26. **Refresh cache from API** — `cache.refresh()` + full re-warmup. Clears all cached dimensions and re-fetches from Tableau Cloud. Required because inline cache invalidations may not reflect silent API failures.

27. **Comprehensive per-user verification across all dimensions:**

| # | Dimension | Pass condition | Fail condition |
|---|-----------|---------------|----------------|
| 1 | **Site role & license** | New user's siteRole matches old user's original role | Mismatch detected |
| 2 | **Owned content (old user)** | Zero remaining workbooks/datasources/flows/projects/VCs | Old user still owns items |
| 3 | **Owned content (new user)** | New user owns transferred items | *(counted, no fail trigger)* |
| 4 | **Collections** | Old user owns zero collections | Old user still owns collections |
| 5 | **Custom views** | Old user owns zero custom views | Old user still owns custom views |
| 6 | **Group memberships** | New user has ≥ old user's group count | Zero groups or count mismatch |
| 7 | **Permissions** | New user has permissions (≥50% of old user's count) | Zero permissions or significantly fewer |
| 8 | **Favorites** | New user has favorites (if old user had any) | Zero favorites when expected |
| 9 | **Subscriptions** | New user has subscriptions (if old user had any) | Zero subscriptions when expected |
| 10 | **Alerts** | New user owns alerts (if old user owned any) | Zero alert ownership when expected |
| 11 | **Pulse subscriptions** | New user has pulse subs (if old user had any) | Zero pulse subs when expected |
| 12 | **Pulse alerts** | New user has pulse alerts (if old user had any) | Zero pulse alerts when expected |
| 13 | **Webhooks** | New user has webhooks (if old user had any) | Zero webhooks when expected |

28. **Classify users by migration outcome:**
    - **Pass** — All dimension checks pass. Full counts written to `verification_results.json`. Cleanup proceeds.
    - **Fail** — One or more issues detected. Issue codes + counts logged. Cleanup blocked. Requires manual review before re-running with `--resume-latest`.

**Output:** `audit/migrate_run_<id>/verification_results.json` containing:
```json
{
  "results": { "user@old.com": "pass", "other@old.com": "fail" },
  "details": {
    "user@old.com": { "new_user_owned_content": 12, "new_user_permissions": 45, ... },
    "other@old.com": { "issues": ["old_user_still_owns_3_items"], ... }
  }
}
```

## Phase 10: Cleanup (sequential per user, only "Pass" users)

> **🔄 Token refresh trigger** — Check token expiry before cleanup loop. This is destructive — a failed token mid-cleanup leaves partial state.

27. **Remove explicit permissions from old user** — `DELETE` per permission
28. **Remove default permissions from old user** — `DELETE` per permission
29. **Remove group memberships** — `DELETE` per group
30. **Remove favorites** — `DELETE` per favorite
31. **Remove subscriptions** — `DELETE` per subscription
32. **Remove alerts** — `DELETE` per alert recipient
33. **Remove custom view default user associations** — `DELETE /sites/{site_id}/customviews/{cv_id}/default/users/{old_user_id}` per custom view where old user was a default user
34. **Remove pulse subscriptions** — Remove metric subscriptions from old user
35. **Remove pulse alerts** — Remove metric alerts from old user
36. **Remove webhooks** — Remove webhook registrations from old user
    - *Steps 27–36 can all run in parallel (independent DELETE calls on different resources)*
37. **Deactivate old user** — `PUT` siteRole=Unlicensed
    - **⏸ This MUST be last** — Once unlicensed, the old user's context is gone. Any missed cleanup before this point requires re-licensing to fix.

## Phase 11: Final Audit

38. **Write final audit trail** — JSONL log, impact summary with report file references, checkpoint summary
39. **Output per-user migration status** — Pass/Fail classification, cleanup actions taken, any remaining "Fail" users requiring manual intervention

---

## Mode-Specific Phase Mapping

Not all modes execute all phases:

| Phase | `dry-run` | `clone` | `migrate` | `clean-only` |
|-------|-----------|---------|-----------|--------------|
| 0: Pre-Flight | ✅ | ✅ | ✅ | ✅ |
| 1: Discovery | ✅ | ✅ | ✅ | ✅ |
| 2: Authentication | ✅ | ✅ | ✅ | ✅ |
| 3: Cache Warmup | ✅ | ✅ | ✅ | ✅ |
| 4: Analysis & Reports | ✅ | ✅ | ✅ | ✅ (baseline) |
| 5: User Creation | — | ✅ | ✅ | — |
| 6: Access Cloning | — | ✅ | ✅ | — |
| 6.5: Personal Space Relocation | — | — | ✅ | ✅ |
| 7: Ownership Transfer | — | — | ✅ | — |
| 8: Artifact Cloning | — | ✅ | ✅ | — |
| 9: Verification | — | — | ✅ (new user has all) | ✅ (old user has zero) |
| 10: Cleanup | — | — | ✅ (pass only) | ✅ (all users) |
| 11: Final Audit | ✅ | ✅ | ✅ | ✅ |

---

## Parallelism Summary

| Steps | Can run in parallel? | Notes |
|-------|---------------------|-------|
| 7 (primary warmup endpoints) | Yes, all of them | Rate-limit bounded only |
| 8a + 8b (enrichments) | Yes, with each other | Both depend on #7 |
| 9 (child endpoints) | Yes, across parents | Depends on #7 |
| 14 + 15 + 16 | Yes | Independent API surfaces |
| 17 (ownership by type) | Yes across types | Sequential within same type |
| 20–25 (artifact cloning) | Partially | 20, 22–25 independent; 21 has internal waits |
| 29–36 (cleanup) | Yes, all of them | All independent DELETEs |

## Required Waits Summary

| After | Wait | Reason |
|-------|------|--------|
| User creation (#13) | 3–5s | New user ID propagation |
| Ownership transfer same type (#17) | 2–3s | Avoid 409 conflicts |
| Collection batch ownership (#18) | 2–3s | Avoid 409 conflicts |
| Custom view ownership PUT (#19) | 1–2s | Owner must propagate before default user POST |
| Alert recipient add → ownership (#21) | 2s | Recipient must exist before ownership transfer |
| All cleanup → deactivate (#37) | 0s but strict ordering | Point of no return |

## Token Refresh Strategy

For long-running batch migrations, JWT tokens (typically 10-minute expiry) will not survive the full workflow. The token refresh strategy:

- **Check before each phase boundary** — Compare current time against stored expiry minus `TOKEN_REFRESH_THRESHOLD_SECONDS` (default: 300s)
- **Check within per-user loops** — For batches with many users, check before each user's mutation sequence begins
- **Never refresh mid-operation** — Complete the current atomic operation (e.g., a single ownership PUT) before refreshing. Refreshing mid-sequence is safe at step boundaries.
- **On refresh failure** — Halt gracefully, checkpoint current state. Resume via `--resume-latest` after re-authentication.

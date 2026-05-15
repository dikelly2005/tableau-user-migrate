# Pre-Migration Checklist — Credential Re-Authentication

## Overview

After migration, content that uses **embedded credentials** (database passwords, OAuth tokens, service accounts) will fail on the next scheduled extract refresh or live connection query. The credentials are tied to the original user's credential store and do not transfer with ownership.

This checklist is generated from the dry-run `impact_summary.json` and identifies every content owner who will need to take action post-migration.

## How to Generate the Checklist

```bash
python -m src.main --mode dry-run
```

Then inspect `audit/migrate_run_<timestamp>/impact_summary.json`. Each user entry includes:

```json
{
  "old_username": "jane.doe@old.com",
  "new_username": "jane.doe@new.com",
  "content_needing_credential_reauth": []
}
```

## What Content Owners Need to Do

### Extract Refreshes (Workbooks and Datasources)

After migration, the new user must:
1. Open the workbook or datasource in Tableau Cloud
2. Go to the data connection
3. Click **Edit Connection**
4. Re-enter the database credentials (username/password, OAuth, etc.)
5. Verify the next scheduled extract refresh succeeds

### Flows

After migration, the new user must:
1. Open the flow in Tableau Cloud
2. Go to **Connections**
3. Re-authenticate each input/output connection
4. Run the flow manually once to verify
5. Confirm the next scheduled run succeeds

### OAuth Connections

OAuth tokens (Google Sheets, Salesforce, Snowflake OAuth, etc.) are per-user. After migration:
1. New user must re-authorize the OAuth connection
2. This typically requires clicking "Sign In" on the connection
3. Previous user's OAuth token becomes invalid after deactivation

### Service Account Connections

If workbooks use a shared service account (not embedded per-user):
- No action needed — service account credentials are independent of user ownership
- Verify by checking if the connection uses "Prompt User" or "Embedded Password"

## Communication Template

Send this to affected content owners after dry-run review:

---

**Subject: Tableau Cloud Migration — Action Required for Your Content**

Hi {new_username},

Your Tableau Cloud account is being migrated from `{old_username}` to `{new_username}`.

**After migration, you will need to re-enter database credentials for the following content:**

| Content | Type | Schedule |
|---------|------|----------|
{table_rows}

**What to do:**
1. Log in with your new account: `{new_username}`
2. Open each item listed above
3. Edit the data connection and re-enter your credentials
4. Verify the next scheduled refresh succeeds

If you use OAuth connections (Google, Salesforce, etc.), you'll need to re-authorize by clicking "Sign In" on the connection.

**Deadline:** Please complete within 24 hours of migration to avoid missed data refreshes.

Questions? Contact your Tableau Cloud admin team.

---

## Items That Do NOT Need Re-Authentication

| Item | Why |
|------|-----|
| Permissions | Automatically cloned to new user |
| Group memberships | Automatically cloned to new user |
| Favorites | Automatically cloned to new user (POST per favorite) |
| Subscriptions | Automatically cloned to new user (new subscription created) |
| Alerts | Ownership transferred via PUT + new user added as recipient |
| Content ownership | Automatically transferred (workbooks, datasources, flows, projects, VCs) |
| Custom views | Ownership transferred via PUT + default user status migrated |
| Collections | Clone-and-replace: new collection created with same items and permissions |
| Live connections using "Prompt User" | Users enter credentials at query time — no stored credential |
| Tableau-native data (Hyper files) | No external credentials needed |

## Items That Cannot Be Migrated

| Item | Reason | Action |
|------|--------|--------|
| Personal Access Tokens | Security — tied to user identity | Users must create new PATs |
| Connected App tokens | Security — tied to user identity | Must be recreated |
| View history / activity log | Tied to immutable internal user ID | No action — historical data stays with old ID |
| SAML/OIDC claims | Outside Tableau — managed by IdP | Coordinate with IdP team separately |
| Collection URLs/bookmarks | Clone-and-replace gives collection a new LUID | Update any external links to collections |

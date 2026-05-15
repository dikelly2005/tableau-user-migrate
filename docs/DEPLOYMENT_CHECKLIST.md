# Tableau Cloud User Migrate Tool v2 — Deployment Checklist

## Pre-Deployment

- [ ] Python 3.10+ installed
- [ ] `pip install -r requirements.txt` completes without errors
- [ ] `.env` file created from `.env.example` (never committed to git)
- [ ] `.gitignore` includes `.env`, `audit/`, `output/`, `__pycache__/`

## Authentication

- [ ] **JWT (primary)**: Connected App created in Tableau Cloud Settings
  - [ ] `TABLEAU_CONNECTED_APP_CLIENT_ID` set
  - [ ] `TABLEAU_CONNECTED_APP_SECRET_ID` set
  - [ ] `TABLEAU_CONNECTED_APP_SECRET_VALUE` set
  - [ ] `TABLEAU_USERNAME` set (admin account to impersonate)
  - [ ] Connected App has required scopes enabled
- [ ] **PAT (fallback)**: Personal Access Token created
  - [ ] `TOKEN_NAME` set
  - [ ] `TOKEN_SECRET` set
  - [ ] PAT linked to Site Administrator account
  - [ ] PAT not expired

## Server Configuration

- [ ] `SERVER_URL` is correct (e.g., `https://your-site.online.tableau.com`)
- [ ] `SITE_NAME` matches the content URL of your site
- [ ] HTTPS connectivity from execution host to Tableau Cloud verified
- [ ] No proxy/firewall blocking REST API calls

## Data Preparation

- [ ] CSV file created at `CSV_LOCATION` path
- [ ] CSV has `old_username,new_username` header
- [ ] All usernames are valid email addresses (case-insensitive — tool normalizes to lowercase)
- [ ] No duplicate entries in either column
- [ ] No circular references (A→B, B→A)
- [ ] Old usernames exist on the Tableau Cloud site
- [ ] `python validate_setup.py` passes all checks

## Directory Structure

- [ ] `LOG_LOCATION` directory exists (or is creatable)
- [ ] `audit/` directory exists (tool creates run subdirectories)
- [ ] `output/` directory exists
- [ ] `audit/cache/` will be created for dimension cache
- [ ] `audit/checkpoints/` will be created for resumable state
- [ ] Sufficient disk space for audit logs (estimate: ~1KB per user per operation)

## Execution Plan

- [ ] API version negotiation working: tool prints `Negotiated API version: X.XX` at startup
- [ ] Dry-run executed first: `python -m src.main --mode dry-run`
- [ ] `impact_summary.json` reviewed — no unexpected errors
- [ ] Per-user reports reviewed in `user_reports_dry_run/`
- [ ] User count confirmed
- [ ] Permission count reasonable
- [ ] Owned content count verified
- [ ] `user_classification.json` reviewed — complexity tiers make sense
- [ ] `classification_csvs/` reviewed — per-tier CSVs ready for phased migration
- [ ] Collection warnings acknowledged (clone-and-replace will create new LUIDs)
- [ ] Custom view transfer warnings acknowledged
- [ ] Data alert transfer warnings reviewed (ownership transferred + recipient added)
- [ ] Credential re-authentication warnings acknowledged (PATs, OAuth, embedded passwords)
- [ ] Execution window scheduled (off-hours recommended)
- [ ] Stakeholders notified of migration window

## Rate Limiting

- [ ] `RATE_LIMIT_RPS` appropriate for site size (default: 10)
- [ ] `MAX_RETRIES` set (default: 3)
- [ ] `API_DELAY_MS` set if needed (default: 100)

## Post-Execution

- [ ] Audit log reviewed: `audit/migrate_run_*/audit_log.jsonl`
- [ ] **Post-migration dry-run comparison**: `python -m src.main --mode dry-run --compare-latest`
  - [ ] `comparison_report.json` reviewed
  - [ ] All old users show `fully_migrated` status (counts zeroed)
  - [ ] No `anomaly` users (unexpected count increases)
  - [ ] Any `unchanged` users investigated
- [ ] Pre-mutation reports archived: `user_reports_dry_run/`, `user_reports_clone/`, or `user_reports_migrate/`
- [ ] No FAILURE entries (or failures investigated)
- [ ] Sample users verified — can log in with new username
- [ ] Sample permissions verified — access matches expectations
- [ ] Content ownership spot-checked
- [ ] License count verified (clone mode adds licenses)
- [ ] Audit logs archived for compliance
- [ ] Checkpoint files cleaned up (or kept for reference)
- [ ] **Post-migration validation**: `python validate_migration.py` passes
  - [ ] All new users exist
  - [ ] All old users unlicensed
  - [ ] Old users own zero transferable content
  - [ ] Review `audit/validation_*/validation_summary.csv` for any FAIL/WARN rows
- [ ] Content owners notified to re-authenticate embedded credentials (see `docs/PRE_MIGRATION_CHECKLIST.md`)
- [ ] Content owners notified to recreate Personal Access Tokens
- [ ] External links to collections updated (new LUIDs after clone-and-replace)

## Rollback Awareness

- [ ] No automatic rollback — understand this before running migrate/clean-only
- [ ] For clone mode: old users still active, no rollback needed
- [ ] For migrate mode: old users deactivated — re-activation requires manual CSV + re-run
- [ ] Checkpoint files available for resume if interrupted

# Tableau Cloud User Migrate Tool v2 — Setup Guide

## Prerequisites

- Python 3.10+
- Tableau Cloud site with **Site Administrator** access
- **One of** the following auth methods:
  - **JWT (recommended)**: Tableau Connected App with direct trust
  - **PAT**: Personal Access Token with admin privileges

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Dependencies: `httpx`, `python-dotenv`, `PyJWT`, `cryptography`, `PyYAML`

### 2. Configure Authentication

#### Option A: JWT via Connected App (Primary — Recommended)

1. In Tableau Cloud: **Settings → Connected Apps → New Connected App**
2. Choose **Direct Trust**
3. Generate a secret — note the **Client ID**, **Secret ID**, and **Secret Value**
4. Set scopes: content read, users read/create, permissions read/update, groups read/update

Set in `.env`:
```
TABLEAU_CONNECTED_APP_CLIENT_ID=<client-id>
TABLEAU_CONNECTED_APP_SECRET_ID=<secret-id>
TABLEAU_CONNECTED_APP_SECRET_VALUE=<secret-value>
TABLEAU_USERNAME=admin@yourdomain.com
```

#### Option B: PAT (Fallback)

1. In Tableau Cloud: **My Account Settings → Personal Access Tokens**
2. Create token — note the **Token Name** and **Token Secret**

Set in `.env`:
```
TOKEN_NAME=your_pat_name
TOKEN_SECRET=your_pat_secret
```

#### Both (recommended for resilience)

Configure both JWT and PAT. The tool uses JWT as primary and automatically falls back to PAT on 401/403.

### 3. Create Environment File

```bash
cp .env.example .env
```

Edit `.env` with your values:
```bash
SERVER_URL=https://your-site.online.tableau.com
SITE_NAME=your-site-name
CSV_LOCATION=./data/user_mappings.csv
LOG_LOCATION=./audit
```

### 4. Create User Mappings CSV

```csv
old_username,new_username
jane.doe@old-domain.com,jane.doe@new-domain.com
john.smith@old-domain.com,john.smith@new-domain.com
```

Validation rules:
- Valid email format required for both columns
- **Usernames are case-insensitive** — normalized to lowercase at load time
- No duplicate old_username or new_username entries
- No circular references (A→B, B→A)
- Chain depth ≤ 5

### 5. Validate Setup

```bash
python validate_setup.py
```

This checks: env vars, auth credentials, CSV format, server connectivity.

## Usage

### Step 1: Always Start with Dry Run

```bash
python -m src.main --mode dry-run
```

Review `audit/migrate_run_<timestamp>/impact_summary.json` before proceeding.

Also review:
- `user_classification.json` — 4-tier complexity breakdown
- `classification_csvs/` — per-tier CSV mapping files for phased migration

### Step 2: Choose Workflow

| Mode | Command | Effect |
|------|---------|--------|
| Clone | `python -m src.main --mode clone` | Creates new users + copies access. Old users stay active. |
| Migrate | `python -m src.main --mode migrate` | Full migration: clone + transfer ownership + deactivate old users. |
| Clean-only | `python -m src.main --mode clean-only` | Strips access + deactivates old users. Run only after migration. |

### Resuming Failed Runs

```bash
python -m src.main --resume-latest           # Resume most recent incomplete run
python -m src.main --resume path/to/checkpoint.json  # Resume specific checkpoint
```

### Skip Confirmation Prompt

```bash
python -m src.main --mode migrate --yes
```

### Post-Migration Comparison

After migrating, run a new dry-run and compare against the baseline to verify:

```bash
python -m src.main --mode dry-run --compare-latest
```

Review `comparison_report.json` — all old users should show `fully_migrated` status.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Missing required environment variables" | Check `.env` has SERVER_URL, SITE_NAME, CSV_LOCATION, LOG_LOCATION |
| "At least one auth method required" | Set JWT or PAT credentials in `.env` |
| "JWT auth failed, falling back to PAT" | Normal if JWT not configured; ensure PAT is set |
| "Old user not found" | User doesn't exist on site — remove from CSV |
| "Rate limit exceeded" (429) | Tool auto-retries with Retry-After header — wait for completion |
| Interrupted mid-run | Re-run with `--resume-latest` to pick up where you left off |

## Best Practices

1. **Always dry-run first**
2. **Review the user classification** — prioritize very_high/high complexity users for careful migration
3. **Use classification CSVs** to run phased migrations by complexity tier
4. **Test with 5-10 users** before full batch
5. **Use Clone mode as a pilot** — verify before committing to Migrate
6. **Run during off-hours** to minimize user impact
7. **Review audit logs** after every run
8. **Run a post-migration dry-run with `--compare-latest`** to verify outcomes
9. **Keep CSV backups** with run IDs documented
10. **Monitor license consumption** during Clone mode (creates net-new users)

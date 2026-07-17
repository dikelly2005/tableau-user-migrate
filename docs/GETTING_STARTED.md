# Getting Started — Complete Beginner Walkthrough

This guide assumes you know nothing. Follow every step in order. Do not skip ahead.

---

## Prerequisites Checklist

Before you touch this tool, confirm you have:

- [ ] **Python 3.10 or higher** installed (`python --version` or `python3 --version`)
- [ ] **pip** available (`pip --version` or `pip3 --version`)
- [ ] **Git** installed (`git --version`)
- [ ] **Site Administrator** access on your Tableau Cloud site
- [ ] The list of old→new email mappings ready (e.g., in a spreadsheet)

---

## Step 1: Clone the Repository

```bash
git clone https://github.com/dikelly2005/tableau-user-migrate.git
cd tableau-user-migrate
```

You are now inside the project root. **All commands below assume you are in this directory.** If you ever get lost:

```bash
# Check you're in the right place — you should see src/, config/, docs/, etc.
ls
```

---

## Step 2: Create a Virtual Environment (Recommended)

This keeps dependencies isolated from your system Python.

```bash
python -m venv .venv
```

Activate it:

| OS | Command |
|----|---------|
| macOS/Linux | `source .venv/bin/activate` |
| Windows (PowerShell) | `.\.venv\Scripts\Activate.ps1` |
| Windows (CMD) | `.\.venv\Scripts\activate.bat` |

You should now see `(.venv)` in your terminal prompt.

---

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

Expected output ends with something like: `Successfully installed httpx-... PyJWT-... etc.`

If you want to run the test suite too:

```bash
pip install pytest pytest-asyncio
```

---

## Step 4: Create Your `.env` Configuration File

```bash
cp .env.example .env
```

Now open `.env` in any text editor and fill in your values:

```ini
# REQUIRED — Your Tableau Cloud URL (no trailing slash)
SERVER_URL=https://your-site.online.tableau.com

# REQUIRED — The content URL of your site (visible in the browser URL bar)
SITE_NAME=your-site-name

# REQUIRED — Path to your user mappings CSV (we'll create this next)
CSV_LOCATION=./data/user_mappings.csv

# REQUIRED — Where audit logs and output go
LOG_LOCATION=./audit
```

### Authentication — Choose ONE (or both for resilience):

**Option A: Personal Access Token (easier to set up)**

1. Log into Tableau Cloud as Site Administrator
2. Click your avatar → **My Account Settings**
3. Scroll to **Personal Access Tokens** → Create one
4. Copy the name and secret into `.env`:

```ini
TOKEN_NAME=my_migration_pat
TOKEN_SECRET=paste-the-long-secret-here
```

**Option B: JWT / Connected App (more secure, recommended for production)**

1. In Tableau Cloud: **Settings → Connected Apps → New Connected App**
2. Choose **Direct Trust**
3. Click **Generate New Secret**
4. Copy the values into `.env`:

```ini
TABLEAU_CONNECTED_APP_CLIENT_ID=paste-client-id
TABLEAU_CONNECTED_APP_SECRET_ID=paste-secret-id
TABLEAU_CONNECTED_APP_SECRET_VALUE=paste-secret-value
TABLEAU_USERNAME=your-admin-email@yourdomain.com
```

5. **CRITICAL**: In Tableau Cloud, go to the Connected App you just created and ensure these scopes are enabled:
   - `tableau:content:read`, `tableau:content:write`
   - `tableau:users:read`, `tableau:users:create`
   - `tableau:permissions:read`, `tableau:permissions:update`
   - `tableau:groups:read`, `tableau:groups:update`
   - `tableau:workbooks:read`, `tableau:workbooks:write`
   - `tableau:datasources:read`, `tableau:datasources:write`
   - `tableau:flows:read`, `tableau:flows:write`
   - `tableau:tasks:read`, `tableau:tasks:write`
   - `tableau:views:read`
   - `tableau:webhooks:read`, `tableau:webhooks:write`
   - `tableau:collections:read`, `tableau:collections:write`
   - `tableau:subscriptions:read`, `tableau:subscriptions:write`

---

## Step 5: Create Your User Mappings CSV

```bash
mkdir -p data
```

Create the file `data/user_mappings.csv` with this exact format:

```csv
old_username,new_username
jane.doe@qtsdatacenters.com,jane.doe@q.com
john.smith@qtsdatacenters.com,john.smith@q.com
```

**Rules:**
- First line MUST be the header: `old_username,new_username`
- One mapping per line
- Both values must be valid email addresses
- Case doesn't matter (the tool normalizes to lowercase)
- No duplicate old_username values
- No duplicate new_username values
- No circular references (A→B and B→A is invalid)

**Tip:** Start with 3–5 test users, not all 600. You can always run again with the full list later.

---

## Step 6: Validate Your Setup

```bash
python validate_setup.py
```

This checks everything: env vars, auth credentials, CSV format, and server connectivity.

**Expected output (all PASS):**

```
  [PASS] .env file exists
  [PASS] SERVER_URL configured
  [PASS] SITE_NAME configured
  [PASS] CSV file exists and is readable
  [PASS] CSV has valid headers
  [PASS] All usernames are valid emails
  [PASS] No duplicate entries
  [PASS] Authentication successful
  [PASS] Site accessible
```

**If anything says FAIL** — fix it before continuing. The error message tells you what's wrong.

---

## Step 7: Run a Dry Run (Read-Only — Changes Nothing)

```bash
python -m src.main --mode dry-run
```

**This is safe.** It makes zero changes to your Tableau Cloud site. It only reads data and generates reports.

**What happens:**
1. Authenticates to Tableau Cloud
2. Builds a cache of all site content (may take 1–5 minutes)
3. Generates per-user impact reports
4. Classifies users by migration complexity (low/moderate/high/very_high)
5. Writes everything to `audit/migrate_run_YYYYMMDD_HHMMSS/`

**Review the output:**

```bash
# Open the impact summary
cat audit/migrate_run_*/impact_summary.json

# Check per-user reports (one JSON file per user)
ls audit/migrate_run_*/user_reports_dry_run/

# Check the complexity classification
cat audit/migrate_run_*/user_classification.json
```

---

## Step 8: Review the Dry Run Results

Before proceeding, verify:

- [ ] The user count matches what you expect
- [ ] No ERROR entries in `audit_log.jsonl`
- [ ] Each user's permission/content counts look reasonable
- [ ] You understand the `user_classification.json` tiers

---

## Step 9: Run the Real Migration

Once you're satisfied with the dry-run, choose your mode:

### Option A: Batch Planning (recommended for large migrations)

Generate prioritized batch CSVs from your dry-run output (low-risk users first):

```bash
python plan_batches.py
```

This reads the `user_classification.json` from your latest dry-run, scores each user by migration complexity, and outputs per-batch CSVs to `data/batches/`. Customize with:

```bash
python plan_batches.py --batch-size 15                # Smaller batches
python plan_batches.py --dry-run-dir audit/migrate_run_20260707_121835  # Specific run
```

Then migrate one batch at a time using `--csv` to override the default CSV:

```bash
python -m src.main --mode migrate --csv data/batches/batch_01.csv --yes
python -m src.main --mode migrate --csv data/batches/batch_02.csv --yes
# ... verify between batches
```

### Option B: Clone Only (safest — old users stay active)

```bash
python -m src.main --mode clone --yes
```

This creates new users and copies all their access. Old users remain active. Good for piloting.

### Option C: Full Migration (clone + transfer ownership + deactivate old users)

For small batches (≤50 users):
```bash
python -m src.main --mode migrate --yes
```

For large batches (>50 users) — use `--batch-size` to prevent stale cache issues:
```bash
python -m src.main --mode migrate --yes --batch-size 50
```

---

## Step 10: Verify the Migration

Run a post-migration dry-run and compare against your baseline:

```bash
python -m src.main --mode dry-run --compare-latest
```

Then run the validation script:

```bash
python validate_migration.py
```

**Expected:** All old users show as `Unlicensed` with zero owned content. All new users have the correct permissions.

---

## If Something Goes Wrong

### Interrupted mid-run?

Resume where you left off:

```bash
python -m src.main --resume-latest
```

### Need to undo the migration?

```bash
python -m src.main --rollback ./audit/migrate_run_YYYYMMDD_HHMMSS/audit_log.jsonl
```

To also deactivate the newly created users:

```bash
python -m src.main --rollback ./audit/migrate_run_YYYYMMDD_HHMMSS/audit_log.jsonl --rollback-delete-users
```

### Something else?

Check the detailed troubleshooting guide:

```bash
cat docs/TROUBLESHOOTING.md
```

---

## Running the Test Suite

If you want to verify the tool's code is working correctly:

```bash
# Install test dependencies (if you haven't already)
pip install pytest pytest-asyncio

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run only the migration fix tests
pytest tests/test_migration_fixes.py -v

# Run only the workflow and idempotency tests
pytest tests/test_workflow_and_idempotency.py -v

# Run a specific test class
pytest tests/test_migration_fixes.py::TestCollectionService -v

# Run a specific test
pytest tests/test_migration_fixes.py::TestPIIRedaction::test_hash_email_deterministic -v
```

### What the tests cover:

| Test File | What It Validates |
|-----------|-------------------|
| `tests/test_migration_fixes.py` | PII redaction, collection safety (C1), subscription schedule validation (H2), webhook JSON payload (H1), Pulse endpoint resolution (C2), permission cache batch refresh (C3) |
| `tests/test_workflow_and_idempotency.py` | Multi-page pagination, H4 deactivation blocking when content remains, 409 conflict handling (groups, favorites, subscriptions) |
| `tests/test_cache.py` | Dimension cache internals |
| `tests/test_checkpoint.py` | Checkpoint save/load/resume |
| `tests/test_csv_loader.py` | CSV validation rules |
| `tests/test_paths.py` | Endpoint path resolution |
| `tests/test_services.py` | Service unit tests |
| `tests/test_settings.py` | Configuration loading |

---

## CLI Reference — All Available Flags

```
python -m src.main [OPTIONS]

Required:
  --mode {dry-run,clone,migrate,clean-only}
                        Execution mode

Optional:
  --yes                 Skip interactive confirmation prompt
  --csv PATH            Override CSV_LOCATION from .env (e.g., data/batches/batch_01.csv)
  --skip-validation     Skip CSV and config validation at startup
  --batch-size N        Process users in batches of N (refreshes cache between batches)
  --resume PATH         Resume from a specific checkpoint file
  --resume-latest       Resume the most recent incomplete checkpoint
  --compare ID          Compare dry-run against a previous run ID
  --compare-latest      Compare dry-run against the most recent previous dry-run
  --rollback PATH       Rollback a migration using an audit log file
  --rollback-delete-users
                        Also deactivate newly created users during rollback
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `SERVER_URL` | Yes | — | Your Tableau Cloud URL (e.g., `https://site.online.tableau.com`) |
| `SITE_NAME` | Yes | — | Content URL of your site |
| `CSV_LOCATION` | Yes | — | Path to user mappings CSV |
| `LOG_LOCATION` | Yes | — | Directory for audit output |
| `TABLEAU_CONNECTED_APP_CLIENT_ID` | JWT | — | Connected App client ID |
| `TABLEAU_CONNECTED_APP_SECRET_ID` | JWT | — | Connected App secret ID |
| `TABLEAU_CONNECTED_APP_SECRET_VALUE` | JWT | — | Connected App secret value |
| `TABLEAU_USERNAME` | JWT | — | Admin username for JWT auth |
| `TOKEN_NAME` | PAT | — | Personal Access Token name |
| `TOKEN_SECRET` | PAT | — | Personal Access Token secret |
| `API_DELAY_MS` | No | `100` | Milliseconds between API calls |
| `MAX_RETRIES` | No | `3` | Max retries on transient failures |
| `RETRY_BACKOFF_BASE` | No | `2.0` | Exponential backoff multiplier |
| `RETRY_JITTER` | No | `true` | Randomize retry waits |
| `RATE_LIMIT_RPS` | No | `10` | Max requests per second |
| `SESSION_DURATION_SECONDS` | No | `7200` | Auth session lifetime (seconds) |
| `TOKEN_REFRESH_THRESHOLD_SECONDS` | No | `300` | Re-auth before token expiry |
| `DIMENSION_CACHE_TTL_HOURS` | No | `24` | Cache file expiry |
| `DIMENSION_CACHE_ENABLED` | No | `true` | Enable dimension caching |
| `REDACT_PII` | No | `false` | Hash emails in audit JSONL (GDPR) |

---

## File Structure (What Everything Is)

```
tableau-user-migrate/
├── .env.example            ← Copy this to .env and fill in your values
├── .env                    ← YOUR config (never commit this)
├── .gitignore              ← Keeps secrets and output out of git
├── requirements.txt        ← Python dependencies
├── pytest.ini              ← Test runner configuration
├── validate_setup.py       ← Pre-flight check script
├── validate_migration.py   ← Post-migration verification script
├── plan_batches.py         ← Batch planning (scores users, outputs per-batch CSVs)
│
├── config/
│   ├── settings.py         ← Loads .env into Python config objects
│   └── endpoints.yaml      ← All Tableau REST API endpoint paths
│
├── src/
│   ├── main.py             ← CLI entrypoint (you run this)
│   ├── api/                ← Auth, HTTP client, rate limiting
│   ├── services/           ← One file per Tableau artifact type
│   ├── utils/              ← Cache, checkpoints, CSV loader, helpers
│   └── workflows/          ← Orchestration (dry_run, clone, migrate, cleanup, rollback)
│
├── models/                 ← Data models (mapping, impact)
├── reporting/              ← Audit logger (JSONL output)
├── tests/                  ← Unit tests
├── data/                   ← Your user_mappings.csv goes here
├── audit/                  ← Run output (auto-created)
└── docs/                   ← All documentation (you are here)
```

---

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Running from wrong directory | `cd` into the `tableau-user-migrate/` folder first |
| Forgot to activate venv | Run `source .venv/bin/activate` (or Windows equivalent) |
| `.env` not created | Run `cp .env.example .env` then edit it |
| CSV has wrong header | First line must be exactly `old_username,new_username` |
| PAT expired | Create a new one in Tableau Cloud → My Account Settings |
| Using `http://` instead of `https://` | Always use `https://` for SERVER_URL |
| Running migrate before dry-run | Always dry-run first. Always. |
| Running on the wrong site | Double-check `SITE_NAME` matches your target site's content URL |

---

## Summary — The 10-Step Checklist

```
[ ] 1. git clone the repo
[ ] 2. cd into the project directory
[ ] 3. python -m venv .venv && source .venv/bin/activate
[ ] 4. pip install -r requirements.txt
[ ] 5. cp .env.example .env — edit with your credentials
[ ] 6. Create data/user_mappings.csv with old→new email pairs
[ ] 7. python validate_setup.py — all PASS
[ ] 8. python -m src.main --mode dry-run — review output
[ ] 9. python -m src.main --mode migrate --yes --batch-size 50
[ ] 10. python validate_migration.py — confirm success
```

If step 7 fails, fix the issue before moving on.
If step 8 shows unexpected results, investigate before step 9.
Step 9 is irreversible without `--rollback`. Be sure.

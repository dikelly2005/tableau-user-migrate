# Tableau Cloud User Migrate Tool v2

Migrates Tableau Cloud users from one username (email) to another. Because Tableau Cloud usernames are immutable, this tool automates the workaround: **clone → migrate → cleanup**.

**Use case**: Domain migrations, email renames, M&A consolidation — any scenario where a Tableau Cloud user's email/username must change.

---

## Quick Start

> **New here?** Read the full walkthrough: [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md)

### Prerequisites

- Python 3.10+
- Tableau Cloud site with admin access
- JWT (Connected App) or Personal Access Token credentials

### Installation

```bash
git clone https://github.com/dikelly2005/tableau-user-migrate.git
cd tableau-user-migrate
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your Tableau Cloud credentials and settings
```

### Validate Setup

```bash
python validate_setup.py
```

### Run

```bash
# Impact analysis (read-only)
python -m src.main --mode dry-run

# Clone access to new user (old user stays active)
python -m src.main --mode clone --yes

# Full migration (clone + transfer ownership + deactivate old)
python -m src.main --mode migrate --yes

# Full migration in batches of 50 (recommended for >50 users)
python -m src.main --mode migrate --yes --batch-size 50

# Strip access and deactivate old users only
python -m src.main --mode clean-only --yes

# Resume interrupted run
python -m src.main --resume-latest

# Compare dry-runs (post-migration verification)
python -m src.main --mode dry-run --compare-latest

# Rollback a migration
python -m src.main --rollback ./audit/migrate_run_YYYYMMDD_HHMMSS/audit_log.jsonl
```

---

## How It Works

1. Authenticates via JWT (primary) or PAT (fallback)
2. Auto-negotiates API version via `/serverinfo`
3. Builds a dimension cache of all site content (three-pass warmup)
4. Loads CSV user mappings (case-insensitive)
5. Generates per-user impact reports (pre-mutation state)
6. Executes the selected workflow mode
7. Outputs audit trail + checkpoint summary

---

## Modes

| Mode | Description |
|------|-------------|
| `dry-run` | Read-only impact analysis with per-user reports and complexity classification |
| `clone` | Creates new user, clones all access (9 steps). Old user stays active |
| `migrate` | Clone + transfer ownership + deactivate old user (16 steps) |
| `clean-only` | Strips all access and deactivates old users (7 steps) |

---

## CSV Format

Place your mapping file at the path specified in `CSV_LOCATION` (default: `./data/user_mappings.csv`):

```csv
old_username,new_username
jane.doe@old-domain.com,jane.doe@new-domain.com
john.smith@old-domain.com,john.smith@new-domain.com
```

---

## Authentication

### JWT / Connected App (Primary)

Set in `.env`:
- `TABLEAU_CONNECTED_APP_CLIENT_ID`
- `TABLEAU_CONNECTED_APP_SECRET_ID`
- `TABLEAU_CONNECTED_APP_SECRET_VALUE`
- `TABLEAU_USERNAME`

### Personal Access Token (Fallback)

Set in `.env`:
- `TOKEN_NAME`
- `TOKEN_SECRET`

The tool uses JWT as primary auth and falls back to PAT on 401/403.

---

## Audit Output

Each run produces a timestamped directory under `audit/`:

```
audit/migrate_run_<timestamp>/
├── impact_summary.json
├── audit_log.jsonl
├── execution.log
├── user_reports_<mode>/
│   └── <username>.json
└── user_classification.json    (dry-run only)
```

---

## Post-Migration Validation

```bash
python validate_migration.py
```

---

## Key Features

- **Resumable**: Per-user, per-step checkpoints. Safe to re-run.
- **Idempotent**: 409/conflict responses are treated as skips, not failures.
- **Zero per-user list calls**: All reads come from the dimension cache.
- **Rate limiting**: Asyncio semaphore + token bucket with Retry-After support.
- **Full audit trail**: Every mutation logged to JSONL with success/fail/skip status.

---

## Known Limitations

| Artifact | Limitation |
|----------|-----------|
| Data alerts | Ownership transferred via PUT after adding new user as recipient |
| Collections | No ownership update endpoint — uses clone-and-replace |
| PATs | Cannot be cloned (secrets) — user must recreate |
| OAuth / embedded credentials | Cannot be migrated via API |

---

## Project Structure

```
├── config/
│   ├── settings.py          # Configuration from .env
│   └── endpoints.yaml       # Endpoint registry
├── src/
│   ├── main.py              # CLI entrypoint
│   ├── api/                 # Auth, HTTP client, rate limiting
│   ├── services/            # Per-artifact migration logic
│   ├── workflows/           # Mode orchestration
│   └── utils/               # Cache, checkpoints, CSV loading
├── models/                  # Data models
├── reporting/               # Audit logging
├── tests/                   # Unit tests
├── data/                    # User mapping CSVs
└── audit/                   # Run output
```

---

## Documentation

Detailed guides are available in the [`docs/`](docs/) directory:

| Guide | Description |
|-------|-------------|
| [Setup Guide](docs/SETUP_GUIDE.md) | Full installation, Connected App configuration, CSV validation rules, and first-run walkthrough |
| [Quick Reference](docs/QUICK_REFERENCE.md) | Cheat sheet — CLI commands, mode comparison matrix, workflow steps, terminal log prefixes |
| [Deployment Checklist](docs/DEPLOYMENT_CHECKLIST.md) | Pre/post-deployment checklist covering auth, data prep, execution plan, and rollback awareness |
| [Pre-Migration Checklist](docs/PRE_MIGRATION_CHECKLIST.md) | Credential re-authentication guide for content owners, communication template, and what does/doesn't need action |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common failure scenarios, checkpoint inspection, resume workflows, and recovery steps |
| [Known Limitations & Recovery](docs/KNOWN_LIMITATIONS_AND_RECOVERY.md) | API constraints, edge cases, and manual recovery procedures |
| [Implementation Summary](docs/IMPLEMENTATION_SUMMARY.md) | Technical architecture, cache design, and service internals |

---

## License

Internal use only.

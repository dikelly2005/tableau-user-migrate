# Discover Tableau Endpoints

Automatically catalogues every Tableau REST API endpoint with its HTTP method, URL, name, and JWT access scope by crawling the official Tableau documentation.

Endpoints without a JWT scope are flagged as requiring PAT (Personal Access Token) or session-based authentication.

## Why

- Tableau doesn't publish a single reference of all JWT scopes mapped to endpoints
- Manually tracking which scopes your Connected App needs is error-prone
- When Tableau adds new endpoints or changes scopes, you need to know immediately
- Identifying PAT-only endpoints lets you plan auth fallback strategies

## Output

The script produces a JSON file with:

- **metadata** — crawl timestamp, totals, coverage stats
- **scopes_summary** — all discovered scopes grouped by wildcard category (e.g., `tableau:workbooks:*`)
- **pat_required** — endpoints with no JWT scope (must use PAT/session auth)
- **endpoints** — full catalogue of every endpoint with name, method, URL, scope, and category

## Requirements

```
pip install beautifulsoup4 requests
```

No browser or Selenium needed for the default mode.

## Usage

```bash
# Crawl live from help.tableau.com (recommended):
python scripts/discover_tableau_endpoints.py --pages-dir ./cached_pages --output output/tableau_endpoints.json

# Parse pre-downloaded HTML files (offline):
python scripts/discover_tableau_endpoints.py --local-only --pages-dir ./REFERENCE --output output/tableau_endpoints.json
```

The `--pages-dir` flag caches downloaded pages locally so subsequent runs are instant.

## Sample Output

```json
{
  "metadata": {
    "total_endpoints": 474,
    "endpoints_with_scope": 418,
    "endpoints_with_method": 474,
    "endpoints_without_scope_pat_required": 56,
    "wildcard_categories": 82
  },
  "scopes_summary": {
    "wildcard_categories": {
      "tableau:workbooks:*": ["create", "delete", "download", "read", "update"],
      "tableau:users:*": ["create", "delete", "read", "update"],
      "tableau:metric_subscriptions:*": ["create", "delete", "read"]
    }
  },
  "pat_required": [
    {
      "name": "Sign In",
      "method": "POST",
      "url": "/api/api-version/auth/signin",
      "category": "authentication"
    }
  ],
  "endpoints": [
    {
      "name": "Query Workbooks for Site",
      "method": "GET",
      "url": "/api/api-version/sites/site-id/workbooks",
      "scope": "tableau:workbooks:read",
      "category": "workbooks_and_views"
    }
  ]
}
```

## Accuracy

Validated against source HTML: **418/418 scope-to-endpoint mappings verified correct** (100%).

## Optional: Selenium Rendering

If Tableau ever moves to pure client-side rendering, you can use `--render` mode:

```bash
pip install selenium webdriver-manager
python scripts/discover_tableau_endpoints.py --render --output output/tableau_endpoints.json
```

This launches a headless browser to render JavaScript before parsing. Not currently needed — the standard HTTP fetch works.

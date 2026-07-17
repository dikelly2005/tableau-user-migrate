# Discover and catalogue all Tableau REST API endpoints with methods, URLs, and JWT scopes
# Co-authored with CoCo

"""
Discovers and catalogues every Tableau REST API endpoint with its HTTP method,
URL, name, and JWT access scope. Endpoints without a scope are flagged as
requiring PAT/session authentication.

Works by crawling the official Tableau REST API reference documentation at
help.tableau.com and parsing endpoint sections from the HTML.

Usage:
    # Recommended: crawl live pages (no browser needed):
    python scripts/discover_tableau_endpoints.py --pages-dir ./cached_pages --output output/tableau_endpoints.json

    # Parse pre-downloaded HTML files:
    python scripts/discover_tableau_endpoints.py --local-only --pages-dir ./REFERENCE --output output/tableau_endpoints.json

    # Auto-crawl with Selenium rendering (optional, for JS-heavy pages):
    python scripts/discover_tableau_endpoints.py --render --output output/tableau_endpoints.json

Requirements:
    pip install beautifulsoup4 requests
    # For --render mode (optional):
    pip install selenium webdriver-manager
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://help.tableau.com/current/api/rest_api/en-us/REST/"
INDEX_PAGE = f"{BASE_URL}rest_api_ref.htm"
REQUEST_DELAY = 1.5
MAX_RETRIES = 3
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Network / rendering helpers
# ---------------------------------------------------------------------------

def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return session


def fetch_page(session: requests.Session, url: str) -> str | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  Retry {attempt + 1}: {e}")
                time.sleep(2 ** attempt)
            else:
                print(f"  FAILED: {e}")
                return None
    return None


def render_page_selenium(url: str) -> str | None:
    """Render a page using Selenium. Tries Edge first (less corporate restrictions), then Chrome."""
    try:
        from selenium import webdriver
    except ImportError:
        print("  ERROR: Install selenium + webdriver-manager: pip install selenium webdriver-manager")
        return None

    # Try Edge first (usually less restricted on corporate Windows)
    html = _try_edge(url)
    if html:
        return html

    # Fallback to Chrome
    html = _try_chrome(url)
    if html:
        return html

    print("  ERROR: Both Edge and Chrome failed. Check browser accessibility.")
    return None


def _try_edge(url: str) -> str | None:
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
        from selenium.webdriver.edge.service import Service
        from webdriver_manager.microsoft import EdgeChromiumDriverManager

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--user-data-dir=" + str(Path.home() / ".tableau_scope_crawler_edge"))

        service = Service(EdgeChromiumDriverManager().install())
        driver = webdriver.Edge(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.get(url)
        time.sleep(4)
        html = driver.page_source
        driver.quit()
        return html
    except Exception as e:
        print(f"  Edge failed: {e}")
        return None


def _try_chrome(url: str) -> str | None:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-client-side-phishing-detection")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-hang-monitor")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-prompt-on-repost")
        options.add_argument("--disable-sync")
        options.add_argument("--no-first-run")
        options.add_argument("--user-data-dir=" + str(Path.home() / ".tableau_scope_crawler_chrome"))

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.get(url)
        time.sleep(4)
        html = driver.page_source
        driver.quit()
        return html
    except Exception as e:
        print(f"  Chrome failed: {e}")
        return None


def discover_ref_pages(session: requests.Session, use_render: bool = False) -> list[dict]:
    """Find all rest_api_ref_*.htm links from the index page."""
    print(f"Fetching index: {INDEX_PAGE}")
    html = render_page_selenium(INDEX_PAGE) if use_render else fetch_page(session, INDEX_PAGE)
    if not html:
        raise RuntimeError("Failed to fetch REST API index page")

    soup = BeautifulSoup(html, "html.parser")
    pages = []
    seen = set()

    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = re.search(r"(rest_api_ref_[^\"#?]+\.htm)", href)
        if match:
            page_name = match.group(1)
            if page_name not in seen:
                seen.add(page_name)
                full_url = urljoin(BASE_URL, page_name)
                pages.append({"name": page_name, "url": full_url})

    print(f"Discovered {len(pages)} reference pages")
    return pages


# ---------------------------------------------------------------------------
# Endpoint extraction — rendered HTML parser
# ---------------------------------------------------------------------------

def extract_category(page_name: str) -> str:
    """Derive category from page filename or title."""
    # Handle saved browser titles like "Users and Groups Methods - Tableau.html"
    match = re.match(r"(.+?)(?:\s*Methods?\s*)?(?:\s*-\s*(?:Retired[^.]*|Tableau))?\.html?$", page_name, re.IGNORECASE)
    if match:
        return match.group(1).strip().lower().replace(" ", "_")
    match = re.match(r"rest_api_ref_(.+?)\.html?$", page_name)
    if match:
        return match.group(1)
    return "unknown"


def extract_endpoints(html: str, page_name: str) -> list[dict]:
    """
    Parse a rendered Tableau REST API reference page.
    
    Handles two DOM patterns:
    1. Standard: <h2> followed by sibling elements until next <h2>
    2. Flat (Pulse): All content as siblings inside snippetBlock,
       with <p class="api-syntax"> for URIs
    """
    soup = BeautifulSoup(html, "html.parser")
    category = extract_category(page_name)
    endpoints = []

    # Filter out non-endpoint headings
    skip_ids = {"topic-title", "use-postman", "errors", "insight-types",
                "what-do-pulse-rest-api-methods-enable", "configuring-pulse-insights-for-a-metric",
                "tableau-pulse-overview"}

    # Find all h2 headings with IDs (each is an endpoint)
    headings = [
        h for h in soup.find_all("h2", id=True)
        if h.get("id") not in skip_ids
        and not h.get_text(strip=True).lower().startswith(("use postman", "error"))
    ]

    for i, h2 in enumerate(headings):
        name = h2.get_text(strip=True)
        h2_id = h2.get("id", "")

        # Determine section boundaries
        # Strategy 1: Siblings of h2 until next h2 (standard pages)
        section_parts = []
        for sibling in h2.find_next_siblings():
            if sibling.name == "h2":
                break
            text = sibling.get_text(separator="\n", strip=True)
            if text:
                section_parts.append(text)

        # Strategy 2: If no siblings found, this might be a flat layout (Pulse)
        # Walk forward through ALL following elements until the next h2
        if not section_parts:
            node = h2
            while True:
                node = node.find_next()
                if node is None:
                    break
                if node.name == "h2" and node.get("id") and node != h2:
                    break
                if node.name in ("p", "div", "dl", "table", "pre", "ul", "ol"):
                    text = node.get_text(separator="\n", strip=True)
                    if text:
                        section_parts.append(text)

        section_text = "\n".join(section_parts)

        # --- Extract Method + URL ---
        method = None
        url = None

        # Check for <p class="api-syntax"> pattern (Pulse pages)
        api_syntax_el = None
        if h2.find_next_siblings():
            for sib in h2.find_next_siblings():
                if sib.name == "h2":
                    break
                if sib.name == "p" and "api-syntax" in " ".join(sib.get("class", [])):
                    api_syntax_el = sib
                    break

        # Flat layout: look forward
        if not api_syntax_el:
            node = h2
            for _ in range(30):
                node = node.find_next()
                if node is None or (node.name == "h2" and node.get("id") and node != h2):
                    break
                if node.name == "p" and "api-syntax" in " ".join(node.get("class", [])):
                    api_syntax_el = node
                    break

        if api_syntax_el:
            syntax_text = api_syntax_el.get_text(strip=True)
            m = re.match(r"(GET|POST|PUT|DELETE|PATCH)\s+(.+)", syntax_text)
            if m:
                method = m.group(1)
                url = m.group(2).strip()

        # Fallback: regex on section text
        if not method:
            uri_match = re.search(
                r"(GET|POST|PUT|DELETE|PATCH)\s+(/api/[^\n]+|\{server\}[^\n]+)",
                section_text
            )
            if uri_match:
                method = uri_match.group(1)
                raw_url = uri_match.group(2).strip()
                url = re.sub(r"\s+", "", raw_url)

        # Fallback 2: METHOD on one line, path segments on following lines
        if not method:
            lines = section_text.split("\n")
            for li, line in enumerate(lines):
                m = re.match(r"^(GET|POST|PUT|DELETE|PATCH)\s+/api/", line.strip())
                if m:
                    method = m.group(1)
                    # Reassemble URL from this line and following path segments
                    url_parts = [line.strip()[len(method):].strip()]
                    for next_line in lines[li + 1:li + 15]:
                        seg = next_line.strip()
                        if not seg or seg.startswith(("Parameter", "Request", "Attribute",
                                                      "Permissions", "Required", "Version",
                                                      "Note", "For more")):
                            break
                        if re.match(r"^[\w{}\-/:.?&=]+$", seg):
                            url_parts.append(seg)
                        else:
                            break
                    url = "".join(url_parts)
                    break

        # --- Extract Scope ---
        scope = None
        scope_match = re.search(r"(tableau:\w+:\w+)", section_text)
        if scope_match:
            scope = scope_match.group(1)

        # Only add if we have a method/url OR the section looks like a real endpoint
        if method or url or ("URI" in section_text and len(section_text) > 100):
            endpoints.append({
                "name": name,
                "method": method,
                "url": url,
                "scope": scope,
                "category": category,
                "source_page": page_name,
                "heading_id": h2_id,
            })

    return endpoints


# ---------------------------------------------------------------------------
# Output building
# ---------------------------------------------------------------------------

def build_output(all_endpoints: list[dict]) -> dict:
    scopes_by_name = {}
    for ep in all_endpoints:
        if not ep.get("scope"):
            continue
        scope = ep["scope"]
        if scope not in scopes_by_name:
            scopes_by_name[scope] = []
        entry = ep["name"] or f"{ep['method']} {ep['url']}"
        if entry not in scopes_by_name[scope]:
            scopes_by_name[scope].append(entry)

    wildcard_summary = {}
    for scope in sorted(scopes_by_name.keys()):
        parts = scope.split(":")
        if len(parts) == 3:
            wc = f"{parts[0]}:{parts[1]}:*"
            if wc not in wildcard_summary:
                wildcard_summary[wc] = set()
            wildcard_summary[wc].add(parts[2])

    pat_required = [
        {k: v for k, v in ep.items() if k != "heading_id"}
        for ep in all_endpoints if not ep.get("scope")
    ]

    # Clean heading_id from endpoint output
    clean_endpoints = [{k: v for k, v in ep.items()} for ep in all_endpoints]

    total = len(all_endpoints)
    with_scope = sum(1 for ep in all_endpoints if ep.get("scope"))
    with_method = sum(1 for ep in all_endpoints if ep.get("method"))

    return {
        "metadata": {
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "base_url": BASE_URL,
            "total_endpoints": total,
            "endpoints_with_scope": with_scope,
            "endpoints_with_method": with_method,
            "endpoints_without_scope_pat_required": len(pat_required),
        },
        "scopes_summary": {
            "by_scope": scopes_by_name,
            "wildcard_categories": {k: sorted(v) for k, v in sorted(wildcard_summary.items())},
        },
        "pat_required": pat_required,
        "endpoints": clean_endpoints,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Discover and catalogue all Tableau REST API endpoints, methods, URLs, and JWT scopes"
    )
    parser.add_argument("--output", "-o", default="output/tableau_endpoints.json",
                        help="Output JSON file path")
    parser.add_argument("--pages-dir", default=None,
                        help="Directory containing HTML pages (for caching or --local-only)")
    parser.add_argument("--local-only", action="store_true",
                        help="Skip network; parse all .htm/.html files in --pages-dir")
    parser.add_argument("--render", action="store_true",
                        help="Use Selenium to render JS (requires Chrome)")
    parser.add_argument("--update-yaml", default="config/endpoints.yaml",
                        help="Path to endpoints.yaml to update with scope/auth_type annotations (default: config/endpoints.yaml)")
    parser.add_argument("--generate-full-yaml", default="config/endpoints_full.yaml",
                        help="Generate a complete endpoints.yaml with ALL discovered endpoints (default: config/endpoints_full.yaml)")
    parser.add_argument("--skip-yaml-updates", action="store_true",
                        help="Skip all YAML file generation/updates (JSON output only)")
    args = parser.parse_args()

    session = get_session()

    # Discover pages
    if args.local_only:
        if not args.pages_dir:
            parser.error("--local-only requires --pages-dir")
        pages_path = Path(args.pages_dir)
        ref_pages = [
            {"name": f.name, "url": None}
            for f in sorted(pages_path.iterdir())
            if f.suffix in (".htm", ".html")
            and ("rest_api_ref" in f.name or "Methods" in f.name)
        ]
        print(f"Local mode: {len(ref_pages)} reference files in {pages_path}")
    else:
        ref_pages = discover_ref_pages(session, use_render=args.render)
        time.sleep(REQUEST_DELAY)

    # Process pages
    all_endpoints = []

    for i, page in enumerate(ref_pages, 1):
        print(f"[{i}/{len(ref_pages)}] {page['name']}")

        html = None

        # Read from local
        if args.pages_dir:
            cache_path = Path(args.pages_dir) / page["name"]
            if cache_path.exists():
                html = cache_path.read_text(encoding="utf-8")

        # Fetch / render if not local
        if not html and page.get("url"):
            if args.render:
                print(f"  Rendering...")
                html = render_page_selenium(page["url"])
            else:
                html = fetch_page(session, page["url"])
            # Cache
            if html and args.pages_dir:
                cd = Path(args.pages_dir)
                cd.mkdir(parents=True, exist_ok=True)
                (cd / page["name"]).write_text(html, encoding="utf-8")

        if not html:
            print(f"  SKIPPED")
            continue

        endpoints = extract_endpoints(html, page["name"])
        all_endpoints.extend(endpoints)

        scoped = sum(1 for e in endpoints if e.get("scope"))
        methods = sum(1 for e in endpoints if e.get("method"))
        print(f"  {len(endpoints)} endpoints ({scoped} scoped, {len(endpoints) - scoped} PAT-only, {methods} with method)")

        if not args.local_only and page.get("url"):
            time.sleep(REQUEST_DELAY)

    # Deduplicate
    seen = set()
    unique = []
    for ep in all_endpoints:
        key = (ep.get("method"), ep.get("url"), ep.get("name"))
        if key not in seen:
            seen.add(key)
            unique.append(ep)
    all_endpoints = unique

    # Build output
    output = build_output(all_endpoints)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also write a compact summary
    summary_path = output_path.with_stem(output_path.stem + "_summary")
    summary = {
        "metadata": output["metadata"],
        "scopes_summary": output["scopes_summary"],
        "pat_required": output["pat_required"],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print results
    meta = output["metadata"]
    print(f"\nDone! {output_path}")
    print(f"  Total endpoints:       {meta['total_endpoints']}")
    print(f"  With JWT scope:        {meta['endpoints_with_scope']}")
    print(f"  With method+URL:       {meta['endpoints_with_method']}")
    print(f"  PAT required:          {meta['endpoints_without_scope_pat_required']}")
    print(f"  Wildcard categories:   {len(output['scopes_summary']['wildcard_categories'])}")

    print("\n=== Wildcard Scope Categories ===")
    for wc, actions in sorted(output["scopes_summary"]["wildcard_categories"].items()):
        print(f"  {wc:50s} {', '.join(actions)}")

    if output["pat_required"]:
        print(f"\n=== PAT-Required Endpoints ({len(output['pat_required'])}) ===")
        for ep in output["pat_required"][:25]:
            m = ep.get("method") or "?"
            u = ep.get("url") or "?"
            print(f"  {m:<7s} {u:<60s} {ep['name']}")
        if len(output["pat_required"]) > 25:
            print(f"  ... and {len(output['pat_required']) - 25} more")

    # Update endpoints.yaml with scope/auth_type annotations
    if not args.skip_yaml_updates and args.update_yaml:
        update_endpoints_yaml(args.update_yaml, all_endpoints, output_path)

    # Generate full registry YAML with all discovered endpoints
    if not args.skip_yaml_updates and args.generate_full_yaml:
        generate_full_yaml(args.generate_full_yaml, all_endpoints, args.update_yaml)


# ---------------------------------------------------------------------------
# YAML update logic
# ---------------------------------------------------------------------------

def normalize_path(path: str) -> str:
    """
    Normalize an endpoint path for comparison.
    Converts both formats to a canonical form:
      endpoints.yaml: "sites/{site_id}/users"
      crawled:        "/api/api-version/sites/site-id/users" or "{server}/api/-/pulse/alerts"
    """
    # Strip leading server/api prefix
    path = re.sub(r"^\{server\}", "", path)
    path = re.sub(r"^/api/api-version/", "", path)
    path = re.sub(r"^/api/\d+\.\d+/", "", path)
    path = re.sub(r"^/api/-/", "-/", path)
    path = re.sub(r"^/api/", "", path)
    path = path.strip("/")

    # Normalize parameter placeholders:
    # "site-id" -> "{site_id}", "group-id" -> "{group_id}", etc.
    path = re.sub(r"site-id", "{site_id}", path)
    path = re.sub(r"site-luid", "{site_id}", path)
    path = re.sub(r"group-id", "{group_id}", path)
    path = re.sub(r"group-set-id", "{group_set_id}", path)
    path = re.sub(r"user-id", "{user_id}", path)
    path = re.sub(r"workbook-id", "{workbook_id}", path)
    path = re.sub(r"view-id", "{view_id}", path)
    path = re.sub(r"datasource-id", "{datasource_id}", path)
    path = re.sub(r"flow-id", "{flow_id}", path)
    path = re.sub(r"project-id", "{project_id}", path)
    path = re.sub(r"schedule-id", "{schedule_id}", path)
    path = re.sub(r"job-id", "{job_id}", path)
    path = re.sub(r"task-id", "{task_id}", path)
    path = re.sub(r"subscription-id", "{subscription_id}", path)
    path = re.sub(r"alert-id", "{alert_id}", path)
    path = re.sub(r"webhook-id", "{webhook_id}", path)
    path = re.sub(r"collection-luid", "{collection_luid}", path)
    path = re.sub(r"custom-view-id", "{custom_view_id}", path)
    path = re.sub(r"connection-id", "{connection_id}", path)
    path = re.sub(r"revision-number", "{revision_number}", path)

    # Generic: any remaining "foo-bar-id" -> "{foo_bar_id}"
    path = re.sub(r"\b([a-z]+(?:-[a-z]+)*)-id\b", lambda m: "{" + m.group(1).replace("-", "_") + "_id}", path)
    # Any remaining "foo-luid" -> "{foo_luid}"
    path = re.sub(r"\b([a-z]+(?:-[a-z]+)*)-luid\b", lambda m: "{" + m.group(1).replace("-", "_") + "_luid}", path)

    return path


def match_yaml_to_discovered(yaml_path: str, discovered_endpoints: list[dict]) -> dict:
    """
    Match each endpoint in endpoints.yaml to its discovered counterpart.
    Uses prefix matching: yaml path "sites/{site_id}/datasources" matches
    discovered "/api/api-version/sites/site-id/datasources" (list endpoint).
    Returns {normalized_yaml_path: [discovered_endpoint_dicts]}
    """
    # Build list of (normalized_url, endpoint_data) for all discovered
    discovered_normalized = []
    for ep in discovered_endpoints:
        url = ep.get("url")
        if not url:
            continue
        norm = normalize_path(url)
        discovered_normalized.append((norm, ep))

    # For each yaml path, find discovered endpoints that match
    # Match criteria: exact match OR the discovered path equals yaml_path + optional /{param}
    discovered_by_yaml_path = {}

    # We'll call this with each yaml path
    def find_matches(yaml_norm: str) -> list[dict]:
        matches = []
        for norm, ep in discovered_normalized:
            # Exact match
            if norm == yaml_norm:
                matches.append(ep)
            # yaml is the "list" endpoint, discovered is the same path (GET for list)
            elif norm == yaml_norm and ep.get("method") == "GET":
                matches.append(ep)
        # If no exact match, find the closest prefix match for the list endpoint
        if not matches:
            for norm, ep in discovered_normalized:
                # Discovered URL starts with yaml path (e.g., yaml="sites/{site_id}/datasources"
                # matches discovered "sites/{site_id}/datasources/{datasource_id}")
                if norm.startswith(yaml_norm + "/") or norm == yaml_norm:
                    matches.append(ep)
            # Also try: yaml path is longer (detail endpoint matching a discovered list)
            if not matches:
                for norm, ep in discovered_normalized:
                    if yaml_norm.startswith(norm + "/") or yaml_norm == norm:
                        matches.append(ep)
        return matches

    return find_matches


def _write_yaml_clean(yaml_file: Path, data, yaml_instance):
    """
    Write scope annotations by directly manipulating the YAML text.
    ruamel.yaml's mapping insertion doesn't handle blank lines and comments
    between endpoint blocks correctly. Instead, we read the original file
    and insert scope/scope_write/auth_type lines right after the last
    existing property of each endpoint block.
    """
    # Instead of using ruamel's dump, we operate on the original text
    # and insert annotations in the right place using regex
    original = yaml_file.read_text(encoding="utf-8")
    lines = original.split("\n")

    # Build a map of endpoint names -> their scope/scope_write/auth_type values from data
    endpoints_section = data.get("endpoints", {})
    annotations = {}  # ep_name -> {scope: ..., scope_write: ..., auth_type: ...}
    for ep_name, ep_config in endpoints_section.items():
        if not hasattr(ep_config, 'get'):
            continue
        ann = {}
        if ep_config.get("scope"):
            ann["scope"] = ep_config["scope"]
        if ep_config.get("scope_write"):
            ann["scope_write"] = ep_config["scope_write"]
        if ep_config.get("auth_type"):
            ann["auth_type"] = ep_config["auth_type"]
        if ann:
            annotations[ep_name] = ann

    # Process the file: find each endpoint block and insert/update annotations
    result_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect start of an endpoint block: "  endpoint_name:" at 2-space indent
        ep_match = re.match(r"^  (\w+):$", line)
        if ep_match and ep_match.group(1) in annotations:
            ep_name = ep_match.group(1)
            ann = annotations[ep_name]
            result_lines.append(line)
            i += 1

            # Collect all property lines for this endpoint (4+ space indent, non-blank)
            # Skip any existing scope/scope_write/auth_type lines
            while i < len(lines):
                prop_line = lines[i]
                # Blank line or comment = end of this endpoint's properties
                if prop_line.strip() == "" or prop_line.strip().startswith("#"):
                    break
                # Next endpoint at 2-space indent = end
                if re.match(r"^  \w+:", prop_line) and not prop_line.startswith("    "):
                    break
                # Skip old scope/scope_write/auth_type lines (we'll re-add them)
                if re.match(r"^\s+(scope|scope_write|auth_type):", prop_line):
                    i += 1
                    continue
                result_lines.append(prop_line)
                i += 1

            # Now insert the annotations
            if ann.get("scope"):
                result_lines.append(f"    scope: {ann['scope']}")
            if ann.get("scope_write"):
                result_lines.append(f"    scope_write: {ann['scope_write']}")
            if ann.get("auth_type"):
                result_lines.append(f"    auth_type: {ann['auth_type']}")

            # Continue (don't re-add the current line, it's already handled)
            continue
        else:
            # Skip stray scope/scope_write/auth_type lines that are orphaned
            if re.match(r"^\s+(scope|scope_write|auth_type):", line):
                # Check if this line is inside a recognized endpoint block
                # If the previous non-blank line was a property at 4+ indent, keep it
                # Otherwise it's orphaned — skip it
                prev_meaningful = None
                for j in range(len(result_lines) - 1, -1, -1):
                    if result_lines[j].strip():
                        prev_meaningful = result_lines[j]
                        break
                if prev_meaningful and re.match(r"^    \w+", prev_meaningful):
                    result_lines.append(line)
                # else: skip orphaned scope line
                i += 1
                continue

            result_lines.append(line)
            i += 1

    yaml_file.write_text("\n".join(result_lines), encoding="utf-8")


def update_endpoints_yaml(yaml_path: str, discovered_endpoints: list[dict], output_json_path: Path):
    """Update endpoints.yaml with scope and auth_type from discovered endpoints."""
    try:
        from ruamel.yaml import YAML
        from ruamel.yaml.comments import CommentedMap
    except ImportError:
        print("\n  ERROR: ruamel.yaml not installed. Run: pip install ruamel.yaml")
        print("  Skipping YAML update.")
        return

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 120
    yaml.allow_duplicate_keys = True

    yaml_file = Path(yaml_path)
    if not yaml_file.exists():
        print(f"\n  ERROR: {yaml_path} not found")
        return

    print(f"\n=== Updating {yaml_path} ===")

    data = yaml.load(yaml_file.read_text(encoding="utf-8"))
    endpoints_section = data.get("endpoints", {})

    # Build discovered lookup (returns a match function)
    find_matches = match_yaml_to_discovered(yaml_path, discovered_endpoints)

    updated = 0
    unmatched_yaml = []

    for ep_name, ep_config in endpoints_section.items():
        if not isinstance(ep_config, (dict, CommentedMap)):
            continue
        path = ep_config.get("path")
        if not path:
            continue

        norm_path = normalize_path(path)
        matches = find_matches(norm_path)

        if matches:
            # Pick the best match (prefer GET for read endpoints)
            best = matches[0]
            for m in matches:
                if m.get("method") == "GET":
                    best = m
                    break

            if best.get("scope"):
                new_scope = best["scope"]
                if ep_config.get("scope") != new_scope:
                    # Insert scope right after path key for clean formatting
                    ep_config["scope"] = new_scope
                    updated += 1

                # Find write scope if there's a POST/PUT variant
                write_scopes = [m["scope"] for m in matches
                                if m.get("method") in ("POST", "PUT") and m.get("scope")
                                and m["scope"] != new_scope]
                if write_scopes:
                    ep_config["scope_write"] = write_scopes[0]
            else:
                if ep_config.get("auth_type") != "pat":
                    ep_config["auth_type"] = "pat"
                    updated += 1
        else:
            unmatched_yaml.append((ep_name, norm_path))

    # Write back using a clean approach: rebuild the file manually to avoid
    # ruamel.yaml's blank-line insertion issues
    if updated > 0:
        _write_yaml_clean(yaml_file, data, yaml)
        print(f"  Updated {updated} endpoints with scope/auth_type annotations")
    else:
        print(f"  No changes needed — all annotations up to date")

    # Report unmatched yaml entries
    if unmatched_yaml:
        print(f"\n  Endpoints in YAML not matched to docs ({len(unmatched_yaml)}):")
        for name, norm in unmatched_yaml[:15]:
            print(f"    {name:40s} ({norm})")
        if len(unmatched_yaml) > 15:
            print(f"    ... and {len(unmatched_yaml) - 15} more")

    # Discovery report: endpoints in docs but not in yaml
    yaml_paths = set()
    for ep_name, ep_config in endpoints_section.items():
        if isinstance(ep_config, dict) and ep_config.get("path"):
            yaml_paths.add(normalize_path(ep_config["path"]))

    missing_from_yaml = []
    for ep in discovered_endpoints:
        url = ep.get("url")
        if not url:
            continue
        norm = normalize_path(url)
        if norm not in yaml_paths and ep.get("scope"):
            missing_from_yaml.append(ep)

    # Deduplicate by normalized path
    seen_missing = set()
    unique_missing = []
    for ep in missing_from_yaml:
        norm = normalize_path(ep["url"])
        if norm not in seen_missing:
            seen_missing.add(norm)
            unique_missing.append(ep)

    if unique_missing:
        print(f"\n  === Coverage Gap Report ===")
        print(f"  Endpoints in Tableau docs NOT in your endpoints.yaml ({len(unique_missing)}):")
        print(f"  These may need adding if your migration tool should handle them:\n")
        for ep in unique_missing[:30]:
            m = ep.get("method") or "?"
            scope = ep.get("scope") or "PAT-only"
            print(f"    {m:<7s} {ep['url']:<55s} {ep['name']}")
            print(f"           scope: {scope}  category: {ep.get('category', '?')}")
        if len(unique_missing) > 30:
            print(f"    ... and {len(unique_missing) - 30} more")

        # Write coverage report to file
        report_path = output_json_path.with_stem(output_json_path.stem + "_coverage_gaps")
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_in_docs": len(discovered_endpoints),
            "total_in_yaml": len(yaml_paths),
            "missing_from_yaml": [
                {
                    "name": ep["name"],
                    "method": ep.get("method"),
                    "url": ep.get("url"),
                    "scope": ep.get("scope"),
                    "category": ep.get("category"),
                }
                for ep in unique_missing
            ],
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  Coverage gap report written to: {report_path}")
    else:
        print(f"\n  No coverage gaps — endpoints.yaml covers all scoped endpoints!")


def generate_full_yaml(output_yaml_path: str, discovered_endpoints: list[dict], existing_yaml_path: str | None = None):
    """
    Generate a complete endpoints.yaml with ALL discovered endpoints.
    Auto-detects: format, pagination, response_key, cache, parent, permissions_endpoint,
    ownership_transferable — entirely from crawled data and URL patterns.
    """
    print(f"\n=== Generating full endpoint registry: {output_yaml_path} ===")

    # Group discovered endpoints by category
    by_category = {}
    for ep in discovered_endpoints:
        cat = ep.get("category", "uncategorized")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(ep)

    # Build indexes for cross-referencing
    all_paths = set()
    paths_with_permissions = set()  # base resource paths that have a /permissions child
    paths_with_put = {}

    for ep in discovered_endpoints:
        url = ep.get("url", "")
        norm = normalize_path(url)
        all_paths.add(norm)
        if "/permissions" in norm:
            # The resource base is everything before /permissions
            base = norm.split("/permissions")[0]
            paths_with_permissions.add(base)
        if ep.get("method") == "PUT" and ep.get("scope"):
            paths_with_put[norm] = ep["scope"]

    # Helper: infer response_key from path
    def infer_response_key(path: str, fmt: str) -> str | None:
        """Derive response_key from the last resource segment of the URL."""
        # Strip parameters from end to get the resource collection
        segments = path.rstrip("/").split("/")
        # Find last non-parameter segment
        resource = None
        for seg in reversed(segments):
            if not seg.startswith("{") and seg not in ("api", "sites", "-"):
                resource = seg
                break
        if not resource:
            return None

        if fmt == "json":
            # JSON endpoints usually have the plural as the wrapper key
            return resource

        # XML: wrapper is plural.singular (e.g., users.user, workbooks.workbook)
        singular = resource.rstrip("s") if resource.endswith("s") and resource != "permissions" else resource
        # Handle special cases
        xml_singulars = {
            "datasources": "datasource",
            "workbooks": "workbook",
            "views": "view",
            "users": "user",
            "groups": "group",
            "projects": "project",
            "flows": "flow",
            "subscriptions": "subscription",
            "schedules": "schedule",
            "jobs": "job",
            "tasks": "task",
            "webhooks": "webhook",
            "favorites": "favorite",
            "customviews": "customView",
            "virtualconnections": "virtualConnection",
            "dataAlerts": "dataAlert",
            "permissions": "permissions",
            "revisions": "revision",
            "connections": "connection",
            "tags": "tag",
            "extracts": "extract",
        }
        singular = xml_singulars.get(resource, singular)
        if resource == "permissions":
            return "permissions"
        return f"{resource}.{singular}"

    # Helper: infer parent endpoint name
    def infer_parents(path: str, ep_key: str, all_ep_keys_by_path: dict) -> list[str]:
        """Find all parent list endpoints that provide IDs needed by this path."""
        segments = path.split("/")
        parents = []
        # Look for every {param} that indicates a dependency on a parent list endpoint
        for i, seg in enumerate(segments):
            if seg.startswith("{") and seg.endswith("}") and i > 0:
                parent_path = "/".join(segments[:i])
                if parent_path in all_ep_keys_by_path:
                    parent_name = all_ep_keys_by_path[parent_path]
                    if parent_name != ep_key and parent_name not in parents:
                        parents.append(parent_name)
        return parents

    # Helper: check if endpoint is a list (cacheable)
    def is_list_endpoint(path: str, method: str, scope: str | None) -> bool:
        """A list endpoint is a read operation on a collection (path doesn't end with {id})."""
        is_read = method == "GET" or (scope and ":read" in scope)
        if not is_read:
            return False
        segments = path.rstrip("/").split("/")
        if not segments:
            return False
        last = segments[-1]
        return not last.startswith("{")

    # Helper: normalize URL to path format
    def url_to_path(url: str) -> str:
        path = url
        path = re.sub(r"^\{server\}/api/-/", "-/", path)
        path = re.sub(r"^\{server\}/api/\d+\.\d+/", "", path)
        if "/sites/" in url:
            path = re.sub(r"^/api/api-version/", "", path)
        else:
            path = re.sub(r"^/api/api-version/", "", path)
        path = re.sub(r"site-id", "{site_id}", path)
        path = re.sub(r"site-luid", "{site_id}", path)
        path = re.sub(r"([a-z]+(?:-[a-z]+)*)-id\b", lambda m: "{" + m.group(1).replace("-", "_") + "_id}", path)
        path = re.sub(r"([a-z]+(?:-[a-z]+)*)-luid\b", lambda m: "{" + m.group(1).replace("-", "_") + "_luid}", path)
        path = re.sub(r"\b([a-z]+(?:-[a-z]+)*)-uuid\b", lambda m: "{" + m.group(1).replace("-", "_") + "_uuid}", path)
        path = path.strip("/")
        return path

    # First pass: build all endpoint entries with auto-detected attributes
    entries = []  # list of (category, ep_key, config_dict)
    ep_keys_by_path = {}  # normalized_path -> ep_key (for parent resolution)
    used_ep_names = set()

    for cat, endpoints in sorted(by_category.items()):
        for ep in endpoints:
            url = ep.get("url", "")
            method = ep.get("method", "GET")
            scope = ep.get("scope")
            name = ep.get("name", "Unknown")

            # Generate key name
            ep_key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            if ep_key in used_ep_names:
                ep_key = f"{ep_key}_{method.lower()}"
            if ep_key in used_ep_names:
                ep_key = f"{ep_key}_2"
            used_ep_names.add(ep_key)

            path = url_to_path(url)
            norm = normalize_path(url)

            # Auto-detect format
            fmt = "json" if path.startswith("-/") or "/-/" in url else "xml"

            # Auto-detect cache (GET list endpoints)
            cache = is_list_endpoint(path, method, scope)

            # Auto-detect pagination
            pagination = None
            if cache:
                pagination = "cursor" if fmt == "json" else "page"

            # Auto-detect response_key
            response_key = infer_response_key(path, fmt) if cache or method == "GET" else None

            # Auto-detect permissions_endpoint
            permissions_ep = None
            if cache and method == "GET":
                # A list endpoint like "sites/{site_id}/workbooks" has permissions at
                # "sites/{site_id}/workbooks/{workbook_id}/permissions"
                # Check if any path in paths_with_permissions starts with our list path + "/"
                for perm_base in paths_with_permissions:
                    if perm_base.startswith(norm + "/"):
                        # Build the permissions path template
                        permissions_ep = f"{path}/{{id}}/permissions"
                        break

            # Auto-detect ownership_transferable
            ownership = False
            if cache:
                for put_path, put_scope in paths_with_put.items():
                    if put_path.startswith(norm + "/") and "update" in put_scope:
                        ownership = True
                        break

            # Build config
            config = {"path": path, "method": method}
            if fmt == "json":
                config["format"] = "json"
            if response_key:
                config["response_key"] = response_key
            if cache:
                config["cache"] = True
            if pagination:
                config["pagination"] = pagination
            if permissions_ep:
                config["permissions_endpoint"] = permissions_ep
            if ownership:
                config["ownership_transferable"] = True
            if scope:
                config["scope"] = scope
            else:
                config["auth_type"] = "pat"

            entries.append((cat, ep_key, config, norm))
            if cache:
                ep_keys_by_path[norm] = ep_key

    # Second pass: resolve parent relationships
    for i, (cat, ep_key, config, norm) in enumerate(entries):
        path = config["path"]
        parents = infer_parents(norm, ep_key, ep_keys_by_path)
        if parents:
            if len(parents) == 1:
                config["parent"] = parents[0]
            else:
                config["parents"] = parents

    # Find write scopes for list endpoints
    scope_by_norm = {}
    for ep in discovered_endpoints:
        url = ep.get("url", "")
        norm = normalize_path(url)
        method = ep.get("method")
        scope = ep.get("scope")
        if scope and method in ("POST", "PUT"):
            if norm not in scope_by_norm:
                scope_by_norm[norm] = {}
            scope_by_norm[norm][method] = scope

    for i, (cat, ep_key, config, norm) in enumerate(entries):
        if config.get("scope") and norm in scope_by_norm:
            write_scopes = scope_by_norm[norm]
            post_scope = write_scopes.get("POST") or write_scopes.get("PUT")
            if post_scope and post_scope != config["scope"]:
                config["scope_write"] = post_scope

    # Third pass: compute execution priority via topological sort
    # Only endpoints relevant to migration/metadata get priority < 99.
    # Priority 1 = no parents, depended on by others (fetch first)
    # Higher priority = deeper in dependency chain
    # Priority 99 = cacheable but not migration-relevant or not depended on

    # Migration-relevant resource patterns: only these get real priorities
    MIGRATION_RELEVANT_PATTERNS = {
        "users", "groups", "groupsets", "sites", "projects",
        "workbooks", "views", "customviews", "datasources", "flows",
        "virtualconnections", "collections", "subscriptions",
        "dataAlerts", "data-driven-alerts", "favorites",
        "webhooks", "permissions", "databases", "tables",
        "pulse", "definitions", "metrics", "alerts",
        "personal-access-tokens", "schedules", "tasks",
        "revisions", "connections", "tags",
    }

    def is_migration_relevant(ep_key: str, config: dict) -> bool:
        """Check if an endpoint is a readable metadata source for dimension caching."""
        method = config.get("method", "")

        # Must be GET — only GET endpoints return cacheable metadata
        if method != "GET":
            return False

        # Must start with a read verb (get_, list_, query_)
        read_prefixes = ("get_", "list_", "query_")
        if not ep_key.startswith(read_prefixes):
            return False

        # Retired/deprecated endpoints get no priority
        if "retired" in ep_key or "deprecated" in ep_key:
            return False

        # Check if the resource is migration-relevant
        path = config.get("path", "").lower()
        for pattern in MIGRATION_RELEVANT_PATTERNS:
            if pattern in path:
                return True
        for pattern in MIGRATION_RELEVANT_PATTERNS:
            if pattern in ep_key:
                return True
        return False

    ep_key_to_idx = {ep_key: i for i, (_, ep_key, _, _) in enumerate(entries)}
    # Build "depended on by" reverse index (only for migration-relevant endpoints)
    depended_on_by = set()
    for _, ep_key, config, _ in entries:
        if not is_migration_relevant(ep_key, config):
            continue
        parent = config.get("parent")
        parents = config.get("parents", [])
        all_parents = ([parent] if parent else []) + (parents if isinstance(parents, list) else [])
        for p in all_parents:
            depended_on_by.add(p)

    # Compute priority levels using BFS from roots
    def get_parents_list(config: dict) -> list[str]:
        parent = config.get("parent")
        parents = config.get("parents", [])
        return ([parent] if parent else []) + (parents if isinstance(parents, list) else [])

    # Iterative priority assignment — only for migration-relevant endpoints
    priorities = {}
    for _, ep_key, config, _ in entries:
        if not is_migration_relevant(ep_key, config):
            continue
        parent_list = get_parents_list(config)
        if not parent_list and ep_key in depended_on_by:
            priorities[ep_key] = 1

    # Propagate priorities through dependency chain
    changed = True
    max_iterations = 20
    iteration = 0
    while changed and iteration < max_iterations:
        changed = False
        iteration += 1
        for _, ep_key, config, _ in entries:
            if ep_key in priorities:
                continue
            if not is_migration_relevant(ep_key, config):
                continue
            parent_list = get_parents_list(config)
            if not parent_list:
                continue
            parent_priorities = [priorities.get(p) for p in parent_list if p in priorities]
            if parent_priorities and len(parent_priorities) == len([p for p in parent_list if p in priorities]):
                my_priority = max(parent_priorities) + 1
                priorities[ep_key] = my_priority
                changed = True

    # Assign priorities to configs
    for _, ep_key, config, _ in entries:
        if ep_key in priorities:
            config["priority"] = priorities[ep_key]
        elif config.get("cache") and ep_key.startswith(("get_", "list_", "query_")):
            if "retired" in ep_key or "deprecated" in ep_key:
                pass  # no priority for retired endpoints
            elif not is_migration_relevant(ep_key, config):
                config["priority"] = 99
            elif ep_key not in depended_on_by:
                config["priority"] = 99

    # Promote batch_get/batch_list endpoints to match their non-batch sibling's priority
    priorities_by_key = {ep_key: config.get("priority") for _, ep_key, config, _ in entries if config.get("priority")}
    for _, ep_key, config, _ in entries:
        if config.get("priority") != 99:
            continue
        # Check if this is a batch read endpoint
        if not (ep_key.startswith("batch_get_") or ep_key.startswith("batch_list_")):
            continue
        # Find the non-batch sibling by matching the resource portion of the name
        # e.g., "batch_list_metric_definitions_few" -> look for "list_metric_definitions"
        # e.g., "batch_get_pulse_subscriptions" -> look for "list_subscriptions" or "list_pulse_subscriptions"
        resource_part = ep_key.replace("batch_get_", "get_").replace("batch_list_", "list_")
        # Strip trailing qualifiers like _few, _many, _post
        for suffix in ("_few", "_many", "_post", "_by_post"):
            resource_part = resource_part.removesuffix(suffix)
        # Also try list_ variant for batch_get_ endpoints
        list_variant = ep_key.replace("batch_get_", "list_").replace("batch_list_", "list_")
        for suffix in ("_few", "_many", "_post", "_by_post"):
            list_variant = list_variant.removesuffix(suffix)
        # Try removing resource qualifiers (e.g., "pulse_" prefix) for broader matching
        # "batch_get_pulse_subscriptions" -> try "list_subscriptions", "list_pulse_subscriptions"
        stripped_variants = [list_variant]
        # Remove common prefixes from the resource name
        for prefix in ("pulse_", "metric_", "insight_"):
            if prefix in list_variant:
                stripped_variants.append(list_variant.replace(prefix, ""))
        # Search for a matching sibling
        sibling_priority = None
        for sib_key, sib_pri in priorities_by_key.items():
            if sib_pri and sib_pri < 99:
                if (sib_key == resource_part or sib_key.startswith(resource_part)):
                    sibling_priority = sib_pri
                    break
                for variant in stripped_variants:
                    if sib_key == variant or sib_key.startswith(variant):
                        sibling_priority = sib_pri
                        break
                if sibling_priority:
                    break
        if sibling_priority:
            config["priority"] = sibling_priority

    # Generate YAML output
    lines = []
    lines.append("# =============================================================================")
    lines.append("# TABLEAU REST API — COMPLETE ENDPOINT REGISTRY")
    lines.append("# =============================================================================")
    lines.append(f"# Auto-generated by discover_tableau_endpoints.py")
    lines.append(f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"# Total endpoints: {len(entries)}")
    lines.append("#")
    lines.append("# All attributes auto-detected from Tableau REST API documentation.")
    lines.append("# Re-run discover_tableau_endpoints.py --generate-full-yaml to refresh.")
    lines.append("#")
    lines.append("# Schema:")
    lines.append("#   path:            URL pattern (runtime variables in {braces})")
    lines.append("#   method:          HTTP method (GET, POST, PUT, DELETE, PATCH)")
    lines.append("#   format:          json | xml (auto-detected from URL pattern)")
    lines.append("#   response_key:    response wrapper path (auto-detected from resource name)")
    lines.append("#   cache:           true for GET list endpoints (pre-fetch at startup)")
    lines.append("#   pagination:      cursor (JSON) or page (XML) for list endpoints")
    lines.append("#   priority:        execution order (1=fetch first, 2=depends on 1, 99=optional)")
    lines.append("#   parent:          parent list endpoint for child lookups (single)")
    lines.append("#   parents:         parent list endpoints (multiple dependencies)")
    lines.append("#   permissions_endpoint: path template for permission operations")
    lines.append("#   ownership_transferable: true if PUT with :update scope exists")
    lines.append("#   scope:           JWT scope for read/GET operations")
    lines.append("#   scope_write:     JWT scope for write/POST/PUT operations")
    lines.append("#   auth_type:       'pat' if no JWT scope (default: jwt)")
    lines.append("# =============================================================================")
    lines.append("")
    lines.append("endpoints:")
    lines.append("")

    cat_names = {
        "users_and_groups": "USERS AND GROUPS",
        "projects": "PROJECTS",
        "workbooks_and_views": "WORKBOOKS AND VIEWS",
        "data_sources": "DATA SOURCES",
        "flow": "FLOWS",
        "virtual_connections": "VIRTUAL CONNECTIONS",
        "collections": "COLLECTIONS",
        "pulse": "PULSE",
        "subscriptions": "SUBSCRIPTIONS",
        "notifications": "NOTIFICATIONS AND ALERTS",
        "favorites": "FAVORITES",
        "permissions": "PERMISSIONS",
        "metadata": "METADATA",
        "jobs,_tasks,_and_schedules": "JOBS, TASKS, AND SCHEDULES",
        "publishing": "PUBLISHING",
        "extract_and_encryption": "EXTRACT AND ENCRYPTION",
        "revisions": "REVISIONS",
        "recycle_bin": "RECYCLE BIN",
        "authentication": "AUTHENTICATION",
        "server": "SERVER",
        "site": "SITE",
        "bridge": "BRIDGE",
        "connected_app": "CONNECTED APPS",
        "custom_domain": "CUSTOM DOMAINS",
        "content_exploration": "CONTENT EXPLORATION",
        "identity_pools": "IDENTITY POOLS",
        "mobile_settings": "MOBILE SETTINGS",
        "openid_connect": "OPENID CONNECT",
        "tableau_extensions_settings": "TABLEAU EXTENSIONS",
        "tableau_mcp": "TABLEAU MCP",
        "webhooks": "WEBHOOKS",
    }

    current_cat = None
    for cat, ep_key, config, norm in entries:
        if cat != current_cat:
            current_cat = cat
            display = cat_names.get(cat, cat.upper().replace("_", " "))
            lines.append(f"  # {'=' * 75}")
            lines.append(f"  # {display}")
            lines.append(f"  # {'=' * 75}")
            lines.append("")

        lines.append(f"  {ep_key}:")
        # Write config in a logical order
        key_order = ["path", "method", "format", "response_key", "cache", "pagination",
                     "priority", "parent", "parents", "permissions_endpoint",
                     "ownership_transferable", "params", "primary_key", "single_object",
                     "scope", "scope_write", "auth_type"]
        for k in key_order:
            if k not in config:
                continue
            v = config[k]
            if isinstance(v, list):
                lines.append(f"    {k}:")
                for item in v:
                    lines.append(f"      - {item}")
            elif isinstance(v, bool):
                lines.append(f"    {k}: {'true' if v else 'false'}")
            elif isinstance(v, str) and (" " in v or "{" in v):
                lines.append(f'    {k}: "{v}"')
            else:
                lines.append(f"    {k}: {v}")
        lines.append("")

    # Defaults
    lines.append("# =============================================================================")
    lines.append("# DEFAULTS")
    lines.append("# =============================================================================")
    lines.append("defaults:")
    lines.append("  page_size: 100")
    lines.append("  site_scoped: true")
    lines.append("  format: xml")
    lines.append("")

    out_path = Path(output_yaml_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # Stats
    cached = sum(1 for _, _, c, _ in entries if c.get("cache"))
    with_parent = sum(1 for _, _, c, _ in entries if c.get("parent") or c.get("parents"))
    with_perms = sum(1 for _, _, c, _ in entries if c.get("permissions_endpoint"))
    with_ownership = sum(1 for _, _, c, _ in entries if c.get("ownership_transferable"))
    print(f"  Written {len(entries)} endpoints to {out_path}")
    print(f"    Cacheable (list endpoints): {cached}")
    print(f"    With parent relationship:   {with_parent}")
    print(f"    With permissions endpoint:  {with_perms}")
    print(f"    Ownership transferable:     {with_ownership}")

    # Priority group summary
    from collections import defaultdict
    priority_groups = defaultdict(list)
    for _, ep_key, config, _ in entries:
        pri = config.get("priority")
        if pri is not None:
            priority_groups[pri].append(ep_key)

    print(f"\n  === Priority Groups ===")
    for pri in sorted(priority_groups.keys()):
        eps_in_group = priority_groups[pri]
        if pri == 99:
            label = "OPTIONAL (cacheable but no dependents)"
        elif pri == 1:
            label = "ROOTS (no dependencies, fetch first)"
        else:
            label = f"LEVEL {pri} (depends on level {pri - 1})"
        print(f"\n  Priority {pri} — {label} ({len(eps_in_group)} endpoints):")
        for ep in sorted(eps_in_group):
            print(f"    - {ep}")

    no_pri = [ep_key for _, ep_key, c, _ in entries if not c.get("priority")]
    if no_pri:
        print(f"\n  No priority — WRITE/ACTION endpoints ({len(no_pri)} endpoints):")
        for ep in sorted(no_pri)[:20]:
            print(f"    - {ep}")
        if len(no_pri) > 20:
            print(f"    ... and {len(no_pri) - 20} more")

    # Also write priority summary to a separate file
    priority_summary_path = out_path.with_stem(out_path.stem + "_priority_groups")
    priority_summary_path = priority_summary_path.with_suffix(".json")
    summary_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "priority_groups": {
            str(pri): {
                "label": "ROOTS (no dependencies, fetch first)" if pri == 1
                         else "OPTIONAL (cacheable but no dependents)" if pri == 99
                         else f"LEVEL {pri} (depends on level {pri - 1})",
                "count": len(eps_list),
                "endpoints": sorted(eps_list),
            }
            for pri, eps_list in sorted(priority_groups.items())
        },
        "no_priority": {
            "label": "WRITE/ACTION endpoints (no caching needed)",
            "count": len(no_pri),
            "endpoints": sorted(no_pri),
        },
    }
    priority_summary_path.write_text(json.dumps(summary_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Priority summary written to: {priority_summary_path}")

    # Generate scopes.yaml — deduplicated wildcard scopes for JWT authentication
    raw_scopes = set()
    for _, _, config, _ in entries:
        for scope_key in ("scope", "scope_write"):
            scope_val = config.get(scope_key)
            if scope_val:
                # Convert "tableau:resource:read" → "tableau:resource:*"
                parts = scope_val.rsplit(":", 1)
                if len(parts) == 2:
                    raw_scopes.add(f"{parts[0]}:*")
                else:
                    raw_scopes.add(scope_val)

    sorted_scopes = sorted(raw_scopes)
    scopes_lines = [
        "# =============================================================================",
        "# JWT SCOPES — AUTO-GENERATED FROM TABLEAU REST API DOCUMENTATION",
        "# =============================================================================",
        f"# Generated by discover_tableau_endpoints.py",
        f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"# Total scopes: {len(sorted_scopes)}",
        "#",
        "# These are wildcard scopes (tableau:resource:*) derived from all discovered",
        "# endpoint scopes. Used by auth.py for JWT token generation.",
        "# Re-run discover_tableau_endpoints.py --generate-full-yaml to refresh.",
        "# =============================================================================",
        "",
        "scopes:",
    ]
    for s in sorted_scopes:
        scopes_lines.append(f'  - "{s}"')

    scopes_path = out_path.parent / "scopes.yaml"
    scopes_path.write_text("\n".join(scopes_lines) + "\n", encoding="utf-8")
    print(f"\n  Scopes written to: {scopes_path} ({len(sorted_scopes)} unique scopes)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\nFATAL ERROR: {e}")
        traceback.print_exc()
        input("\nPress Enter to exit...")
        raise SystemExit(1)

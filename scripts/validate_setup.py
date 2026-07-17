#!/usr/bin/env python3
import sys
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _print_check(label: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return passed


def check_env_file() -> bool:
    env_path = PROJECT_ROOT / ".env"
    return _print_check(".env file exists", env_path.exists(), str(env_path))


def check_dependencies() -> bool:
    missing = []
    for module in ("httpx", "dotenv", "jwt", "cryptography", "yaml"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    passed = len(missing) == 0
    detail = f"missing: {', '.join(missing)}" if missing else "all installed"
    return _print_check("Python dependencies", passed, detail)


def check_settings() -> tuple:
    try:
        from config.settings import Settings
        settings = Settings.from_environment()
        _print_check("Settings loaded from .env", True)
        return True, settings
    except Exception as e:
        _print_check("Settings loaded from .env", False, str(e))
        return False, None


def check_auth(settings) -> bool:
    if settings is None:
        return _print_check("Auth credentials", False, "settings not loaded")
    has_jwt = settings.auth.has_jwt
    has_pat = settings.auth.has_pat
    if has_jwt and has_pat:
        detail = "JWT (primary) + PAT (fallback)"
    elif has_jwt:
        detail = "JWT only (no PAT fallback)"
    elif has_pat:
        detail = "PAT only (no JWT)"
    else:
        return _print_check("Auth credentials", False, "no JWT or PAT configured")
    return _print_check("Auth credentials", True, detail)


def check_validation(settings) -> bool:
    if settings is None:
        return _print_check("Settings validation", False, "settings not loaded")
    try:
        settings.validate()
        return _print_check("Settings validation", True)
    except Exception as e:
        return _print_check("Settings validation", False, str(e))


def check_csv(settings) -> bool:
    if settings is None or settings.paths.csv_location is None:
        return _print_check("CSV file", False, "no CSV path configured")
    csv_path = settings.paths.csv_location
    if not csv_path.exists():
        return _print_check("CSV file", False, f"not found: {csv_path}")
    try:
        from src.utils.csv_loader import load_user_mappings
        mappings = load_user_mappings(csv_path)
        return _print_check("CSV file", True, f"{len(mappings)} valid mappings")
    except Exception as e:
        return _print_check("CSV file", False, str(e))


def check_endpoints_config() -> bool:
    config_path = PROJECT_ROOT / "config" / "endpoints.yaml"
    if not config_path.exists():
        return _print_check("endpoints.yaml", False, f"not found: {config_path}")
    try:
        import yaml
        with open(config_path) as f:
            data = yaml.safe_load(f)
        types = data.get("content_types", {})
        return _print_check("endpoints.yaml", True, f"{len(types)} content types defined")
    except Exception as e:
        return _print_check("endpoints.yaml", False, str(e))


def check_directories(settings) -> bool:
    if settings is None:
        return _print_check("Output directories", False, "settings not loaded")
    passed = True
    log_loc = settings.paths.log_location
    if log_loc:
        try:
            log_loc.mkdir(parents=True, exist_ok=True)
            _print_check(f"Log directory: {log_loc}", True)
        except Exception as e:
            _print_check(f"Log directory: {log_loc}", False, str(e))
            passed = False

    checkpoint_dir = settings.paths.checkpoint_dir
    if checkpoint_dir:
        try:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            _print_check(f"Checkpoint directory: {checkpoint_dir}", True)
        except Exception as e:
            _print_check(f"Checkpoint directory: {checkpoint_dir}", False, str(e))
            passed = False

    cache_dir = settings.cache.cache_dir
    if cache_dir:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            _print_check(f"Cache directory: {cache_dir}", True)
        except Exception as e:
            _print_check(f"Cache directory: {cache_dir}", False, str(e))
            passed = False

    return passed


async def check_connectivity(settings) -> bool:
    if settings is None:
        return _print_check("Server connectivity", False, "settings not loaded")
    try:
        import httpx
        async with httpx.AsyncClient(http2=False, timeout=10.0) as client:
            url = f"{settings.api.server_url}/api/{settings.api.api_version}/serverinfo"
            response = await client.get(url)
            if response.status_code == 200:
                return _print_check("Server connectivity", True, settings.api.server_url)
            else:
                return _print_check("Server connectivity", False, f"HTTP {response.status_code}")
    except Exception as e:
        return _print_check("Server connectivity", False, str(e))


def main():
    print("=" * 60)
    print("Tableau Cloud User Migrate Tool v2 — Setup Validator")
    print("=" * 60)
    print()

    results = []

    print("1. Environment")
    results.append(check_env_file())
    results.append(check_dependencies())
    print()

    print("2. Configuration")
    settings_ok, settings = check_settings()
    results.append(settings_ok)
    results.append(check_auth(settings))
    results.append(check_validation(settings))
    results.append(check_endpoints_config())
    print()

    print("3. Data")
    results.append(check_csv(settings))
    print()

    print("4. Directories")
    results.append(check_directories(settings))
    print()

    print("5. Connectivity")
    results.append(asyncio.run(check_connectivity(settings)))
    print()

    passed = sum(1 for r in results if r)
    total = len(results)
    failed = total - passed

    print("=" * 60)
    if failed == 0:
        print(f"All {passed} checks passed. Ready to run.")
        print()
        print("Next step:")
        print("  python -m src.main --mode dry-run")
    else:
        print(f"{passed}/{total} checks passed. {failed} failed.")
        print()
        print("Fix the FAIL items above before running.")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

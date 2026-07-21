# Tableau REST API base client with rate limiting and per-request delay
# Co-authored with CoCo
import asyncio
import json
import random
import time
from pathlib import Path
from typing import Optional

import httpx

from config.settings import Settings
from src.api.auth import TableauAuthenticator
from src.utils.exceptions import APIError, RateLimitError, AuthenticationError
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_auth_lock = asyncio.Lock()
_auth_generation: int = 0

_pat_auth_lock = asyncio.Lock()
_pat_auth_generation: int = 0

# PAT-only endpoint patterns: these endpoints have no JWT scope and require PAT auth.
# Loaded from tableau_endpoints.json if available, otherwise uses this hardcoded fallback.
_PAT_ONLY_PATTERNS: list[str] = [
    "/-/authn-service/",
    "/-/settings/server/extensions/dashboard",
    "/-/settings/site/extensions/dashboard",
    "/auth/signin",
    "/auth/signout",
    "/-/openid/",
    "/schedules/",  # List Extract Refresh Tasks in Server Schedule
]


def _load_pat_patterns(endpoints_json_path: str | None = None) -> list[str]:
    """Load PAT-only endpoint URL patterns from tableau_endpoints.json."""
    search_paths = [
        endpoints_json_path,
        "output/tableau_endpoints.json",
        "output/tableau_scopes.json",
    ]
    for path in search_paths:
        if not path:
            continue
        p = Path(path)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                pat_endpoints = data.get("pat_required", [])
                patterns = []
                for ep in pat_endpoints:
                    url = ep.get("url")
                    if url and url != "?":
                        # Extract the path portion after {server} or /api/api-version
                        clean = url.replace("{server}", "")
                        # Remove /api/api-version prefix for matching
                        clean = clean.replace("/api/api-version", "")
                        if clean and len(clean) > 3:
                            patterns.append(clean)
                if patterns:
                    logger.info(f"Loaded {len(patterns)} PAT-only endpoint patterns from {p}")
                    return patterns
            except (json.JSONDecodeError, KeyError) as e:
                logger.debug(f"Could not load PAT patterns from {p}: {e}")
    return _PAT_ONLY_PATTERNS


class RateLimiter:
    def __init__(self, max_concurrent: int = 10, rps: float = 10.0):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._rps = rps
        self._min_interval = 1.0 / rps if rps > 0 else 0
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def release(self) -> None:
        self._semaphore.release()


class BaseTableauClient:
    def __init__(self, auth: TableauAuthenticator, settings: Settings):
        self._auth = auth
        self._settings = settings
        self._api_delay_s = settings.api.api_delay_ms / 1000.0
        self._rate_limiter = RateLimiter(
            max_concurrent=int(settings.api.rate_limit_rps),
            rps=float(settings.api.rate_limit_rps),
        )
        self._client = httpx.AsyncClient(
            http2=False,
            timeout=httpx.Timeout(
                connect=settings.api.connect_timeout,
                read=settings.api.read_timeout,
                write=30.0,
                pool=30.0,
            ),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
            follow_redirects=True,
        )
        self._request_count = 0
        self._retry_count = 0

        # Dual-session: separate PAT authenticator for PAT-only endpoints
        self._pat_auth: Optional[TableauAuthenticator] = None
        self._pat_patterns: list[str] = _load_pat_patterns()
        self._pat_routed_count = 0

    def _init_pat_session(self) -> None:
        """Lazily initialize PAT auth session (only if PAT credentials exist)."""
        if self._pat_auth is not None:
            return
        if not self._settings.auth.has_pat:
            return
        # Create a separate authenticator instance for PAT-only requests
        from config.settings import AuthConfig
        self._pat_auth = TableauAuthenticator(self._settings.auth, self._settings.api)

    def _is_pat_only(self, endpoint: str) -> bool:
        """Check if an endpoint requires PAT auth (no JWT scope available)."""
        for pattern in self._pat_patterns:
            if pattern in endpoint:
                return True
        return False

    @property
    def auth(self) -> TableauAuthenticator:
        return self._auth

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._client

    @property
    def stats(self) -> dict:
        return {
            "total_requests": self._request_count,
            "total_retries": self._retry_count,
            "pat_routed_requests": self._pat_routed_count,
        }

    def _build_url(self, endpoint: str) -> str:
        base = self._settings.api.server_url
        version = self._settings.api.api_version
        if endpoint.startswith("/api/"):
            return f"{base}{endpoint}"
        if endpoint.startswith("/-/"):
            return f"{base}/api{endpoint}"
        return f"{base}/api/{version}{endpoint}"

    def _calculate_backoff(self, attempt: int) -> float:
        base = self._settings.api.retry_backoff_base
        wait = base ** attempt
        if self._settings.api.retry_jitter:
            wait *= (0.5 + random.random())
        return min(wait, 120.0)

    def _parse_retry_after(self, response: httpx.Response) -> Optional[float]:
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    async def request(
        self,
        method: str,
        endpoint: str,
        content: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> httpx.Response:
        url = self._build_url(endpoint)
        max_retries = self._settings.api.max_retries
        last_exception = None

        # Route to PAT auth for endpoints that don't support JWT scopes
        use_pat = self._is_pat_only(endpoint) and self._settings.auth.has_pat
        if use_pat:
            self._init_pat_session()
            auth = self._pat_auth
            self._pat_routed_count += 1
            logger.debug(f"PAT-routed: {method} {endpoint}")
        else:
            auth = self._auth

        for attempt in range(max_retries + 1):
            await self._rate_limiter.acquire()
            try:
                if self._api_delay_s > 0:
                    await asyncio.sleep(self._api_delay_s)
                await auth.ensure_valid_token(self._client)
                auth_headers = auth.get_auth_headers()

                request_headers = {
                    **auth_headers,
                }
                if headers:
                    request_headers.update(headers)
                if "Content-Type" not in request_headers:
                    request_headers["Content-Type"] = "application/xml"
                if "Accept" not in request_headers:
                    request_headers["Accept"] = "application/xml"

                self._request_count += 1
                response = await self._client.request(
                    method,
                    url,
                    content=content,
                    headers=request_headers,
                )

                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    wait = retry_after if retry_after else self._calculate_backoff(attempt)
                    self._retry_count += 1
                    print_status("RETRY", f"429 rate limited, waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries + 1})")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code == 401 and attempt < max_retries:
                    if use_pat:
                        global _pat_auth_generation
                        gen_before = _pat_auth_generation
                        async with _pat_auth_lock:
                            if _pat_auth_generation == gen_before:
                                self._retry_count += 1
                                print_status("RETRY", "401 PAT auth expired, re-authenticating")
                                await auth.authenticate_pat(self._client)
                                _pat_auth_generation += 1
                            else:
                                logger.debug("401 (PAT) handled by another request, using refreshed token")
                    else:
                        global _auth_generation
                        gen_before = _auth_generation
                        async with _auth_lock:
                            if _auth_generation == gen_before:
                                self._retry_count += 1
                                print_status("RETRY", "401 auth expired, re-authenticating")
                                if auth.auth_method == "jwt" and self._settings.auth.has_pat:
                                    await auth.authenticate_pat(self._client)
                                else:
                                    await auth.authenticate(self._client)
                                _auth_generation += 1
                            else:
                                logger.debug("401 handled by another request, using refreshed token")
                    continue

                if response.status_code == 403 and not use_pat and self._settings.auth.has_pat:
                    # JWT scope might not cover this endpoint — try PAT fallback
                    logger.warning(f"403 on JWT for {method} {endpoint}, falling back to PAT")
                    self._init_pat_session()
                    await self._pat_auth.ensure_valid_token(self._client)
                    pat_headers = {**self._pat_auth.get_auth_headers()}
                    if headers:
                        pat_headers.update(headers)
                    if "Content-Type" not in pat_headers:
                        pat_headers["Content-Type"] = "application/xml"
                    if "Accept" not in pat_headers:
                        pat_headers["Accept"] = "application/xml"

                    self._request_count += 1
                    self._pat_routed_count += 1
                    response = await self._client.request(
                        method, url, content=content, headers=pat_headers,
                    )
                    if response.status_code >= 400:
                        raise APIError(
                            f"API error {response.status_code} (PAT fallback): {response.text[:500]}",
                            status_code=response.status_code,
                            response_body=response.text,
                        )
                    return response

                if response.status_code == 403:
                    raise APIError(
                        f"403 Forbidden: {response.text[:300]}",
                        status_code=403,
                        response_body=response.text,
                    )

                if response.status_code in (500, 502, 503, 504) and attempt < max_retries:
                    wait = self._calculate_backoff(attempt)
                    self._retry_count += 1
                    print_status("RETRY", f"{response.status_code} server error, waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries + 1})")
                    await asyncio.sleep(wait)
                    continue

                if response.status_code >= 400:
                    raise APIError(
                        f"API error {response.status_code}: {response.text[:500]}",
                        status_code=response.status_code,
                        response_body=response.text,
                    )

                return response

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
                last_exception = e
                if attempt < max_retries:
                    wait = self._calculate_backoff(attempt)
                    self._retry_count += 1
                    print_status("RETRY", f"Connection error: {e}, waiting {wait:.1f}s (attempt {attempt + 1}/{max_retries + 1})")
                    await asyncio.sleep(wait)
                    continue
                raise APIError(f"Connection failed after {max_retries + 1} attempts: {e}") from e
            except APIError:
                raise
            except Exception as e:
                last_exception = e
                if attempt < max_retries:
                    wait = self._calculate_backoff(attempt)
                    self._retry_count += 1
                    await asyncio.sleep(wait)
                    continue
                raise
            finally:
                self._rate_limiter.release()

        raise APIError(f"Request failed after {max_retries + 1} attempts") from last_exception

    async def close(self) -> None:
        try:
            await self._auth.sign_out(self._client)
        except Exception:
            pass
        if self._pat_auth:
            try:
                await self._pat_auth.sign_out(self._client)
            except Exception:
                pass
        await self._client.aclose()

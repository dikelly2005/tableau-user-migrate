import asyncio
import random
import time
from typing import Optional

import httpx

from config.settings import Settings
from src.api.auth import TableauAuthenticator
from src.utils.exceptions import APIError, RateLimitError, AuthenticationError
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_auth_lock = asyncio.Lock()


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
        self._rate_limiter = RateLimiter(
            max_concurrent=10,
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

        for attempt in range(max_retries + 1):
            await self._rate_limiter.acquire()
            try:
                await self._auth.ensure_valid_token(self._client)
                auth_headers = self._auth.get_auth_headers()

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

                if response.status_code == 401 and attempt == 0:
                    async with _auth_lock:
                        self._retry_count += 1
                        print_status("RETRY", "401 auth expired, re-authenticating")
                        if self._auth.auth_method == "jwt" and self._settings.auth.has_pat:
                            await self._auth.authenticate_pat(self._client)
                        else:
                            await self._auth.authenticate(self._client)
                    continue

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
        await self._client.aclose()

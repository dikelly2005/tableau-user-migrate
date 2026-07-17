# Tableau Cloud authentication with externalized scopes and phase-boundary refresh
# Co-authored with CoCo
import asyncio
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

import httpx
import jwt
import yaml

from config.settings import Settings, AuthConfig, ApiConfig
from src.utils.exceptions import AuthenticationError
from src.utils.logging_config import get_logger, print_status

logger = get_logger(__name__)

_JWT_EXPIRY_MINUTES = 5
_DEFAULT_SESSION_DURATION_SECONDS = 2 * 60 * 60
_IDLE_TIMEOUT_SECONDS = 30 * 60


def load_scopes(scopes_path: Optional[Path] = None) -> List[str]:
    if scopes_path is None:
        scopes_path = Path(__file__).resolve().parent.parent.parent / "config" / "scopes.yaml"
    with open(scopes_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("scopes", [])


class TableauAuthenticator:
    def __init__(self, auth_config: AuthConfig, api_config: ApiConfig, scopes: Optional[List[str]] = None):
        self._auth_config = auth_config
        self._api_config = api_config
        self._scopes = scopes or load_scopes()
        self._session_duration_seconds = getattr(api_config, 'session_duration_seconds', _DEFAULT_SESSION_DURATION_SECONDS)
        self._rest_api_token: Optional[str] = None
        self._rest_api_token_expiry: Optional[datetime] = None
        self._site_id: Optional[str] = None
        self._user_id: Optional[str] = None
        self._auth_method: Optional[str] = None
        self._last_request_time: Optional[datetime] = None

    @property
    def site_id(self) -> str:
        if not self._site_id:
            raise AuthenticationError("Not authenticated — call authenticate() first")
        return self._site_id

    @property
    def user_id(self) -> str:
        if not self._user_id:
            raise AuthenticationError("Not authenticated — call authenticate() first")
        return self._user_id

    @property
    def auth_method(self) -> Optional[str]:
        return self._auth_method

    @property
    def is_authenticated(self) -> bool:
        return self._rest_api_token is not None

    def _is_token_expired(self) -> bool:
        if not self._rest_api_token or not self._rest_api_token_expiry:
            return True
        threshold = timedelta(seconds=self._auth_config.token_refresh_threshold_seconds)
        return datetime.now(timezone.utc) >= (self._rest_api_token_expiry - threshold)

    def _create_jwt_token(self) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "iss": self._auth_config.jwt_client_id,
            "exp": now + timedelta(minutes=_JWT_EXPIRY_MINUTES),
            "jti": str(uuid.uuid4()),
            "aud": "tableau",
            "sub": self._auth_config.jwt_username,
            "scp": self._scopes,
        }
        headers = {
            "kid": self._auth_config.jwt_secret_id,
            "iss": self._auth_config.jwt_client_id,
        }
        return jwt.encode(
            payload,
            self._auth_config.jwt_secret_value,
            algorithm="HS256",
            headers=headers,
        )

    async def authenticate_jwt(self, client: httpx.AsyncClient) -> None:
        jwt_token = self._create_jwt_token()
        url = f"{self._api_config.server_url}/api/{self._api_config.api_version}/auth/signin"
        payload = (
            '<tsRequest>'
            '<credentials jwt="{jwt}">'
            '<site contentUrl="{site}"/>'
            '</credentials>'
            '</tsRequest>'
        ).format(jwt=jwt_token, site=self._api_config.site_name)

        response = await client.post(
            url,
            content=payload,
            headers={"Content-Type": "application/xml", "Accept": "application/xml"},
        )

        if response.status_code != 200:
            raise AuthenticationError(f"JWT auth failed: {response.status_code} {response.text[:500]}")

        self._parse_auth_response(response.text)
        self._auth_method = "jwt"
        self._rest_api_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=self._session_duration_seconds)
        print_status("AUTH", f"JWT authenticated as {self._auth_config.jwt_username} (site_id={self._site_id})")

    async def authenticate_pat(self, client: httpx.AsyncClient) -> None:
        url = f"{self._api_config.server_url}/api/{self._api_config.api_version}/auth/signin"
        payload = (
            '<tsRequest>'
            '<credentials personalAccessTokenName="{name}" personalAccessTokenSecret="{secret}">'
            '<site contentUrl="{site}"/>'
            '</credentials>'
            '</tsRequest>'
        ).format(
            name=self._auth_config.pat_token_name,
            secret=self._auth_config.pat_token_secret,
            site=self._api_config.site_name,
        )

        response = await client.post(
            url,
            content=payload,
            headers={"Content-Type": "application/xml", "Accept": "application/xml"},
        )

        if response.status_code != 200:
            raise AuthenticationError(f"PAT auth failed: {response.status_code} {response.text[:500]}")

        self._parse_auth_response(response.text)
        self._auth_method = "pat"
        self._rest_api_token_expiry = datetime.now(timezone.utc) + timedelta(seconds=self._session_duration_seconds)
        print_status("AUTH", f"PAT authenticated (site_id={self._site_id})")

    def _parse_auth_response(self, xml_text: str) -> None:
        import xml.etree.ElementTree as ET
        ns = {"t": "http://tableau.com/api"}
        root = ET.fromstring(xml_text)

        cred = root.find(".//t:credentials", ns)
        if cred is None:
            cred = root.find(".//credentials")
        if cred is None:
            for elem in root.iter():
                if elem.tag.endswith("credentials"):
                    cred = elem
                    break

        if cred is None:
            raise AuthenticationError(f"Could not parse auth response: no credentials element")

        self._rest_api_token = cred.get("token")
        if not self._rest_api_token:
            raise AuthenticationError("Auth response missing token attribute")

        site_elem = cred.find(".//{http://tableau.com/api}site")
        if site_elem is None:
            for elem in cred.iter():
                if elem.tag.endswith("site"):
                    site_elem = elem
                    break
        if site_elem is not None:
            self._site_id = site_elem.get("id")

        user_elem = cred.find(".//{http://tableau.com/api}user")
        if user_elem is None:
            for elem in cred.iter():
                if elem.tag.endswith("user"):
                    user_elem = elem
                    break
        if user_elem is not None:
            self._user_id = user_elem.get("id")

    async def authenticate(self, client: httpx.AsyncClient) -> None:
        if self._auth_config.has_jwt:
            try:
                await self.authenticate_jwt(client)
                return
            except AuthenticationError:
                logger.warning("JWT auth failed, falling back to PAT")
                if not self._auth_config.has_pat:
                    raise

        if self._auth_config.has_pat:
            await self.authenticate_pat(client)
            return

        raise AuthenticationError("No auth credentials configured (need JWT or PAT)")

    async def ensure_valid_token(self, client: httpx.AsyncClient) -> None:
        if not self._is_token_expired():
            return
        print_status("AUTH", "Token expired or near expiry, re-authenticating")
        await self.authenticate(client)

    async def ensure_token_for_phase(self, client: httpx.AsyncClient, phase_name: str) -> None:
        if self._is_token_expired():
            print_status("AUTH", f"Refreshing token before phase: {phase_name}")
            await self.authenticate(client)
        else:
            remaining = (self._rest_api_token_expiry - datetime.now(timezone.utc)).total_seconds()
            logger.debug(f"Token valid for {remaining:.0f}s — proceeding with phase: {phase_name}")

    def get_auth_headers(self) -> dict[str, str]:
        if not self._rest_api_token:
            raise AuthenticationError("Not authenticated")
        return {"X-Tableau-Auth": self._rest_api_token}

    async def sign_out(self, client: httpx.AsyncClient) -> None:
        if not self._rest_api_token:
            return
        url = f"{self._api_config.server_url}/api/{self._api_config.api_version}/auth/signout"
        try:
            await client.post(url, headers=self.get_auth_headers())
        except Exception:
            pass
        self._rest_api_token
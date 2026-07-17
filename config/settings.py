# Tableau user migration configuration with env-based settings
# Co-authored with CoCo
import os
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

from src.utils.exceptions import ValidationError, ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigurationError(f"{key} must be an integer, got: {raw!r}")


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigurationError(f"{key} must be a number, got: {raw!r}")


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.lower() not in ("false", "0", "no")


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


@dataclass
class AuthConfig:
    jwt_client_id: Optional[str] = None
    jwt_secret_id: Optional[str] = None
    jwt_secret_value: Optional[str] = None
    jwt_username: Optional[str] = None
    pat_token_name: Optional[str] = None
    pat_token_secret: Optional[str] = None
    token_refresh_threshold_seconds: int = 300

    @property
    def has_jwt(self) -> bool:
        return all([self.jwt_client_id, self.jwt_secret_id, self.jwt_secret_value, self.jwt_username])

    @property
    def has_pat(self) -> bool:
        return all([self.pat_token_name, self.pat_token_secret])


@dataclass
class ApiConfig:
    server_url: str = ""
    site_name: str = ""
    api_version: str = "3.19"
    api_delay_ms: int = 100
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    retry_initial_wait: float = 1.0
    retry_jitter: bool = True
    rate_limit_rps: int = 10
    connect_timeout: float = 60.0
    read_timeout: float = 300.0
    session_duration_seconds: int = 7200


@dataclass
class CacheConfig:
    ttl_hours: int = 24
    cache_dir: Optional[Path] = None
    enabled: bool = True


@dataclass
class PathConfig:
    csv_location: Optional[Path] = None
    log_location: Optional[Path] = None

    @property
    def checkpoint_dir(self) -> Optional[Path]:
        if self.log_location:
            return self.log_location / "checkpoints"
        return None


@dataclass
class Settings:
    auth: AuthConfig = field(default_factory=AuthConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    mode: str = "dry-run"
    migration_artifacts_project: str = "User Migration Artifacts"

    VALID_MODES: tuple = field(
        default=("dry-run", "clone", "migrate", "clean-only"),
        init=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def from_environment(cls, dotenv_path: Optional[Path] = None) -> "Settings":
        env_file = dotenv_path or PROJECT_ROOT / ".env"
        load_dotenv(env_file, override=True)

        server_url = os.environ.get("SERVER_URL", "").rstrip("/")
        site_name = os.environ.get("SITE_NAME", "")
        csv_raw = os.environ.get("CSV_LOCATION", "")
        log_raw = os.environ.get("LOG_LOCATION", "")

        missing = []
        if not server_url:
            missing.append("SERVER_URL")
        if not site_name:
            missing.append("SITE_NAME")
        if not csv_raw:
            missing.append("CSV_LOCATION")
        if not log_raw:
            missing.append("LOG_LOCATION")
        if missing:
            raise ConfigurationError(f"Missing required environment variables: {', '.join(missing)}")

        auth = AuthConfig(
            jwt_client_id=os.environ.get("TABLEAU_CONNECTED_APP_CLIENT_ID"),
            jwt_secret_id=os.environ.get("TABLEAU_CONNECTED_APP_SECRET_ID"),
            jwt_secret_value=os.environ.get("TABLEAU_CONNECTED_APP_SECRET_VALUE"),
            jwt_username=os.environ.get("TABLEAU_USERNAME"),
            pat_token_name=os.environ.get("TOKEN_NAME"),
            pat_token_secret=os.environ.get("TOKEN_SECRET"),
            token_refresh_threshold_seconds=_env_int("TOKEN_REFRESH_THRESHOLD_SECONDS", 300),
        )

        api = ApiConfig(
            server_url=server_url,
            site_name=site_name,
            api_version=os.environ.get("API_VERSION", "3.19"),
            api_delay_ms=_env_int("API_DELAY_MS", 100),
            max_retries=_env_int("MAX_RETRIES", 3),
            retry_backoff_base=_env_float("RETRY_BACKOFF_BASE", 2.0),
            retry_initial_wait=_env_float("RETRY_INITIAL_WAIT", 1.0),
            retry_jitter=_env_bool("RETRY_JITTER", True),
            rate_limit_rps=_env_int("RATE_LIMIT_RPS", 10),
            connect_timeout=_env_float("CONNECT_TIMEOUT", 60.0),
            read_timeout=_env_float("READ_TIMEOUT", 300.0),
            session_duration_seconds=_env_int("SESSION_DURATION_SECONDS", 7200),
        )

        log_location = _resolve_path(log_raw)
        cache_config = CacheConfig(
            ttl_hours=_env_int("DIMENSION_CACHE_TTL_HOURS", 24),
            cache_dir=log_location / "cache",
            enabled=_env_bool("DIMENSION_CACHE_ENABLED", True),
        )

        paths = PathConfig(
            csv_location=_resolve_path(csv_raw),
            log_location=log_location,
        )

        return cls(auth=auth, api=api, cache=cache_config, paths=paths,
                   migration_artifacts_project=os.environ.get("MIGRATION_ARTIFACTS_PROJECT", "User Migration Artifacts"))

    def validate(self) -> None:
        if not self.auth.has_jwt and not self.auth.has_pat:
            raise ConfigurationError(
                "At least one auth method required: set JWT (TABLEAU_CONNECTED_APP_*) "
                "or PAT (TOKEN_NAME + TOKEN_SECRET) environment variables"
            )

        parsed = urlparse(self.api.server_url)
        if parsed.scheme not in ("https", "http"):
            raise ConfigurationError(
                f"SERVER_URL must begin with https:// or http://, got: {self.api.server_url!r}"
            )
        if parsed.scheme == "http":
            warnings.warn(
                f"SERVER_URL uses http:// — credentials will be sent unencrypted. "
                f"Use https:// for production environments: {self.api.server_url}",
                stacklevel=2,
            )
        if not parsed.netloc:
            raise ConfigurationError(
                f"SERVER_URL does not appear to be a valid URL: {self.api.server_url!r}"
            )

        if self.paths.csv_location and not self.paths.csv_location.exists():
            raise ConfigurationError(f"CSV file not found: {self.paths.csv_location}")
        if self.paths.csv_location and not self.paths.csv_location.is_file():
            raise ConfigurationError(f"CSV location is not a file: {self.paths.csv_location}")

        if self.paths.log_location:
            try:
                self.paths.log_location.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ConfigurationError(f"Cannot create log directory {self.paths.log_location}: {e}")

        if self.mode not in self.VALID_MODES:
            raise ConfigurationError(
                f"Invalid mode: {self.mode!r}. Must be one of: {', '.join(self.VALID_MODES)}"
            )

        if self.api.api_delay_ms < 0:
            raise ConfigurationError("API_DELAY_MS must be >= 0")
        if self.api.max_retries < 0:
            raise ConfigurationError("MAX_RETRIES must be >= 0")
        if self.api.retry_backoff_base <= 1:
            raise ConfigurationError("RETRY_BACKOFF_BASE must be > 1")
        if self.api.rate_limit_rps < 1:
            raise ConfigurationError("RATE_LIMIT_RPS must be >= 1")

    def get_audit_dir(self, run_id: str) -> Path:
        audit_dir = self.paths.log_location / f"migrate_run_{run_id}"
        audit_dir.mkdir(parents=True, exist_ok=True)
        return audit_dir

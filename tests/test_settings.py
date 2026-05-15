import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from config.settings import (
    Settings, AuthConfig, ApiConfig, CacheConfig, PathConfig,
    _env_int, _env_float, _env_bool,
)
from src.utils.exceptions import ConfigurationError


class TestEnvHelpers(unittest.TestCase):
    def test_env_int_default(self):
        with patch.dict(os.environ, {}, clear=False):
            self.assertEqual(_env_int("NONEXISTENT_VAR", 42), 42)

    def test_env_int_from_env(self):
        with patch.dict(os.environ, {"TEST_INT": "99"}):
            self.assertEqual(_env_int("TEST_INT", 0), 99)

    def test_env_int_invalid_raises(self):
        with patch.dict(os.environ, {"TEST_INT": "abc"}):
            with self.assertRaises(ConfigurationError):
                _env_int("TEST_INT", 0)

    def test_env_float_default(self):
        self.assertEqual(_env_float("NONEXISTENT", 3.14), 3.14)

    def test_env_float_from_env(self):
        with patch.dict(os.environ, {"TEST_FLOAT": "2.5"}):
            self.assertEqual(_env_float("TEST_FLOAT", 0.0), 2.5)

    def test_env_float_invalid_raises(self):
        with patch.dict(os.environ, {"TEST_FLOAT": "nope"}):
            with self.assertRaises(ConfigurationError):
                _env_float("TEST_FLOAT", 0.0)

    def test_env_bool_defaults(self):
        self.assertTrue(_env_bool("NONEXISTENT", True))
        self.assertFalse(_env_bool("NONEXISTENT", False))

    def test_env_bool_from_env(self):
        with patch.dict(os.environ, {"TEST_BOOL": "false"}):
            self.assertFalse(_env_bool("TEST_BOOL", True))
        with patch.dict(os.environ, {"TEST_BOOL": "0"}):
            self.assertFalse(_env_bool("TEST_BOOL", True))
        with patch.dict(os.environ, {"TEST_BOOL": "no"}):
            self.assertFalse(_env_bool("TEST_BOOL", True))
        with patch.dict(os.environ, {"TEST_BOOL": "true"}):
            self.assertTrue(_env_bool("TEST_BOOL", False))
        with patch.dict(os.environ, {"TEST_BOOL": "yes"}):
            self.assertTrue(_env_bool("TEST_BOOL", False))


class TestAuthConfig(unittest.TestCase):
    def test_has_jwt_all_set(self):
        auth = AuthConfig(
            jwt_client_id="cid",
            jwt_secret_id="sid",
            jwt_secret_value="sv",
            jwt_username="user@test.com",
        )
        self.assertTrue(auth.has_jwt)
        self.assertFalse(auth.has_pat)

    def test_has_jwt_partial(self):
        auth = AuthConfig(jwt_client_id="cid", jwt_secret_id="sid")
        self.assertFalse(auth.has_jwt)

    def test_has_pat(self):
        auth = AuthConfig(pat_token_name="name", pat_token_secret="secret")
        self.assertFalse(auth.has_jwt)
        self.assertTrue(auth.has_pat)

    def test_neither(self):
        auth = AuthConfig()
        self.assertFalse(auth.has_jwt)
        self.assertFalse(auth.has_pat)


class TestPathConfig(unittest.TestCase):
    def test_checkpoint_dir(self):
        paths = PathConfig(log_location=Path("/tmp/audit"))
        self.assertEqual(paths.checkpoint_dir, Path("/tmp/audit/checkpoints"))

    def test_checkpoint_dir_none(self):
        paths = PathConfig()
        self.assertIsNone(paths.checkpoint_dir)


class TestSettingsValidation(unittest.TestCase):
    def _valid_settings(self, tmpdir: Path) -> Settings:
        csv_path = tmpdir / "test.csv"
        csv_path.write_text("old_username,new_username\na@b.com,c@d.com\n")
        return Settings(
            auth=AuthConfig(pat_token_name="n", pat_token_secret="s"),
            api=ApiConfig(server_url="https://site.online.tableau.com", site_name="mysite"),
            cache=CacheConfig(),
            paths=PathConfig(csv_location=csv_path, log_location=tmpdir / "logs"),
            mode="dry-run",
        )

    def test_valid_settings_pass(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.validate()

    def test_no_auth_raises(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.auth = AuthConfig()
            with self.assertRaises(ConfigurationError) as ctx:
                settings.validate()
            self.assertIn("auth method required", str(ctx.exception))

    def test_bad_url_scheme(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.api.server_url = "ftp://bad.com"
            with self.assertRaises(ConfigurationError):
                settings.validate()

    def test_bad_url_no_host(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.api.server_url = "https://"
            with self.assertRaises(ConfigurationError):
                settings.validate()

    def test_csv_not_found(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.paths.csv_location = Path(tmpdir) / "nonexistent.csv"
            with self.assertRaises(ConfigurationError) as ctx:
                settings.validate()
            self.assertIn("CSV file not found", str(ctx.exception))

    def test_invalid_mode(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.mode = "destroy"
            with self.assertRaises(ConfigurationError) as ctx:
                settings.validate()
            self.assertIn("Invalid mode", str(ctx.exception))

    def test_negative_delay(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.api.api_delay_ms = -1
            with self.assertRaises(ConfigurationError):
                settings.validate()

    def test_bad_backoff_base(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.api.retry_backoff_base = 0.5
            with self.assertRaises(ConfigurationError):
                settings.validate()

    def test_zero_rps(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.api.rate_limit_rps = 0
            with self.assertRaises(ConfigurationError):
                settings.validate()

    def test_get_audit_dir(self):
        with TemporaryDirectory() as tmpdir:
            settings = self._valid_settings(Path(tmpdir))
            settings.paths.log_location = Path(tmpdir) / "logs"
            audit = settings.get_audit_dir("20260413_120000")
            self.assertTrue(audit.exists())
            self.assertEqual(audit.name, "migrate_run_20260413_120000")


class TestSettingsFromEnvironment(unittest.TestCase):
    def test_missing_required_raises(self):
        with patch.dict(os.environ, {"SERVER_URL": "https://x.com"}, clear=True):
            with self.assertRaises(ConfigurationError) as ctx:
                Settings.from_environment(dotenv_path=Path("/nonexistent/.env"))
            self.assertIn("Missing required", str(ctx.exception))

    def test_full_env(self):
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "test.csv"
            csv_path.write_text("old_username,new_username\na@b.com,c@d.com\n")

            env = {
                "SERVER_URL": "https://site.online.tableau.com",
                "SITE_NAME": "mysite",
                "CSV_LOCATION": str(csv_path),
                "LOG_LOCATION": str(Path(tmpdir) / "logs"),
                "TOKEN_NAME": "pat_name",
                "TOKEN_SECRET": "pat_secret",
                "MAX_RETRIES": "5",
                "RATE_LIMIT_RPS": "20",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = Settings.from_environment(dotenv_path=Path("/nonexistent/.env"))

            self.assertEqual(settings.api.server_url, "https://site.online.tableau.com")
            self.assertEqual(settings.api.site_name, "mysite")
            self.assertTrue(settings.auth.has_pat)
            self.assertEqual(settings.api.max_retries, 5)
            self.assertEqual(settings.api.rate_limit_rps, 20)


if __name__ == "__main__":
    unittest.main()

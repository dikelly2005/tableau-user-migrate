import asyncio
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch, call
from pathlib import Path
from tempfile import TemporaryDirectory

from src.utils.cache import DimensionCache, DimensionRecord
from models.impact import UXArtifact
from reporting.audit import AuditLogger, AuditAction


SITE_ID = "test-site-id"
OLD_USER_ID = "old-user-111"
NEW_USER_ID = "new-user-222"
OLD_USERNAME = "old@example.com"
NEW_USERNAME = "new@example.com"


def _make_audit_logger():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.jsonl"
        return AuditLogger(path, "test-run")


def _make_mock_client():
    client = MagicMock()
    client.site_id = SITE_ID
    client.post = AsyncMock()
    client.put = AsyncMock()
    client.get = AsyncMock()
    client.delete = AsyncMock()
    return client


class TestFavoriteServiceHTTPVerbs(unittest.TestCase):

    def test_clone_favorites_uses_post(self):
        from src.services.favorites import FavoriteService

        client = _make_mock_client()
        cache = DimensionCache()
        cache._dimensions["user_favorites"] = {
            f"{OLD_USER_ID}:fav1": DimensionRecord(
                id="fav1", name="Sales Dashboard",
                attrs={
                    "_parent_id": OLD_USER_ID,
                    "workbook": {"id": "wb-1", "name": "Sales Dashboard"},
                },
            ),
        }
        audit = _make_audit_logger()
        svc = FavoriteService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_favorites(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(result, 1)
        client.post.assert_called_once()
        client.put.assert_not_called()
        call_args = client.post.call_args
        endpoint = call_args[0][0]
        payload = call_args[0][1]
        self.assertIn(f"/favorites/{NEW_USER_ID}", endpoint)
        self.assertIn('<favorite label="Sales Dashboard">', payload)
        self.assertIn('<workbook id="wb-1"/>', payload)

    def test_remove_favorites_uses_delete(self):
        from src.services.favorites import FavoriteService

        client = _make_mock_client()
        cache = DimensionCache()
        cache._dimensions["user_favorites"] = {
            f"{OLD_USER_ID}:fav1": DimensionRecord(
                id="fav1", name="Sales Dashboard",
                attrs={
                    "_parent_id": OLD_USER_ID,
                    "workbook": {"id": "wb-1", "name": "Sales Dashboard"},
                },
            ),
        }
        audit = _make_audit_logger()
        svc = FavoriteService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.remove_favorites(OLD_USER_ID, OLD_USERNAME)
        )

        self.assertEqual(result, 1)
        client.delete.assert_called_once()
        endpoint = client.delete.call_args[0][0]
        self.assertIn(f"/favorites/{OLD_USER_ID}/workbook/wb-1", endpoint)


class TestCustomViewServiceHTTPVerbs(unittest.TestCase):

    def _setup_cache_with_cv(self):
        cache = DimensionCache()
        cache._dimensions["custom_views"] = {
            "cv-1": DimensionRecord(
                id="cv-1", name="My Filter",
                attrs={
                    "owner": {"id": OLD_USER_ID},
                    "view": {"id": "view-1", "name": "Overview"},
                    "workbook": {"id": "wb-1", "name": "Dashboard"},
                },
            ),
        }
        return cache

    def test_transfer_ownership_uses_put(self):
        from src.services.custom_views import CustomViewService

        client = _make_mock_client()
        no_users_xml = ET.tostring(ET.Element("tsResponse"), encoding="unicode")
        client.get.return_value = ET.fromstring(f'<tsResponse xmlns="http://tableau.com/api"><users></users></tsResponse>')
        cache = self._setup_cache_with_cv()
        audit = _make_audit_logger()
        svc = CustomViewService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_custom_views(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(result, 1)
        client.put.assert_called_once()
        endpoint = client.put.call_args[0][0]
        payload = client.put.call_args[0][1]
        self.assertIn("/customviews/cv-1", endpoint)
        self.assertNotIn("/default/", endpoint)
        self.assertIn(f'<owner id="{NEW_USER_ID}"/>', payload)

    def test_set_default_uses_post_with_user_id_in_path(self):
        from src.services.custom_views import CustomViewService

        client = _make_mock_client()
        default_xml = ET.fromstring(
            f'<tsResponse xmlns="http://tableau.com/api">'
            f'<users><user id="{OLD_USER_ID}"/></users>'
            f'</tsResponse>'
        )
        client.get.return_value = default_xml
        cache = self._setup_cache_with_cv()
        audit = _make_audit_logger()
        svc = CustomViewService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_custom_views(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(result, 1)
        post_calls = [c for c in client.post.call_args_list if "/default/users/" in str(c)]
        self.assertEqual(len(post_calls), 1)
        post_endpoint = post_calls[0][0][0]
        self.assertIn(f"/customviews/cv-1/default/users/{NEW_USER_ID}", post_endpoint)

        delete_calls = [c for c in client.delete.call_args_list if "/default/users/" in str(c)]
        self.assertEqual(len(delete_calls), 1)
        delete_endpoint = delete_calls[0][0][0]
        self.assertIn(f"/customviews/cv-1/default/users/{OLD_USER_ID}", delete_endpoint)

    def test_no_default_transfer_when_not_default(self):
        from src.services.custom_views import CustomViewService

        client = _make_mock_client()
        empty_xml = ET.fromstring(
            '<tsResponse xmlns="http://tableau.com/api"><users></users></tsResponse>'
        )
        client.get.return_value = empty_xml
        cache = self._setup_cache_with_cv()
        audit = _make_audit_logger()
        svc = CustomViewService(client, audit, cache)

        asyncio.get_event_loop().run_until_complete(
            svc.clone_custom_views(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        client.post.assert_not_called()
        client.delete.assert_not_called()


class TestAlertServiceHTTPVerbs(unittest.TestCase):

    def _setup_cache_with_alert(self):
        cache = DimensionCache()
        cache._dimensions["data_alerts"] = {
            "alert-1": DimensionRecord(
                id="alert-1", name="Revenue threshold",
                attrs={
                    "owner": {"id": OLD_USER_ID},
                    "view": {"id": "view-1"},
                    "creatorId": OLD_USER_ID,
                },
            ),
        }
        return cache

    def test_clone_alerts_posts_recipient_then_puts_ownership(self):
        from src.services.alerts import AlertService

        client = _make_mock_client()
        cache = self._setup_cache_with_alert()
        audit = _make_audit_logger()
        svc = AlertService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_alerts(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(result, 1)
        client.post.assert_called_once()
        post_endpoint = client.post.call_args[0][0]
        post_payload = client.post.call_args[0][1]
        self.assertIn(f"/dataAlerts/alert-1/users", post_endpoint)
        self.assertIn(f'<user id="{NEW_USER_ID}"/>', post_payload)

        client.put.assert_called_once()
        put_endpoint = client.put.call_args[0][0]
        put_payload = client.put.call_args[0][1]
        self.assertIn("/dataAlerts/alert-1", put_endpoint)
        self.assertNotIn("/users", put_endpoint)
        self.assertIn(f'<owner id="{NEW_USER_ID}"/>', put_payload)

    def test_clone_alerts_handles_409_conflict(self):
        from src.services.alerts import AlertService
        from src.utils.exceptions import APIError

        client = _make_mock_client()
        client.post.side_effect = APIError("409 Conflict", status_code=409)
        cache = self._setup_cache_with_alert()
        audit = _make_audit_logger()
        svc = AlertService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_alerts(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(result, 1)
        client.put.assert_called_once()

    def test_clone_alerts_ownership_transfer_failure_still_counts(self):
        from src.services.alerts import AlertService
        from src.utils.exceptions import APIError

        client = _make_mock_client()
        client.put.side_effect = APIError("403 Forbidden", status_code=403)
        cache = self._setup_cache_with_alert()
        audit = _make_audit_logger()
        svc = AlertService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_alerts(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(result, 1)
        client.post.assert_called_once()
        client.put.assert_called_once()


    def test_remove_alerts_uses_delete(self):
        from src.services.alerts import AlertService

        client = _make_mock_client()
        cache = self._setup_cache_with_alert()
        audit = _make_audit_logger()
        svc = AlertService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.remove_alerts(OLD_USER_ID, OLD_USERNAME)
        )

        self.assertEqual(result, 1)
        client.delete.assert_called_once()
        endpoint = client.delete.call_args[0][0]
        self.assertIn(f"/dataAlerts/alert-1/users/{OLD_USER_ID}", endpoint)

    def test_clone_alerts_retries_on_transient_failure(self):
        from src.services.alerts import AlertService
        from src.utils.exceptions import APIError

        client = _make_mock_client()
        client.post.side_effect = [
            APIError("500 Server Error", status_code=500),
            None,
        ]
        cache = self._setup_cache_with_alert()
        audit = _make_audit_logger()
        svc = AlertService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_alerts(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(result, 1)

    def test_clone_alerts_exhausts_retries(self):
        from src.services.alerts import AlertService
        from src.utils.exceptions import APIError

        client = _make_mock_client()
        client.post.side_effect = APIError("500 Server Error", status_code=500)
        cache = self._setup_cache_with_alert()
        audit = _make_audit_logger()
        svc = AlertService(client, audit, cache)

        result = asyncio.get_event_loop().run_until_complete(
            svc.clone_alerts(OLD_USER_ID, OLD_USERNAME, NEW_USER_ID, NEW_USERNAME)
        )

        self.assertEqual(result, 0)
        self.assertEqual(client.post.call_count, 3)


if __name__ == "__main__":
    unittest.main()

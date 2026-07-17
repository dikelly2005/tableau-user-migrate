# Unit tests for migration services per PDF Section 5.2 recommendations
# Co-authored with CoCo
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from reporting.audit import AuditLogger, AuditAction, AuditResult, _hash_email, _redact_value


# =============================================================================
# PII REDACTION TESTS (M2)
# =============================================================================

class TestPIIRedaction:
    def test_hash_email_deterministic(self):
        h1 = _hash_email("user@example.com")
        h2 = _hash_email("user@example.com")
        assert h1 == h2
        assert len(h1) == 12

    def test_hash_email_case_insensitive(self):
        assert _hash_email("User@Example.COM") == _hash_email("user@example.com")

    def test_redact_value_string_with_email(self):
        result = _redact_value("hello user@example.com goodbye")
        assert "@" not in result
        assert "hello" in result
        assert "goodbye" in result

    def test_redact_value_no_email(self):
        assert _redact_value("hello world") == "hello world"

    def test_redact_value_dict(self):
        result = _redact_value({"user": "admin@test.org", "id": "abc123"})
        assert "@" not in result["user"]
        assert result["id"] == "abc123"

    def test_redact_value_nested(self):
        result = _redact_value({"details": {"emails": ["a@b.com", "c@d.org"]}})
        for v in result["details"]["emails"]:
            assert "@" not in v

    def test_audit_logger_redact_mode(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        logger = AuditLogger(audit_file, "test-run", redact_pii=True)
        logger.log_success(
            AuditAction.USER_CREATE,
            old_username="old@example.com",
            new_username="new@example.com",
        )
        line = audit_file.read_text().strip()
        data = json.loads(line)
        assert "@" not in data.get("old_username", "")
        assert "@" not in data.get("new_username", "")

    def test_audit_logger_no_redact_mode(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        logger = AuditLogger(audit_file, "test-run", redact_pii=False)
        logger.log_success(
            AuditAction.USER_CREATE,
            old_username="old@example.com",
            new_username="new@example.com",
        )
        line = audit_file.read_text().strip()
        data = json.loads(line)
        assert data["old_username"] == "old@example.com"
        assert data["new_username"] == "new@example.com"


# =============================================================================
# COLLECTION SERVICE TESTS (C1 fix validation)
# =============================================================================

class TestCollectionService:
    @pytest.fixture
    def mock_deps(self, tmp_path):
        client = MagicMock()
        client.site_id = "site-123"
        client._base = AsyncMock()
        audit = AuditLogger(tmp_path / "audit.jsonl", "test-run")
        cache = MagicMock()
        endpoints_config = {
            "endpoints": {
                "collections": {"path": "-/collections"},
                "collection_single": {"path": "-/collections/{collection_luid}"},
                "collection_items": {"path": "-/collections/{collection_luid}/items"},
                "collection_permissions": {"path": "sites/{site_id}/collections/{collection_luid}/permissions"},
            }
        }
        return client, audit, cache, endpoints_config

    @pytest.mark.asyncio
    async def test_clone_skips_delete_on_partial_copy(self, mock_deps):
        from src.services.collections import CollectionService
        client, audit, cache, endpoints_config = mock_deps

        cache.get_ids.return_value = ["coll-1"]
        record = MagicMock()
        record.id = "coll-1"
        record.name = "My Collection"
        record.attrs = {"description": "test"}
        cache.get_record.return_value = record

        svc = CollectionService(client, audit, cache, endpoints_config)

        # Mock: 3 items to copy but only 1 succeeds
        items_response = MagicMock()
        items_response.json.return_value = {"items": [
            {"type": "workbook", "content": {"luid": "wb-1"}},
            {"type": "datasource", "content": {"luid": "ds-1"}},
            {"type": "view", "content": {"luid": "v-1"}},
        ]}
        create_response = MagicMock()
        create_response.json.return_value = {"luid": "new-coll-1"}

        call_count = [0]
        async def mock_request(method, endpoint, **kwargs):
            if method == "GET":
                return items_response
            if method == "POST" and "items" not in endpoint:
                return create_response
            if method == "POST" and "items" in endpoint:
                call_count[0] += 1
                if call_count[0] > 1:
                    raise Exception("API Error 500")
                resp = MagicMock()
                resp.json.return_value = {}
                return resp
            if method == "DELETE":
                pytest.fail("DELETE should not be called when items are incomplete")
            return MagicMock()

        client._base.request = mock_request
        client.get = AsyncMock(return_value=MagicMock())

        result = await svc.clone_collections("old-user", "old@test.com", "new-user", "new@test.com")
        assert result == 0  # No successful clones since items were partial

    @pytest.mark.asyncio
    async def test_clone_deletes_on_full_copy(self, mock_deps):
        from src.services.collections import CollectionService
        client, audit, cache, endpoints_config = mock_deps

        cache.get_ids.return_value = ["coll-1"]
        record = MagicMock()
        record.id = "coll-1"
        record.name = "My Collection"
        record.attrs = {"description": "test"}
        cache.get_record.return_value = record

        svc = CollectionService(client, audit, cache, endpoints_config)

        items_response = MagicMock()
        items_response.json.return_value = {"items": [
            {"type": "workbook", "content": {"luid": "wb-1"}},
        ]}
        create_response = MagicMock()
        create_response.json.return_value = {"luid": "new-coll-1"}

        delete_called = [False]

        async def mock_request(method, endpoint, **kwargs):
            if method == "GET":
                return items_response
            if method == "POST":
                return create_response
            if method == "DELETE":
                delete_called[0] = True
                return MagicMock()
            return MagicMock()

        client._base.request = mock_request
        client.get = AsyncMock(return_value=MagicMock())
        client.put = AsyncMock(return_value=MagicMock())

        result = await svc.clone_collections("old-user", "old@test.com", "new-user", "new@test.com")
        assert result == 1
        assert delete_called[0] is True


# =============================================================================
# SUBSCRIPTION SERVICE TESTS (H2 fix validation)
# =============================================================================

class TestSubscriptionService:
    @pytest.fixture
    def mock_deps(self, tmp_path):
        client = MagicMock()
        client.site_id = "site-123"
        audit = AuditLogger(tmp_path / "audit.jsonl", "test-run")
        cache = MagicMock()
        endpoints_config = {
            "endpoints": {
                "subscriptions": {"path": "sites/{site_id}/subscriptions"},
                "subscription_single": {"path": "sites/{site_id}/subscriptions/{subscription_id}"},
            }
        }
        return client, audit, cache, endpoints_config

    @pytest.mark.asyncio
    async def test_skips_subscription_without_schedule_id(self, mock_deps):
        from src.services.subscriptions import SubscriptionService
        from models.impact import UXArtifact
        client, audit, cache, endpoints_config = mock_deps

        cache.get_ids.return_value = ["sub-1"]
        record = MagicMock()
        record.id = "sub-1"
        record.name = "Weekly Report"
        record.attrs = {"content": {"type": "view", "id": "v-1"}, "schedule": {}}
        cache.get_record.return_value = record

        svc = SubscriptionService(client, audit, cache, endpoints_config)
        client.post = AsyncMock()

        result = await svc.clone_subscriptions("old-user", "old@test.com", "new-user", "new@test.com")
        assert result == 0
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_clones_subscription_with_valid_schedule(self, mock_deps):
        from src.services.subscriptions import SubscriptionService
        client, audit, cache, endpoints_config = mock_deps

        cache.get_ids.return_value = ["sub-1"]
        record = MagicMock()
        record.id = "sub-1"
        record.name = "Weekly Report"
        record.attrs = {"content": {"type": "view", "id": "v-1"}, "schedule": {"id": "sched-1"}}
        cache.get_record.return_value = record

        svc = SubscriptionService(client, audit, cache, endpoints_config)
        client.post = AsyncMock(return_value=MagicMock())

        result = await svc.clone_subscriptions("old-user", "old@test.com", "new-user", "new@test.com")
        assert result == 1
        client.post.assert_called_once()


# =============================================================================
# WEBHOOK SERVICE TESTS (H1 fix validation)
# =============================================================================

class TestWebhookService:
    @pytest.fixture
    def mock_deps(self, tmp_path):
        client = MagicMock()
        client.site_id = "site-123"
        client._base = AsyncMock()
        audit = AuditLogger(tmp_path / "audit.jsonl", "test-run")
        cache = MagicMock()
        endpoints_config = {
            "endpoints": {
                "webhooks": {"path": "sites/{site_id}/webhooks"},
                "webhook_single": {"path": "sites/{site_id}/webhooks/{webhook_id}"},
            }
        }
        return client, audit, cache, endpoints_config

    @pytest.mark.asyncio
    async def test_clone_uses_json_payload(self, mock_deps):
        from src.services.webhooks import WebhookService
        client, audit, cache, endpoints_config = mock_deps

        record = MagicMock()
        record.id = "wh-1"
        record.name = "Deploy Hook"
        record.attrs = {"event": "workbook-updated", "url": "https://hooks.example.com/deploy"}
        cache.get_ids.return_value = ["wh-1"]
        cache.get_record.return_value = record

        svc = WebhookService(client, audit, cache, endpoints_config)
        result = await svc.clone_webhooks("old-user", "old@test.com", "new-user", "new@test.com")

        assert result == 1
        call_args = client._base.request.call_args
        assert call_args[0][0] == "PUT"
        payload = json.loads(call_args[1]["content"])
        assert "webhook" in payload
        assert payload["webhook"]["owner"]["id"] == "new-user"
        assert "webhook-destination" in payload["webhook"]


# =============================================================================
# PULSE SERVICE TESTS
# =============================================================================

class TestPulseService:
    @pytest.fixture
    def mock_deps(self, tmp_path):
        client = MagicMock()
        client.site_id = "site-123"
        client._base = AsyncMock()
        audit = AuditLogger(tmp_path / "audit.jsonl", "test-run")
        cache = MagicMock()
        endpoints_config = {
            "endpoints": {
                "pulse_subscriptions": {"path": "-/pulse/subscriptions"},
                "pulse_alerts": {"path": "-/pulse/alerts"},
            }
        }
        return client, audit, cache, endpoints_config

    @pytest.mark.asyncio
    async def test_clone_pulse_subscriptions_resolves_path_from_config(self, mock_deps):
        from src.services.pulse import PulseService
        client, audit, cache, endpoints_config = mock_deps

        record = MagicMock()
        record.id = "ps-1"
        record.attrs = {"follower_id": "old-user", "metric_id": "m-1", "condition": None}
        cache.get_all_records.return_value = [record]

        svc = PulseService(client, audit, cache, endpoints_config)
        result = await svc.clone_pulse_subscriptions("old-user", "old@test.com", "new-user", "new@test.com")

        assert result == 1
        call_args = client._base.request.call_args
        assert call_args[0][0] == "POST"
        assert "/-/pulse/subscriptions" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_skips_subscription_without_metric_id(self, mock_deps):
        from src.services.pulse import PulseService
        client, audit, cache, endpoints_config = mock_deps

        record = MagicMock()
        record.id = "ps-1"
        record.attrs = {"follower_id": "old-user", "metric_id": None}
        cache.get_all_records.return_value = [record]

        svc = PulseService(client, audit, cache, endpoints_config)
        result = await svc.clone_pulse_subscriptions("old-user", "old@test.com", "new-user", "new@test.com")
        assert result == 0
        client._base.request.assert_not_called()


# =============================================================================
# PERMISSION SERVICE TESTS (C3 batch refresh)
# =============================================================================

class TestPermissionServiceCacheStaleness:
    @pytest.mark.asyncio
    async def test_cache_refresh_after_batch_limit(self):
        from src.services.permissions import PermissionService, PERMISSION_BATCH_LIMIT

        client = MagicMock()
        client.site_id = "site-123"
        audit = MagicMock()
        cache = MagicMock()
        cache.refresh_permissions = AsyncMock()
        endpoints_config = {"endpoints": {}}

        svc = PermissionService(client, audit, cache, endpoints_config)

        for i in range(PERMISSION_BATCH_LIMIT - 1):
            await svc.check_cache_staleness()
        cache.refresh_permissions.assert_not_called()

        await svc.check_cache_staleness()
        cache.refresh_permissions.assert_called_once()

    @pytest.mark.asyncio
    async def test_counter_resets_after_refresh(self):
        from src.services.permissions import PermissionService, PERMISSION_BATCH_LIMIT

        client = MagicMock()
        client.site_id = "site-123"
        audit = MagicMock()
        cache = MagicMock()
        cache.refresh_permissions = AsyncMock()
        endpoints_config = {"endpoints": {}}

        svc = PermissionService(client, audit, cache, endpoints_config)

        for i in range(PERMISSION_BATCH_LIMIT):
            await svc.check_cache_staleness()
        assert cache.refresh_permissions.call_count == 1

        # Counter should have reset, so another full batch triggers a second refresh
        for i in range(PERMISSION_BATCH_LIMIT):
            await svc.check_cache_staleness()
        assert cache.refresh_permissions.call_count == 2

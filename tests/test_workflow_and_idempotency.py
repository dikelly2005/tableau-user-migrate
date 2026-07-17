# Additional unit tests for paginate_items, workflow ordering, and idempotency
# Co-authored with CoCo
import json
import xml.etree.ElementTree as ET
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from reporting.audit import AuditLogger, AuditAction


# =============================================================================
# PAGINATE_ITEMS TESTS
# =============================================================================

class TestPaginateItems:
    @pytest.fixture
    def mock_client(self):
        from src.api.client import TableauAPIClient
        base = MagicMock()
        base.auth = MagicMock()
        base.auth.site_id = "site-123"
        client = MagicMock(spec=TableauAPIClient)
        client.site_id = "site-123"
        # Use the real paginate_items implementation
        client.paginate_items = TableauAPIClient.paginate_items.__get__(client)
        return client

    @pytest.mark.asyncio
    async def test_single_page(self, mock_client):
        xml_response = ET.fromstring(
            '<tsResponse xmlns="http://tableau.com/api">'
            '<pagination pageNumber="1" pageSize="100" totalAvailable="2"/>'
            '<users><user id="u1" name="Alice"/><user id="u2" name="Bob"/></users>'
            '</tsResponse>'
        )
        mock_client.get = AsyncMock(return_value=xml_response)

        result = await mock_client.paginate_items("/sites/s/users", "user")
        assert len(result) == 2
        assert result[0].get("id") == "u1"
        mock_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_page(self, mock_client):
        page1 = ET.fromstring(
            '<tsResponse xmlns="http://tableau.com/api">'
            '<pagination pageNumber="1" pageSize="2" totalAvailable="4"/>'
            '<users><user id="u1" name="A"/><user id="u2" name="B"/></users>'
            '</tsResponse>'
        )
        page2 = ET.fromstring(
            '<tsResponse xmlns="http://tableau.com/api">'
            '<pagination pageNumber="2" pageSize="2" totalAvailable="4"/>'
            '<users><user id="u3" name="C"/><user id="u4" name="D"/></users>'
            '</tsResponse>'
        )
        mock_client.get = AsyncMock(side_effect=[page1, page2])

        result = await mock_client.paginate_items("/sites/s/users", "user", page_size=2)
        assert len(result) == 4
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_pagination_element(self, mock_client):
        xml_response = ET.fromstring(
            '<tsResponse xmlns="http://tableau.com/api">'
            '<users><user id="u1" name="Solo"/></users>'
            '</tsResponse>'
        )
        mock_client.get = AsyncMock(return_value=xml_response)

        result = await mock_client.paginate_items("/sites/s/users", "user")
        assert len(result) == 1
        mock_client.get.assert_called_once()


# =============================================================================
# MIGRATE WORKFLOW STEP ORDERING TESTS
# =============================================================================

class TestMigrateWorkflowStepOrdering:
    @pytest.mark.asyncio
    async def test_deactivation_blocked_when_content_remains(self, tmp_path):
        """Verifies H4: deactivation is blocked if get_owned_content returns items."""
        from src.workflows.migrate import MigrateWorkflow
        from src.utils.checkpoint import CheckpointManager

        user_svc = MagicMock()
        user_svc.lookup_user = AsyncMock(return_value={"id": "new-u1", "name": "new@q.com", "site_role": "Explorer"})
        user_svc.create_user = AsyncMock(return_value={"id": "new-u1", "name": "new@q.com", "site_role": "Explorer", "created": True})
        user_svc.deactivate_user = AsyncMock()

        perm_svc = MagicMock()
        perm_svc.clone_permissions = AsyncMock()
        perm_svc.check_cache_staleness = AsyncMock()

        group_svc = MagicMock()
        group_svc.clone_groups = AsyncMock(return_value=0)

        ownership_svc = MagicMock()
        ownership_svc.transfer_ownership = AsyncMock(return_value=1)
        # This is the key — simulate 1 item still owned after transfer
        ownership_svc.get_owned_content = AsyncMock(return_value=[
            {"content_type": "workbooks", "content_id": "wb-1", "content_name": "Orphan Workbook"}
        ])

        fav_svc = MagicMock()
        fav_svc.clone_favorites = AsyncMock(return_value=0)
        sub_svc = MagicMock()
        sub_svc.clone_subscriptions = AsyncMock(return_value=0)
        alert_svc = MagicMock()
        alert_svc.clone_alerts = AsyncMock(return_value=0)
        cv_svc = MagicMock()
        cv_svc.clone_custom_views = AsyncMock(return_value=0)
        collection_svc = MagicMock()
        collection_svc.clone_collections = AsyncMock(return_value=0)
        pulse_svc = MagicMock()
        pulse_svc.clone_pulse_subscriptions = AsyncMock(return_value=0)
        pulse_svc.clone_pulse_alerts = AsyncMock(return_value=0)
        pulse_svc.remove_pulse_subscriptions = AsyncMock(return_value=0)
        pulse_svc.remove_pulse_alerts = AsyncMock(return_value=0)
        webhook_svc = MagicMock()
        webhook_svc.clone_webhooks = AsyncMock(return_value=0)
        webhook_svc.remove_webhooks = AsyncMock(return_value=0)
        sub_svc.remove_subscriptions = AsyncMock(return_value=0)
        alert_svc.remove_alerts = AsyncMock(return_value=0)

        audit_file = tmp_path / "audit.jsonl"
        audit = AuditLogger(audit_file, "test-run")
        checkpoint = CheckpointManager()
        settings = MagicMock()

        workflow = MigrateWorkflow(
            user_svc, perm_svc, group_svc, ownership_svc,
            fav_svc, sub_svc, alert_svc, cv_svc, collection_svc,
            pulse_svc, webhook_svc, MagicMock(), checkpoint, audit, settings,
        )

        mappings = [{"old_username": "old@qts.com", "new_username": "new@q.com", "site_role": "Explorer"}]
        await workflow.execute(mappings, tmp_path)

        # Deactivate should NOT have been called since content remains
        user_svc.deactivate_user.assert_not_called()


# =============================================================================
# IDEMPOTENCY / 409 CONFLICT HANDLING TESTS
# =============================================================================

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_group_add_409_is_success(self, tmp_path):
        """GroupService.add_user_to_group should treat 409 as a skip, not an error."""
        from src.services.groups import GroupService

        client = MagicMock()
        client.site_id = "site-123"
        client.post = AsyncMock(side_effect=Exception("409 Conflict: User already in group"))
        audit = AuditLogger(tmp_path / "audit.jsonl", "test-run")
        cache = MagicMock()
        cache.get_parents_for_child.return_value = []
        cache.has_dimension.return_value = False
        endpoints_config = {
            "endpoints": {
                "group_users": {"path": "sites/{site_id}/groups/{group_id}/users"},
                "group_user_single": {"path": "sites/{site_id}/groups/{group_id}/users/{user_id}"},
                "user_groups": {"path": "sites/{site_id}/users/{user_id}/groups"},
            }
        }

        svc = GroupService(client, audit, cache, endpoints_config)
        # Should not raise — 409 is handled gracefully
        await svc.add_user_to_group("grp-1", "user-1", "user@test.com", "Marketing")

    @pytest.mark.asyncio
    async def test_favorites_clone_409_counts_as_success(self, tmp_path):
        """FavoriteService should count 409 responses as successful clones."""
        from src.services.favorites import FavoriteService
        from models.impact import UXArtifact

        client = MagicMock()
        client.site_id = "site-123"
        client.post = AsyncMock(side_effect=Exception("409 Conflict: already exists"))
        audit = AuditLogger(tmp_path / "audit.jsonl", "test-run")
        cache = MagicMock()
        cache.get_child_records.return_value = [
            MagicMock(
                id="fav-1",
                name="My WB",
                attrs={"workbook": {"id": "wb-1", "name": "My WB"}},
            )
        ]
        endpoints_config = {
            "endpoints": {
                "user_favorites": {"path": "sites/{site_id}/favorites/{user_id}"},
                "user_favorite_single": {"path": "sites/{site_id}/favorites/{user_id}/{content_type}/{content_id}"},
            }
        }

        svc = FavoriteService(client, audit, cache, endpoints_config)
        result = await svc.clone_favorites("old-user", "old@test.com", "new-user", "new@test.com")
        assert result == 1  # 409 counted as success

    @pytest.mark.asyncio
    async def test_subscription_clone_409_counts_as_success(self, tmp_path):
        """SubscriptionService should count 409 responses as successful clones."""
        from src.services.subscriptions import SubscriptionService

        client = MagicMock()
        client.site_id = "site-123"
        client.post = AsyncMock(side_effect=Exception("409 Conflict: already exists"))
        audit = AuditLogger(tmp_path / "audit.jsonl", "test-run")
        cache = MagicMock()
        record = MagicMock()
        record.id = "sub-1"
        record.name = "Weekly"
        record.attrs = {"content": {"type": "view", "id": "v-1"}, "schedule": {"id": "sched-1"}}
        cache.get_ids.return_value = ["sub-1"]
        cache.get_record.return_value = record
        endpoints_config = {
            "endpoints": {
                "subscriptions": {"path": "sites/{site_id}/subscriptions"},
                "subscription_single": {"path": "sites/{site_id}/subscriptions/{subscription_id}"},
            }
        }

        svc = SubscriptionService(client, audit, cache, endpoints_config)
        result = await svc.clone_subscriptions("old-user", "old@test.com", "new-user", "new@test.com")
        assert result == 1  # 409 counted as success

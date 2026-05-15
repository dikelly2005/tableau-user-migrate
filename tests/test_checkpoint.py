import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.utils.checkpoint import CheckpointManager, CheckpointStatus, UserCheckpoint


class TestUserCheckpoint(unittest.TestCase):
    def test_defaults(self):
        cp = UserCheckpoint(old_username="old@test.com", new_username="new@test.com")
        self.assertEqual(cp.status, CheckpointStatus.PENDING)
        self.assertEqual(cp.mode, "")
        self.assertIsNone(cp.started_at)
        self.assertIsNone(cp.error)
        self.assertEqual(cp.steps_completed, [])

    def test_to_dict_serializes_status(self):
        cp = UserCheckpoint(old_username="a@b.com", new_username="c@d.com", status=CheckpointStatus.COMPLETED)
        d = cp.to_dict()
        self.assertEqual(d["status"], "completed")
        self.assertEqual(d["old_username"], "a@b.com")

    def test_from_dict_roundtrip(self):
        cp = UserCheckpoint(
            old_username="a@b.com",
            new_username="c@d.com",
            status=CheckpointStatus.FAILED,
            mode="migrate",
            error="test error",
            steps_completed=["step1", "step2"],
        )
        d = cp.to_dict()
        restored = UserCheckpoint.from_dict(d)
        self.assertEqual(restored.status, CheckpointStatus.FAILED)
        self.assertEqual(restored.mode, "migrate")
        self.assertEqual(restored.error, "test error")
        self.assertEqual(restored.steps_completed, ["step1", "step2"])

    def test_from_dict_ignores_unknown_fields(self):
        d = {"old_username": "a", "new_username": "b", "status": "pending", "unknown": "x"}
        cp = UserCheckpoint.from_dict(d)
        self.assertEqual(cp.old_username, "a")


class TestCheckpointManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir_obj = TemporaryDirectory()
        self.tmpdir = Path(self.tmpdir_obj.name)
        self.mappings = [
            {"old_username": "alice@old.com", "new_username": "alice@new.com"},
            {"old_username": "bob@old.com", "new_username": "bob@new.com"},
            {"old_username": "charlie@old.com", "new_username": "charlie@new.com"},
        ]

    def tearDown(self):
        self.tmpdir_obj.cleanup()

    def _make_manager(self) -> CheckpointManager:
        mgr = CheckpointManager()
        mgr.initialize(self.mappings, "migrate", "20260413_120000", self.tmpdir)
        return mgr

    def test_initialize(self):
        mgr = self._make_manager()
        self.assertEqual(mgr.total, 3)
        self.assertEqual(mgr.completed_count, 0)
        self.assertEqual(mgr.failed_count, 0)
        pending = mgr.get_pending()
        self.assertEqual(len(pending), 3)

    def test_mark_in_progress(self):
        mgr = self._make_manager()
        mgr.mark_in_progress("alice@old.com")
        pending = mgr.get_pending()
        alice = [cp for cp in pending if cp.old_username == "alice@old.com"][0]
        self.assertEqual(alice.status, CheckpointStatus.IN_PROGRESS)
        self.assertIsNotNone(alice.started_at)

    def test_mark_completed(self):
        mgr = self._make_manager()
        mgr.mark_in_progress("alice@old.com")
        mgr.mark_completed("alice@old.com")
        self.assertEqual(mgr.completed_count, 1)
        pending = mgr.get_pending()
        self.assertEqual(len(pending), 2)

    def test_mark_failed(self):
        mgr = self._make_manager()
        mgr.mark_in_progress("bob@old.com")
        mgr.mark_failed("bob@old.com", "Test error")
        self.assertEqual(mgr.failed_count, 1)
        pending = mgr.get_pending()
        bob = [cp for cp in pending if cp.old_username == "bob@old.com"][0]
        self.assertEqual(bob.status, CheckpointStatus.FAILED)
        self.assertEqual(bob.error, "Test error")

    def test_step_tracking(self):
        mgr = self._make_manager()
        self.assertFalse(mgr.is_step_completed("alice@old.com", "clone_permissions"))
        mgr.mark_step_completed("alice@old.com", "clone_permissions")
        self.assertTrue(mgr.is_step_completed("alice@old.com", "clone_permissions"))
        self.assertFalse(mgr.is_step_completed("alice@old.com", "clone_groups"))

    def test_step_not_duplicated(self):
        mgr = self._make_manager()
        mgr.mark_step_completed("alice@old.com", "step1")
        mgr.mark_step_completed("alice@old.com", "step1")
        all_cps = mgr.get_all()
        alice = [cp for cp in all_cps if cp.old_username == "alice@old.com"][0]
        self.assertEqual(alice.steps_completed.count("step1"), 1)

    def test_save_and_load(self):
        mgr = self._make_manager()
        mgr.mark_in_progress("alice@old.com")
        mgr.mark_step_completed("alice@old.com", "create_user")
        mgr.mark_completed("alice@old.com")
        mgr.mark_failed("bob@old.com", "oops")

        checkpoint_file = self.tmpdir / "checkpoint_20260413_120000.json"
        self.assertTrue(checkpoint_file.exists())

        new_mgr = CheckpointManager()
        new_mgr.load(checkpoint_file)
        self.assertEqual(new_mgr.total, 3)
        self.assertEqual(new_mgr.completed_count, 1)
        self.assertEqual(new_mgr.failed_count, 1)
        pending = new_mgr.get_pending()
        self.assertEqual(len(pending), 2)
        self.assertTrue(new_mgr.is_step_completed("alice@old.com", "create_user"))

    def test_load_nonexistent_raises(self):
        mgr = CheckpointManager()
        with self.assertRaises(FileNotFoundError):
            mgr.load(Path("/nonexistent/file.json"))

    def test_find_latest_with_pending(self):
        mgr = self._make_manager()
        latest = CheckpointManager.find_latest(self.tmpdir)
        self.assertIsNotNone(latest)
        self.assertTrue(latest.name.startswith("checkpoint_"))

    def test_find_latest_all_completed(self):
        mgr = self._make_manager()
        for m in self.mappings:
            mgr.mark_completed(m["old_username"])

        latest = CheckpointManager.find_latest(self.tmpdir)
        self.assertIsNone(latest)

    def test_find_latest_nonexistent_dir(self):
        result = CheckpointManager.find_latest(Path("/nonexistent/dir"))
        self.assertIsNone(result)

    def test_summary(self):
        mgr = self._make_manager()
        mgr.mark_completed("alice@old.com")
        mgr.mark_failed("bob@old.com", "err")
        summary = mgr.summary()
        self.assertIn("completed: 1", summary)
        self.assertIn("failed: 1", summary)
        self.assertIn("pending: 1", summary)

    def test_get_all(self):
        mgr = self._make_manager()
        all_cps = mgr.get_all()
        self.assertEqual(len(all_cps), 3)
        names = {cp.old_username for cp in all_cps}
        self.assertEqual(names, {"alice@old.com", "bob@old.com", "charlie@old.com"})

    def test_mark_nonexistent_user_no_error(self):
        mgr = self._make_manager()
        mgr.mark_in_progress("nonexistent@old.com")
        mgr.mark_completed("nonexistent@old.com")
        mgr.mark_failed("nonexistent@old.com", "err")
        mgr.mark_step_completed("nonexistent@old.com", "step")
        self.assertFalse(mgr.is_step_completed("nonexistent@old.com", "step"))


if __name__ == "__main__":
    unittest.main()

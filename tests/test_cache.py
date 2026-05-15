import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.utils.cache import DimensionCache, DimensionRecord, owner_filter, user_filter


class TestDimensionRecord(unittest.TestCase):
    def test_defaults(self):
        r = DimensionRecord(id="1")
        self.assertEqual(r.id, "1")
        self.assertIsNone(r.type)
        self.assertIsNone(r.license_type)
        self.assertIsNone(r.name)
        self.assertEqual(r.attrs, {})


class TestDimensionCachePopulate(unittest.TestCase):
    def setUp(self):
        self.cache = DimensionCache()

    def test_populate_workbooks(self):
        items = [
            {"id": "wb-1", "name": "WB1", "contentUrl": "wb1", "owner": {"id": "u-1"}, "project": {"id": "p-1"}},
            {"id": "wb-2", "name": "WB2", "contentUrl": "wb2", "owner": {"id": "u-2"}, "project": {"id": "p-1"}},
        ]
        count = self.cache.populate("workbooks", items)
        self.assertEqual(count, 2)
        self.assertTrue(self.cache.has_dimension("workbooks"))
        self.assertEqual(self.cache.count("workbooks"), 2)

    def test_populate_users(self):
        items = [
            {"id": "u-1", "name": "alice@test.com", "siteRole": "Creator", "email": "alice@test.com"},
            {"id": "u-2", "name": "bob@test.com", "siteRole": "Viewer"},
        ]
        count = self.cache.populate("users", items)
        self.assertEqual(count, 2)

        record = self.cache.get_record("users", "u-1")
        self.assertIsNotNone(record)
        self.assertEqual(record.name, "alice@test.com")
        self.assertEqual(record.type, "Creator")
        self.assertEqual(record.attrs.get("email"), "alice@test.com")

    def test_populate_unknown_endpoint_skipped(self):
        count = self.cache.populate("unknown_endpoint", [{"id": "1", "name": "x"}])
        self.assertEqual(count, 0)
        self.assertFalse(self.cache.has_dimension("unknown_endpoint"))

    def test_populate_items_without_id_skipped(self):
        items = [{"name": "no-id"}, {"id": "valid", "name": "has-id"}]
        count = self.cache.populate("users", items)
        self.assertEqual(count, 1)

    def test_populate_replaces_previous(self):
        self.cache.populate("users", [{"id": "u-1", "name": "alice"}])
        self.assertEqual(self.cache.count("users"), 1)
        self.cache.populate("users", [{"id": "u-2", "name": "bob"}, {"id": "u-3", "name": "charlie"}])
        self.assertEqual(self.cache.count("users"), 2)
        self.assertIsNone(self.cache.get_record("users", "u-1"))


class TestDimensionCacheGetIds(unittest.TestCase):
    def setUp(self):
        self.cache = DimensionCache()
        self.cache.populate("workbooks", [
            {"id": "wb-1", "name": "WB1", "contentUrl": "wb1", "owner": {"id": "u-1"}, "project": {"id": "p-1"}},
            {"id": "wb-2", "name": "WB2", "contentUrl": "wb2", "owner": {"id": "u-2"}, "project": {"id": "p-1"}},
            {"id": "wb-3", "name": "WB3", "contentUrl": "wb3", "owner": {"id": "u-1"}, "project": {"id": "p-2"}},
        ])

    def test_get_all_ids(self):
        ids = self.cache.get_ids("workbooks")
        self.assertEqual(sorted(ids), ["wb-1", "wb-2", "wb-3"])

    def test_get_ids_nonexistent_dimension(self):
        ids = self.cache.get_ids("nonexistent")
        self.assertEqual(ids, [])

    def test_get_ids_with_owner_filter(self):
        ids = self.cache.get_ids("workbooks", filter_fn=owner_filter("u-1"))
        self.assertEqual(sorted(ids), ["wb-1", "wb-3"])

    def test_get_ids_with_owner_filter_no_match(self):
        ids = self.cache.get_ids("workbooks", filter_fn=owner_filter("u-999"))
        self.assertEqual(ids, [])


class TestOwnerFilter(unittest.TestCase):
    def test_owner_filter_matches(self):
        r = DimensionRecord(id="1", attrs={"owner": {"id": "u-1"}})
        self.assertTrue(owner_filter("u-1")(r))
        self.assertFalse(owner_filter("u-2")(r))

    def test_owner_filter_no_owner(self):
        r = DimensionRecord(id="1", attrs={})
        self.assertFalse(owner_filter("u-1")(r))

    def test_owner_filter_non_dict_owner(self):
        r = DimensionRecord(id="1", attrs={"owner": "string-value"})
        self.assertFalse(owner_filter("u-1")(r))


class TestUserFilter(unittest.TestCase):
    def test_user_filter_matches(self):
        r = DimensionRecord(id="1", attrs={"user": {"id": "u-1"}})
        self.assertTrue(user_filter("u-1")(r))
        self.assertFalse(user_filter("u-2")(r))

    def test_user_filter_no_user(self):
        r = DimensionRecord(id="1", attrs={})
        self.assertFalse(user_filter("u-1")(r))


class TestDimensionCacheGetRecord(unittest.TestCase):
    def setUp(self):
        self.cache = DimensionCache()
        self.cache.populate("users", [{"id": "u-1", "name": "alice", "siteRole": "Creator"}])

    def test_get_existing(self):
        r = self.cache.get_record("users", "u-1")
        self.assertIsNotNone(r)
        self.assertEqual(r.name, "alice")
        self.assertEqual(r.type, "Creator")

    def test_get_nonexistent_id(self):
        self.assertIsNone(self.cache.get_record("users", "u-999"))

    def test_get_nonexistent_dimension(self):
        self.assertIsNone(self.cache.get_record("nonexistent", "u-1"))


class TestDimensionCacheGetAllRecords(unittest.TestCase):
    def test_returns_all(self):
        cache = DimensionCache()
        cache.populate("groups", [{"id": "g-1", "name": "G1"}, {"id": "g-2", "name": "G2"}])
        records = cache.get_all_records("groups")
        self.assertEqual(len(records), 2)

    def test_empty_dimension(self):
        cache = DimensionCache()
        self.assertEqual(cache.get_all_records("nonexistent"), [])


class TestDimensionCacheFilterIds(unittest.TestCase):
    def test_filter_ids(self):
        cache = DimensionCache()
        cache.populate("workbooks", [
            {"id": "wb-1", "name": "W1", "contentUrl": "w1", "owner": {"id": "u-1"}},
            {"id": "wb-2", "name": "W2", "contentUrl": "w2", "owner": {"id": "u-2"}},
        ])
        result = cache.filter_ids("workbooks", ["wb-1", "wb-2"], owner_filter("u-1"))
        self.assertEqual(result, ["wb-1"])

    def test_filter_ids_nonexistent_dimension(self):
        cache = DimensionCache()
        result = cache.filter_ids("nonexistent", ["a", "b"], owner_filter("u-1"))
        self.assertEqual(result, ["a", "b"])


class TestDimensionCachePopulateChild(unittest.TestCase):
    def setUp(self):
        self.cache = DimensionCache()

    def test_populate_group_users(self):
        items = [
            {"id": "u-1", "name": "alice", "siteRole": "Creator"},
            {"id": "u-2", "name": "bob", "siteRole": "Viewer"},
        ]
        count = self.cache.populate_child("group_users", "g-1", items)
        self.assertEqual(count, 2)

    def test_get_child_records(self):
        self.cache.populate_child("group_users", "g-1", [
            {"id": "u-1", "name": "alice"},
            {"id": "u-2", "name": "bob"},
        ])
        self.cache.populate_child("group_users", "g-2", [
            {"id": "u-3", "name": "charlie"},
        ])

        g1_records = self.cache.get_child_records("group_users", "g-1")
        self.assertEqual(len(g1_records), 2)

        g2_records = self.cache.get_child_records("group_users", "g-2")
        self.assertEqual(len(g2_records), 1)

    def test_get_child_ids(self):
        self.cache.populate_child("group_users", "g-1", [
            {"id": "u-1", "name": "alice"},
            {"id": "u-2", "name": "bob"},
        ])
        ids = self.cache.get_child_ids("group_users", "g-1")
        self.assertEqual(sorted(ids), ["u-1", "u-2"])

    def test_get_child_records_empty(self):
        records = self.cache.get_child_records("group_users", "g-999")
        self.assertEqual(records, [])

    def test_populate_child_favorites(self):
        items = [
            {"id": "wb-1", "label": "My WB", "workbook": {"id": "wb-1", "name": "Workbook 1"}},
        ]
        count = self.cache.populate_child("user_favorites", "u-1", items)
        self.assertEqual(count, 1)

        records = self.cache.get_child_records("user_favorites", "u-1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "My WB")
        self.assertIn("workbook", records[0].attrs)


class TestDimensionCacheInvalidateOwner(unittest.TestCase):
    def test_invalidate(self):
        cache = DimensionCache()
        cache.populate("workbooks", [
            {"id": "wb-1", "name": "W1", "contentUrl": "w1", "owner": {"id": "u-1"}},
        ])
        ids_before = cache.get_ids("workbooks", filter_fn=owner_filter("u-1"))
        self.assertEqual(ids_before, ["wb-1"])

        cache.invalidate_owner("workbooks", "wb-1", "u-2")

        ids_after_old = cache.get_ids("workbooks", filter_fn=owner_filter("u-1"))
        self.assertEqual(ids_after_old, [])

        ids_after_new = cache.get_ids("workbooks", filter_fn=owner_filter("u-2"))
        self.assertEqual(ids_after_new, ["wb-1"])

    def test_invalidate_nonexistent(self):
        cache = DimensionCache()
        cache.invalidate_owner("workbooks", "wb-999", "u-2")


class TestDimensionCacheClear(unittest.TestCase):
    def test_clear_all(self):
        cache = DimensionCache()
        cache.populate("users", [{"id": "u-1", "name": "a"}])
        cache.populate("groups", [{"id": "g-1", "name": "b"}])
        cache.clear()
        self.assertEqual(cache.total_records, 0)

    def test_clear_specific(self):
        cache = DimensionCache()
        cache.populate("users", [{"id": "u-1", "name": "a"}])
        cache.populate("groups", [{"id": "g-1", "name": "b"}])
        cache.clear("users")
        self.assertFalse(cache.has_dimension("users"))
        self.assertTrue(cache.has_dimension("groups"))


class TestDimensionCachePersistence(unittest.TestCase):
    def test_save_and_load(self):
        cache = DimensionCache()
        cache.populate("workbooks", [
            {"id": "wb-1", "name": "WB1", "contentUrl": "wb1", "owner": {"id": "u-1"}},
            {"id": "wb-2", "name": "WB2", "contentUrl": "wb2", "owner": {"id": "u-2"}},
        ])
        cache.populate("users", [
            {"id": "u-1", "name": "alice", "siteRole": "Creator"},
        ])

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"
            cache.save(path)
            self.assertTrue(path.exists())

            loaded = DimensionCache.load(path, ttl_hours=24)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.total_records, 3)
            self.assertEqual(loaded.count("workbooks"), 2)
            self.assertEqual(loaded.count("users"), 1)

            record = loaded.get_record("workbooks", "wb-1")
            self.assertEqual(record.name, "WB1")

    def test_load_nonexistent(self):
        result = DimensionCache.load(Path("/nonexistent/cache.json"))
        self.assertIsNone(result)

    def test_load_expired(self):
        cache = DimensionCache(ttl_hours=0)
        cache.populate("users", [{"id": "u-1", "name": "a"}])
        cache._created_at = cache._created_at.__class__(2020, 1, 1)

        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"
            cache.save(path)

            loaded = DimensionCache.load(path, ttl_hours=1)
            self.assertIsNone(loaded)


class TestDimensionCacheSummary(unittest.TestCase):
    def test_summary(self):
        cache = DimensionCache()
        cache.populate("users", [{"id": "u-1", "name": "a"}])
        cache.populate("groups", [{"id": "g-1", "name": "b"}, {"id": "g-2", "name": "c"}])
        s = cache.summary()
        self.assertEqual(s["users"], 1)
        self.assertEqual(s["groups"], 2)

    def test_total_records(self):
        cache = DimensionCache()
        self.assertEqual(cache.total_records, 0)
        cache.populate("users", [{"id": "u-1", "name": "a"}])
        self.assertEqual(cache.total_records, 1)


if __name__ == "__main__":
    unittest.main()

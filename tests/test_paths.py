import unittest

from src.utils.paths import resolve_endpoint_path, resolve_element_tag


class TestResolveEndpointPath(unittest.TestCase):
    def test_site_scoped(self):
        result = resolve_endpoint_path("sites/{site_id}/workbooks", "abc-123")
        self.assertEqual(result, "/sites/abc-123/workbooks")

    def test_site_scoped_with_id(self):
        result = resolve_endpoint_path("sites/{site_id}/workbooks/{id}/permissions", "site-1", id="wb-1")
        self.assertEqual(result, "/sites/site-1/workbooks/wb-1/permissions")

    def test_dash_prefix(self):
        result = resolve_endpoint_path("-/collections", "site-1")
        self.assertEqual(result, "/-/collections")

    def test_dash_prefix_with_luid(self):
        result = resolve_endpoint_path("-/collections/{luid}/items", "site-1", luid="col-1")
        self.assertEqual(result, "/-/collections/col-1/items")

    def test_already_has_leading_slash(self):
        result = resolve_endpoint_path("/sites/{site_id}/users", "site-1")
        self.assertEqual(result, "/sites/site-1/users")

    def test_no_double_slash(self):
        result = resolve_endpoint_path("sites/{site_id}/flows", "s1")
        self.assertNotIn("//", result)


class TestResolveElementTag(unittest.TestCase):
    def test_dotted_response_key(self):
        result = resolve_element_tag({"response_key": "workbooks.workbook"}, "workbooks")
        self.assertEqual(result, "workbook")

    def test_simple_response_key(self):
        result = resolve_element_tag({"response_key": "permissions"}, "workbook_permissions")
        self.assertEqual(result, "permissions")

    def test_null_response_key_uses_ep_name(self):
        result = resolve_element_tag({"response_key": None}, "custom_views")
        self.assertEqual(result, "custom_views")

    def test_missing_response_key_uses_ep_name(self):
        result = resolve_element_tag({}, "projects")
        self.assertEqual(result, "projects")

    def test_deeply_dotted(self):
        result = resolve_element_tag({"response_key": "a.b.c"}, "x")
        self.assertEqual(result, "c")


if __name__ == "__main__":
    unittest.main()

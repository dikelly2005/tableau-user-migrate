import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.utils.csv_loader import load_user_mappings
from src.utils.exceptions import ValidationError


class TestLoadUserMappings(unittest.TestCase):
    def _write_csv(self, tmpdir: Path, content: str) -> Path:
        p = tmpdir / "test.csv"
        p.write_text(content, encoding="utf-8")
        return p

    def test_valid_csv(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\nalice@old.com,alice@new.com\nbob@old.com,bob@new.com\n")
            mappings = load_user_mappings(p)
            self.assertEqual(len(mappings), 2)
            self.assertEqual(mappings[0]["old_username"], "alice@old.com")
            self.assertEqual(mappings[1]["new_username"], "bob@new.com")

    def test_case_insensitive_normalization(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\nAlice@Old.Com,Alice@New.Com\n")
            mappings = load_user_mappings(p)
            self.assertEqual(mappings[0]["old_username"], "alice@old.com")
            self.assertEqual(mappings[0]["new_username"], "alice@new.com")

    def test_mixed_case_dedup_detected(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\nalice@old.com,alice@new.com\nALICE@OLD.COM,alice2@new.com\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("duplicate", str(ctx.exception).lower())

    def test_file_not_found(self):
        with self.assertRaises(ValidationError) as ctx:
            load_user_mappings(Path("/nonexistent/test.csv"))
        self.assertIn("not found", str(ctx.exception))

    def test_empty_csv(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("no user mappings", str(ctx.exception).lower())

    def test_missing_columns(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "email,target\na@b.com,c@d.com\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("missing required columns", str(ctx.exception).lower())

    def test_invalid_email_old(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\nnot-an-email,valid@new.com\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("invalid email", str(ctx.exception).lower())

    def test_invalid_email_new(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\nvalid@old.com,not-an-email\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("invalid email", str(ctx.exception).lower())

    def test_identical_usernames(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\nalice@test.com,alice@test.com\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("identical", str(ctx.exception).lower())

    def test_identical_usernames_case_insensitive(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\nalice@test.com,ALICE@TEST.COM\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("identical", str(ctx.exception).lower())

    def test_duplicate_old_username(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\na@b.com,c@d.com\na@b.com,e@f.com\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("duplicate old_username", str(ctx.exception).lower())

    def test_duplicate_new_username(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\na@b.com,c@d.com\nx@y.com,c@d.com\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("duplicate new_username", str(ctx.exception).lower())

    def test_circular_reference(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\na@b.com,c@d.com\nc@d.com,a@b.com\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("circular", str(ctx.exception).lower())

    def test_chain_depth_exceeded(self):
        with TemporaryDirectory() as tmpdir:
            rows = ["old_username,new_username"]
            for i in range(7):
                rows.append(f"user{i}@test.com,user{i+1}@test.com")
            p = self._write_csv(Path(tmpdir), "\n".join(rows) + "\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("chain depth", str(ctx.exception).lower())

    def test_blank_rows_skipped(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\na@b.com,c@d.com\n,\nx@y.com,z@w.com\n")
            mappings = load_user_mappings(p)
            self.assertEqual(len(mappings), 2)

    def test_partial_row_raises(self):
        with TemporaryDirectory() as tmpdir:
            p = self._write_csv(Path(tmpdir), "old_username,new_username\na@b.com,\n")
            with self.assertRaises(ValidationError) as ctx:
                load_user_mappings(p)
            self.assertIn("both old_username and new_username", str(ctx.exception).lower())

    def test_bom_handling(self):
        with TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "test.csv"
            p.write_bytes(b"\xef\xbb\xbfold_username,new_username\nalice@old.com,alice@new.com\n")
            mappings = load_user_mappings(p)
            self.assertEqual(len(mappings), 1)
            self.assertEqual(mappings[0]["old_username"], "alice@old.com")


if __name__ == "__main__":
    unittest.main()

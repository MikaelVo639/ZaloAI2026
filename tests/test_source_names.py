import unicodedata
import unittest

from datamate.ingestion.source_names import filename_key, is_family, matches_source


class SourceNameTests(unittest.TestCase):
    def test_unicode_and_legacy_zip_encodings_share_one_lookup_key(self):
        expected = "Danh sách tổng hợp.xlsx"
        for name in (expected, unicodedata.normalize("NFD", expected),
                     expected.encode("utf-8").decode("cp437"),
                     unicodedata.normalize("NFD", expected.encode("utf-8").decode("cp437"))):
            with self.subTest(name=name):
                self.assertEqual(filename_key(name), expected)

    def test_ascii_and_unrelated_names_remain_distinct(self):
        self.assertEqual(filename_key("dir\\sales.csv"), "sales.csv")
        self.assertNotEqual(filename_key("Bảng A.xlsx"), filename_key("Bang A.xlsx"))


class SourceFamilyTests(unittest.TestCase):
    """A README may name one file per period with placeholders (synthetic names)."""

    def test_date_placeholders_match_digits_only(self):
        family = "doanh_so_T<tháng>_<năm>.xlsx"
        self.assertTrue(is_family(family))
        for name in ("doanh_so_T1_2025.xlsx", "doanh_so_T12_2026.xlsx",
                     unicodedata.normalize("NFD", "doanh_so_T3_2026.xlsx")):
            self.assertTrue(matches_source(family, name), name)
        for name in ("doanh_so_TongHop_2025.xlsx", "doanh_so_T1_2025.csv",
                     "doanh_so.xlsx", "x_doanh_so_T1_2025.xlsx"):
            self.assertFalse(matches_source(family, name), name)

    def test_other_placeholders_and_plain_names(self):
        self.assertTrue(matches_source("kho_<chi nhánh>.csv", "kho_quan 1.csv"))
        self.assertFalse(is_family("ban_hang.csv"))
        self.assertTrue(matches_source("ban_hang.csv", "ban_hang.csv"))
        self.assertFalse(matches_source("ban_hang.csv", "ban_hang_T1_2025.csv"))

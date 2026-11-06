import unicodedata
import unittest

from datamate.ingestion.source_names import filename_key


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

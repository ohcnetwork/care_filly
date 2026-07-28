"""Tests for the `care_filly` package layout."""

import unittest


class TestCareFilly(unittest.TestCase):
    def test_version(self):
        import care_filly

        self.assertTrue(care_filly.__version__)

    def test_plugin_name(self):
        from care_filly.apps import PLUGIN_NAME

        self.assertEqual(PLUGIN_NAME, "care_filly")


if __name__ == "__main__":
    unittest.main()

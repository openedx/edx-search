"""Tests for utility functions"""
import unittest

from search.utils import normalize_bool


class TestNormalizeBool(unittest.TestCase):
    """Test cases for normalize_bool"""

    def test_boolean_values(self):
        cases = [
            (True, True),
            (False, False),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                assert normalize_bool(value) is expected

    def test_string_truthy_values(self):
        truthy_strings = ['y', 'Y', 'yes', 'YES', 't', 'T', 'true', 'TRUE', 'on', 'ON', '1']
        for value in truthy_strings:
            with self.subTest(value=value):
                assert normalize_bool(value) is True

    def test_string_falsy_values(self):
        falsy_strings = ['n', 'N', 'no', 'NO', 'f', 'F', 'false', 'FALSE', 'off', 'OFF', '0']
        for value in falsy_strings:
            with self.subTest(value=value):
                assert normalize_bool(value) is False

    def test_invalid_string_values(self):
        invalid_values = ['invalid', '10']
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_bool(value)

    def test_other_types(self):
        cases = [
            (1, True),
            (0, False),
            (100, True),
            ([], False),
            ([1, 2, 3], True),
            ({}, False),
            ({'key': 'value'}, True),
            (None, False),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                assert normalize_bool(value) is expected

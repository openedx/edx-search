"""Tests for utility functions in search.utils module."""

import unittest
from ddt import ddt, data, unpack

from search.utils import normalize_bool


@ddt
class TestNormalizeBool(unittest.TestCase):
    """Test cases for normalize_bool function."""

    @data(
        (True, True),
        (False, False),
    )
    @unpack
    def test_boolean_values(self, value, expected):
        assert normalize_bool(value) is expected

    @data('y', 'Y', 'yes', 'YES', 't', 'T', 'true', 'TRUE', 'on', 'ON', '1')
    def test_string_truthy_values(self, value):
        assert normalize_bool(value) is True

    @data('n', 'N', 'no', 'NO', 'f', 'F', 'false', 'FALSE', 'off', 'OFF', '0')
    def test_string_falsy_values(self, value):
        assert normalize_bool(value) is False

    @data('invalid', '10')
    def test_invalid_string_values(self, value):
        with self.assertRaises(ValueError):
            normalize_bool(value)

    @data(
        (1, True),
        (0, False),
        (100, True),
        ([], False),
        ([1, 2, 3], True),
        ({}, False),
        ({'key': 'value'}, True),
        (None, False),
    )
    @unpack
    def test_other_types(self, value, expected):
        assert normalize_bool(value) is expected

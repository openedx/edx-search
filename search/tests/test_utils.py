"""
Test for the utility functions shared by multiple search engines.
"""
import unittest

from ddt import ddt, data, unpack
import django.test
from django.utils import timezone

from search.utils import convert_doc_datatypes, normalize_bool, restore_doc_datatypes


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


class EngineUtilsTests(django.test.TestCase):
    """
    Utils tests.
    """

    def test_convert_empty_document(self):
        assert not convert_doc_datatypes({})

    def test_convert_document_recursive(self):
        """
        Test converting a document to Meilisearch/Typesense compatible format
        """
        now = timezone.datetime(2025, 8, 25)
        document = {
            "timestamp": now,
            "dict_field": {
                "inner_value": timezone.datetime(2024, 1, 1),
            },
        }
        processed = convert_doc_datatypes(document)
        assert {
            "timestamp": 1756080000.0,
            "timestamp__utcoffset": 0,
            "dict_field": {
                "inner_value": 1704067200.0,
                "inner_value__utcoffset": 0,
            }
        } == processed

    def test_convert_datetime_with_tz(self):
        """
        Test converting a document to Meilisearch/Typesense compatible format,
        including non-UTC datetimes.
        """
        # With timezone
        document = {
            "id": "1",
            "dt": timezone.datetime(
                2024,
                1,
                1,
                tzinfo=timezone.get_fixed_timezone(timezone.timedelta(seconds=3600)),
            ),
        }
        processed = convert_doc_datatypes(document)
        assert 1704063600.0 == processed["dt"]
        assert 3600 == processed["dt__utcoffset"]
        # reverse serialisation
        reverse = restore_doc_datatypes(processed)
        assert document == reverse

    def test_convert_document_with_null(self):
        """
        Test converting a document to Meilisearch/Typesense compatible format,
        including NULL values
        """
        document = {
            "foo": "bar",
            "count": 17,
            "null_value": None,
            "dict_value": {
                "null_value": None,
                "bar": "foo",
            },
        }
        processed = convert_doc_datatypes(document)
        assert {
            "foo": "bar",
            "count": 17,
            # null_value removed
            "dict_value": {
                # null_value removed
                "bar": "foo",
            },
        } == processed

    def test_convert_document_with_null_separate(self):
        """
        Test converting a document to Typesense compatible format, marking NULL
        values using a separate field as recommended at
        https://typesense.org/docs/guide/tips-for-searching-common-types-of-data.html#searching-for-null-or-empty-values
        """
        document = {
            "foo": "bar",
            "count": 17,
            "null_value": None,
            "dict_value": {
                "null_value": None,
                "bar": "foo",
            },
        }
        processed = convert_doc_datatypes(document, record_nulls=True)
        assert {
            "foo": "bar",
            "count": 17,
            "null_value__is_null": True,
            "dict_value": {
                "null_value__is_null": True,
                "bar": "foo",
            },
        } == processed
        assert document == restore_doc_datatypes(processed)

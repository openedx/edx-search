""" Tests for MockSearchEngine specific features """

from datetime import datetime

import pytz
from django.test import TestCase
from django.test.utils import override_settings

from search.tests.mock_search_engine import _find_field, _filter_intersection, json_date_to_datetime
from search.tests.utils import SearcherMixin
from search.utils import DateRange


# Any class that inherits from TestCase will cause too-many-public-methods pylint error
# pylint: disable=too-many-public-methods


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
class MockSpecificSearchTests(TestCase, SearcherMixin):
    """ For testing pieces of the Mock Engine that have no equivalent in Elastic """

    def test_find_field_arguments(self):
        """ test that field argument validity is observed """
        field_value = _find_field(
            {
                "name": "Come and listen to my story"
            },
            "name"
        )
        self.assertEqual(field_value, "Come and listen to my story")

        field_value = _find_field(
            {
                "name": {
                    "first": "Martyn",
                    "last": "James"
                }
            },
            "name.first"
        )
        self.assertEqual(field_value, "Martyn")

        field_value = _find_field(
            {
                "name": {
                    "first": "Monica",
                    "last": {
                        "one": "Parker",
                        "two": "James"
                    }
                }
            },
            "name.last.two"
        )
        self.assertEqual(field_value, "James")

        with self.assertRaises(ValueError):
            field_value = _find_field(
                {
                    "name": "Come and listen to my story"
                },
                123
            )

        with self.assertRaises(ValueError):
            field_value = _find_field(123, "name")

    def test_filter_optimization(self):
        """ Make sure that intersection optimizes return when no filter dictionary is provided """
        test_docs = [{"A": {"X": 1, "Y": 2, "Z": 3}}, {"B": {"X": 9, "Y": 8, "Z": 7}}]
        self.assertTrue(_filter_intersection(test_docs, None), test_docs)

    def test_datetime_conversion(self):
        """ tests json_date_to_datetime with different formats """
        json_date = "2015-01-31"
        self.assertTrue(json_date_to_datetime(json_date), datetime(2015, 1, 31))

        json_datetime = "2015-01-31T07:30:28"
        self.assertTrue(json_date_to_datetime(json_datetime), datetime(2015, 1, 31, 7, 30, 28))

        json_datetime = "2015-01-31T07:30:28.65785"
        self.assertTrue(json_date_to_datetime(json_datetime), datetime(2015, 1, 31, 7, 30, 28, 65785))

        json_datetime = "2015-01-31T07:30:28Z"
        self.assertTrue(json_date_to_datetime(json_datetime), datetime(2015, 1, 31, 7, 30, 28))

        json_datetime = "2015-01-31T07:30:28.65785Z"
        self.assertTrue(json_date_to_datetime(json_datetime), datetime(2015, 1, 31, 7, 30, 28, 65785))

    def test_timezone_conversion(self):
        """
        Tests internal operation of mock when range values have timezone, and query objects do not, and vice versa
        """
        # first where index has no timezones, and query has
        low_date = datetime(2010, 1, 1)
        high_date = datetime(2100, 1, 1)
        self.searcher.index([
            {"id": "FAKE_ID_1", "test_value": "1", "start_date": low_date},
            {"id": "FAKE_ID_2", "test_value": "2", "start_date": high_date},
        ])

        response = self.searcher.search(
            field_dictionary={"start_date": low_date.replace(tzinfo=pytz.UTC)}
        )
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(
            field_dictionary={"start_date": DateRange(None, datetime.utcnow().replace(tzinfo=pytz.UTC))}
        )
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(
            field_dictionary={"start_date": DateRange(datetime(2099, 1, 1).replace(tzinfo=pytz.UTC), None)}
        )
        self.assertEqual(response["total"], 1)

        self.searcher.destroy()
        self.searcher.index([
            {"id": "FAKE_ID_1", "test_value": "1", "start_date": low_date.replace(tzinfo=pytz.UTC)},
            {"id": "FAKE_ID_2", "test_value": "2", "start_date": high_date.replace(tzinfo=pytz.UTC)},
        ])

        response = self.searcher.search(field_dictionary={"start_date": low_date})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(field_dictionary={"start_date": DateRange(None, datetime.utcnow())})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(field_dictionary={"start_date": DateRange(datetime(2099, 1, 1), None)})
        self.assertEqual(response["total"], 1)

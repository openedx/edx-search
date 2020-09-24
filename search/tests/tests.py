#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-ancestors
""" Tests for search functionalty """

from datetime import datetime

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings
from elasticsearch import Elasticsearch

from search.search_engine_base import SearchEngine
from search.elastic import ElasticSearchEngine
from search.tests.utils import SearcherMixin, TEST_INDEX_NAME
from search.utils import ValueRange, DateRange

from .mock_search_engine import MockSearchEngine


# Any class that inherits from TestCase will cause too-many-public-methods pylint error
# pylint: disable=too-many-public-methods
@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
@override_settings(MOCK_SEARCH_BACKING_FILE=None)
class MockSearchTests(TestCase, SearcherMixin):
    """ Test operation of search activities """
    @property
    def _is_elastic(self):
        """ check search engine implementation, to manage cleanup differently """
        return isinstance(self.searcher, ElasticSearchEngine)

    def setUp(self):
        super(MockSearchTests, self).setUp()
        # ignore unexpected-keyword-arg; ES python client documents that it can be used
        # pylint: disable=unexpected-keyword-arg
        if self._is_elastic:
            _elasticsearch = Elasticsearch()
            # Make sure that we are fresh
            _elasticsearch.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])

            config_body = {}
            # ignore unexpected-keyword-arg; ES python client documents that it can be used
            _elasticsearch.indices.create(index=TEST_INDEX_NAME, ignore=400, body=config_body)
        else:
            MockSearchEngine.destroy()
        self._searcher = None
        cache.clear()

    def tearDown(self):
        # ignore unexpected-keyword-arg; ES python client documents that it can be used
        # pylint: disable=unexpected-keyword-arg
        if self._is_elastic:
            _elasticsearch = Elasticsearch()
            # ignore unexpected-keyword-arg; ES python client documents that it can be used
            _elasticsearch.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])
        else:
            MockSearchEngine.destroy()

        self._searcher = None
        super(MockSearchTests, self).tearDown()

    def test_factory_creator(self):
        """ Make sure that search object implements SearchEngine interface """
        self.assertTrue(isinstance(self.searcher, SearchEngine))

    def test_abstract_impl(self):
        """ Make sure that if one tries to use the abstract base, then operations yeild NotImplementedError s """
        abstract = SearchEngine("test_index_name")
        test_string = "A test string"
        with self.assertRaises(NotImplementedError):
            abstract.index([{"name": test_string}])
        with self.assertRaises(NotImplementedError):
            abstract.search(test_string)
        with self.assertRaises(NotImplementedError):
            abstract.remove(["test_id"])

    def test_find_all(self):
        """ Make sure that null search finds everything in the index """
        test_string = "A test string"
        self.searcher.index([{"name": test_string}])

        # search everything
        response = self.searcher.search(None)
        self.assertEqual(response["total"], 1)
        results = response["results"]
        self.assertEqual(results[0]["data"]["name"], test_string)

        self.searcher.index([{"value": test_string}])

        response = self.searcher.search(None)
        self.assertEqual(response["total"], 2)
        results = response["results"]
        test_0 = results[0]["data"] if "name" in results[0]["data"] else results[1]["data"]
        test_1 = results[1]["data"] if "name" in results[0]["data"] else results[0]["data"]
        self.assertEqual(test_0["name"], test_string)
        self.assertEqual(test_1["value"], test_string)

    def test_find_string(self):
        """ Find a string within the object "content" node """
        test_string = "A test string"
        self.searcher.index([{"content": {"name": test_string}}])

        # search string
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.index([{"content": {"value": test_string}}])

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 2)

        response = self.searcher.search_string("something else")
        self.assertEqual(response["total"], 0)

        self.searcher.index([{"content": {"deep": {"down": test_string}}}])
        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 3)

    def test_field(self):
        """ test matching on a field """
        test_string = "A test string"
        test_object = {
            "name": test_string,
            "tags": {
                "tag_one": "one",
                "tag_two": "two"
            },
            "fieldX": "valueY",
            "id": "12345"
        }
        self.searcher.index([test_object])

        # search tags
        response = self.searcher.search_fields({"tags.tag_one": "one"})
        self.assertEqual(response["total"], 1)

        # search id
        response = self.searcher.search_fields({"id": "12345"})
        self.assertEqual(response["total"], 1)

        # search id
        response = self.searcher.search_fields({"id": "54321"})
        self.assertEqual(response["total"], 0)

        # search tags
        response = self.searcher.search_fields({"tags.tag_one": "one", "tags.tag_two": "two"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"fieldX": "valueY"})
        self.assertEqual(response["total"], 1)

        # search tags
        response = self.searcher.search_fields({"tags.tag_one": "one", "tags.tag_two": "not_two"})
        self.assertEqual(response["total"], 0)

    def test_search_string_and_field(self):
        """ test matching on both string and field value """
        test_object = {
            "content": {
                "name": "You may find me in a coffee shop",
            },
            "course": "A/B/C",
            "abc": "xyz",
        }
        self.searcher.index([test_object])

        response = self.searcher.search(query_string="find me")
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"course": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(query_string="find me", field_dictionary={"course": "X/Y/Z"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(query_string="find me", field_dictionary={"course": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string("find me", field_dictionary={"course": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"course": "A/B/C"}, query_string="find me")
        self.assertEqual(response["total"], 1)

    def test_search_tags(self):
        """ test nested object tags """
        test_object = {
            "name": "John Lester",
            "course_id": "A/B/C",
            "abc": "xyz"
        }
        tags = {
            "color": "red",
            "shape": "square",
            "taste": "sour",
        }
        test_object["tags"] = tags
        self.searcher.index([test_object])

        response = self.searcher.search_fields({"tags.color": "red"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self.searcher.search(field_dictionary={"tags.color": "red"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self.searcher.search(field_dictionary={"tags.color": "blue"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(field_dictionary={"tags.shape": "square"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self.searcher.search(field_dictionary={"tags.shape": "round"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(field_dictionary={"tags.shape": "square", "tags.color": "red"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self._searcher.search(field_dictionary={"tags.shape": "square", "tags.color": "blue"})
        self.assertEqual(response["total"], 0)

    def test_search_array(self):
        """ test nested object array """
        test_object1 = {
            "name": "John Lester",
            "course_id": "A/B/C",
            "array": ["a", "c", "x"]
        }
        test_object2 = {
            "name": "Anthony Rizzo",
            "course_id": "C/D/E",
            "array": ["a", "b", "c"]
        }
        self.searcher.index([test_object1, test_object2])

        response = self.searcher.search(field_dictionary={"array": "x"})
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["results"][0]["data"], test_object1)

        response = self.searcher.search(field_dictionary={"array": "a"})
        self.assertEqual(response["total"], 2)
        self.assertIn(response["results"][0]["data"], [test_object1, test_object2])
        self.assertIn(response["results"][1]["data"], [test_object1, test_object2])

        response = self.searcher.search(field_dictionary={"array": "g"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(field_dictionary={"array": "c"})
        self.assertEqual(response["total"], 2)

    def test_search_any(self):
        """ match any field value in a list """
        test_object1 = {
            "name": "John Lester",
            "course": "A/B/C"
        }
        test_object2 = {
            "name": "Anthony Rizzo",
            "course": "C/D/E"
        }
        self.searcher.index([test_object1, test_object2])

        response = self.searcher.search(field_dictionary={"course": ["x", "y"]})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(field_dictionary={"course": ["C/D/E"]})
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["results"][0]["data"], test_object2)

        response = self.searcher.search(field_dictionary={"course": ["A/B/C", "C/D/E", "x"]})
        self.assertEqual(response["total"], 2)
        self.assertIn(response["results"][0]["data"], [test_object1, test_object2])
        self.assertIn(response["results"][1]["data"], [test_object1, test_object2])

    def test_extended_characters(self):
        """ Make sure that extended character searches work """
        test_string = u"قضايـا هامـة"
        self.searcher.index([{"content": {"name": test_string}}])

        # search string
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.index([{"content": {"value": test_string}}])

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 2)

    def test_delete_item(self):
        """ make sure that we can remove an item from the index """
        test_string = "This is a test of the emergency broadcast system"
        self.searcher.index([{"id": "FAKE_ID", "content": {"name": test_string}}])

        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)
        self.searcher.remove([response["results"][0]["data"]["id"]])
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 0)

    def test_delete_item_slashes(self):
        """ make sure that we can remove an item from the index with complex id """
        test_string = "This is a test of the emergency broadcast system"
        self.searcher.index([
            {
                "id": "i4x://edX/DemoX/google-document/e3369ea4aa0749a7ba29c461d1c819a4",
                "content": {"name": test_string}
            },
        ])

        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.remove(["i4x://edX/DemoX/google-document/e3369ea4aa0749a7ba29c461d1c819a4"])
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 0)

    def test_delete_item_not_present(self):
        """ make sure that we get no error removing an item that does not exist """
        self.searcher.remove(["TOTALLY_FAKE_ID"])

    def test_filter_items(self):
        """ Make sure that filters work """
        self.searcher.index([
            {"id": "FAKE_ID_1", "test_value": "1", "filter_field": "my_filter_value"},
            {"id": "FAKE_ID_2", "test_value": "2"},
        ])

        response = self.searcher.search(filter_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 2)

        response = self.searcher.search(field_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 1)

        self.searcher.index([{"id": "FAKE_ID_3", "test_value": "3", "filter_field": "not_my_filter_value"}])
        response = self.searcher.search(filter_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 2)

        response = self.searcher.search()
        self.assertEqual(response["total"], 3)

    def test_iterable_filters(self):
        """
        Make sure that iterable filters works
        """
        self.searcher.index([
            {"id": "FAKE_ID_1"},
            {"id": "FAKE_ID_2", "filter_field": "orange"},
            {"id": "FAKE_ID_3", "filter_field": ["orange", "blue"]},
        ])

        response = self.searcher.search(filter_dictionary={"filter_field": "orange"})
        self.assertEqual(response["total"], 3)

        response = self.searcher.search(filter_dictionary={"filter_field": ["orange", "green"]})
        self.assertEqual(response["total"], 3)

        response = self.searcher.search(filter_dictionary={"filter_field": ["red", "green"]})
        self.assertEqual(response["total"], 1)

    def test_filter_where_null(self):
        """
        Make sure that filtering with `None` value finds only fields where item
        is not present or where explicitly None
        """
        self.searcher.index([
            {"id": "FAKE_ID_1", "test_value": "1", "filter_field": "my_filter_value"},
            {"id": "FAKE_ID_2", "test_value": "2"},
            {"id": "FAKE_ID_3", "test_value": "3", "filter_field": "not_my_filter_value"},
        ])

        response = self.searcher.search(filter_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 2)

        response = self.searcher.search(filter_dictionary={"filter_field": None})
        self.assertEqual(response["total"], 1)

        self.searcher.index([{"id": "FAKE_ID_4", "test_value": "4", "filter_field": None}])
        response = self.searcher.search(filter_dictionary={"filter_field": None})
        self.assertEqual(response["total"], 2)

    def test_date_range(self):
        """ Make sure that date ranges can be searched """
        self.searcher.index([
            {"id": "FAKE_ID_1", "test_value": "1", "start_date": datetime(2010, 1, 1)},
            {"id": "FAKE_ID_2", "test_value": "2", "start_date": datetime(2100, 1, 1)},
        ])

        response = self.searcher.search()
        self.assertEqual(response["total"], 2)

        response = self.searcher.search(field_dictionary={"start_date": DateRange(None, datetime.utcnow())})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(field_dictionary={"start_date": DateRange(datetime(2099, 1, 1), None)})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(filter_dictionary={"start_date": DateRange(None, datetime.utcnow())})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(filter_dictionary={"start_date": DateRange(datetime(2099, 1, 1), None)})
        self.assertEqual(response["total"], 1)

    def test_numeric_range(self):
        """ Make sure that numeric ranges can be searched with both field and filter queries """
        self.searcher.index([
            {"id": "FAKE_ID_1", "test_value": "1", "age": 20},
            {"id": "FAKE_ID_2", "test_value": "2", "age": 30},
            {"id": "FAKE_ID_3", "test_value": "3", "age": 40},
        ])

        def test_age_range_field(begin, end, expect):
            """ repeated operations consolidated for tests """
            response = self.searcher.search(field_dictionary={"age": ValueRange(begin, end)})
            self.assertEqual(response["total"], expect)

        def test_age_range_filter(begin, end, expect):
            """ repeated operations consolidated for tests """
            response = self.searcher.search(filter_dictionary={"age": ValueRange(begin, end)})
            self.assertEqual(response["total"], expect)

        response = self.searcher.search()
        self.assertEqual(response["total"], 3)

        test_age_range_field(19, 29, 1)
        test_age_range_field(19, 39, 2)
        test_age_range_field(19, 49, 3)
        test_age_range_field(29, 49, 2)
        test_age_range_field(39, 49, 1)
        test_age_range_field(None, 29, 1)
        test_age_range_field(39, None, 1)

        test_age_range_filter(19, 29, 1)
        test_age_range_filter(19, 39, 2)
        test_age_range_filter(19, 49, 3)
        test_age_range_filter(29, 49, 2)
        test_age_range_filter(39, 49, 1)
        test_age_range_filter(None, 29, 1)
        test_age_range_filter(39, None, 1)

        self.searcher.index([{"id": "FAKE_ID_4", "test_value": "4", "age": 50}])

        test_age_range_field(19, 29, 1)
        test_age_range_field(19, 39, 2)
        test_age_range_field(19, 49, 3)
        test_age_range_field(29, 49, 2)
        test_age_range_field(39, 49, 1)
        test_age_range_field(None, 29, 1)
        test_age_range_field(39, None, 2)

        test_age_range_filter(19, 29, 1)
        test_age_range_filter(19, 39, 2)
        test_age_range_filter(19, 49, 3)
        test_age_range_filter(29, 49, 2)
        test_age_range_filter(39, 49, 1)
        test_age_range_filter(None, 29, 1)
        test_age_range_filter(39, None, 2)

        self.searcher.index([{"id": "FAKE_ID_5", "test_value": "5", "not_age": 50}])
        test_age_range_filter(19, 29, 2)
        test_age_range_filter(19, 39, 3)
        test_age_range_filter(19, 49, 4)
        test_age_range_filter(29, 49, 3)
        test_age_range_filter(39, 49, 2)
        test_age_range_filter(None, 29, 2)
        test_age_range_filter(39, None, 3)

    def test_range_filter(self):
        """ Make sure that ranges can be used in field_dictionary and filter_dictionary """
        self.searcher.index([
            {"id": "FAKE_ID_1", "test_value": "1", "age": 20},
            {"id": "FAKE_ID_2", "test_value": "2", "age": 30},
            {"id": "FAKE_ID_3", "test_value": "3", "not_age": 40},
        ])

        response = self.searcher.search()
        self.assertEqual(response["total"], 3)

        response = self.searcher.search(field_dictionary={"age": ValueRange(19, 29)})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(filter_dictionary={"age": ValueRange(19, 29)})
        self.assertEqual(response["total"], 2)

    def test_pagination(self):
        """ Test paging operation """
        self.searcher.index([
            {
                "course": "ABC",
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling Little Darling Little Darling, it's been a long long lonely winter"
                }
            },
            {
                "course": "ABC",
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling Little Darling, it's been a year since you've been gone"
                }
            },
            {
                "course": "XYZ",
                "id": "FAKE_ID_3",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            },
        ])

        response = self.searcher.search(query_string="Little Darling")
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 3)

        response = self.searcher.search(query_string="Little Darling", size=1)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)

        response = self.searcher.search(query_string="Little Darling", size=1, from_=0)
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertEqual(result_ids, ["FAKE_ID_1"])

        response = self.searcher.search(query_string="Little Darling", size=1, from_=1)
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertEqual(result_ids, ["FAKE_ID_2"])

        response = self.searcher.search(query_string="Little Darling", size=1, from_=2)
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertEqual(result_ids, ["FAKE_ID_3"])

        response = self.searcher.search(query_string="Little Darling", size=2)
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertEqual(result_ids, ["FAKE_ID_1", "FAKE_ID_2"])

        response = self.searcher.search(query_string="Little Darling", size=2, from_=0)
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertEqual(result_ids, ["FAKE_ID_1", "FAKE_ID_2"])

        response = self.searcher.search(query_string="Little Darling", size=2, from_=2)
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertEqual(result_ids, ["FAKE_ID_3"])

    def test_exclude_ids(self):
        """ Test that ids that would normally be present in the resultset will not be present if in the exclude list """
        self.searcher.index([
            {"course": "ABC", "id": "FAKE_ID_1"},
            {"course": "ABC", "id": "FAKE_ID_2"},
            {"course": "ABC", "id": "FAKE_ID_3"},
            {"course": "XYZ", "id": "FAKE_ID_11"},
            {"course": "XYZ", "id": "FAKE_ID_12"},
            {"course": "XYZ", "id": "FAKE_ID_13"},
        ])

        response = self.searcher.search()
        self.assertEqual(response["total"], 6)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertIn("FAKE_ID_11", result_ids)
        self.assertIn("FAKE_ID_12", result_ids)
        self.assertIn("FAKE_ID_13", result_ids)

        response = self.searcher.search(
            exclude_ids=["FAKE_ID_1", "FAKE_ID_2", "FAKE_ID_11", "FAKE_ID_12"]
        )
        self.assertEqual(response["total"], 2)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertNotIn("FAKE_ID_1", result_ids)
        self.assertNotIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertNotIn("FAKE_ID_11", result_ids)
        self.assertNotIn("FAKE_ID_12", result_ids)
        self.assertIn("FAKE_ID_13", result_ids)

        response = self.searcher.search(field_dictionary={"course": "ABC"})
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertNotIn("FAKE_ID_11", result_ids)
        self.assertNotIn("FAKE_ID_12", result_ids)
        self.assertNotIn("FAKE_ID_13", result_ids)

        response = self.searcher.search(field_dictionary={"course": "ABC"}, exclude_ids=["FAKE_ID_3", "FAKE_ID_2"])
        self.assertEqual(response["total"], 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertNotIn("FAKE_ID_2", result_ids)
        self.assertNotIn("FAKE_ID_3", result_ids)
        self.assertNotIn("FAKE_ID_11", result_ids)
        self.assertNotIn("FAKE_ID_12", result_ids)
        self.assertNotIn("FAKE_ID_13", result_ids)

    def test_exclude_filter_single(self):
        """ Test that single entries present in the exclude filter are filtered out """
        self.searcher.index([
            {"course": "ABC", "org": "edX", "id": "FAKE_ID_1"},
            {"course": "XYZ", "org": "edX", "id": "FAKE_ID_2"},
            {"course": "LMN", "org": "MITX", "id": "FAKE_ID_3"},
        ])

        response = self.searcher.search()
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)

        response = self.searcher.search(exclude_dictionary={"org": "MITX"})
        self.assertEqual(response["total"], 2)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertNotIn("FAKE_ID_3", result_ids)

        response = self.searcher.search(exclude_dictionary={"org": "edX"})
        self.assertEqual(response["total"], 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertNotIn("FAKE_ID_1", result_ids)
        self.assertNotIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)

        # Combo excludes
        response = self.searcher.search(exclude_dictionary={"org": "MITX", "course": "ABC"})
        self.assertEqual(response["total"], 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertNotIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertNotIn("FAKE_ID_3", result_ids)

        # ignore with include
        response = self.searcher.search(field_dictionary={"course": "ABC"}, exclude_dictionary={"org": "MITX"})
        self.assertEqual(response["total"], 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertNotIn("FAKE_ID_2", result_ids)
        self.assertNotIn("FAKE_ID_3", result_ids)

    def test_exclude_filter_multiple(self):
        """ Test that multiple entries present in the exclude filter are filtered out """
        self.searcher.index([
            {"course": "ABC", "org": "edX", "id": "FAKE_ID_1"},
            {"course": "XYZ", "org": "edX", "id": "FAKE_ID_2"},
            {"course": "DEF", "org": "MITX", "id": "FAKE_ID_3"},
            {"course": "GHI", "org": "HarvardX", "id": "FAKE_ID_4"},
            {"course": "LMN", "org": "edX", "id": "FAKE_ID_5"},
        ])

        response = self.searcher.search()
        self.assertEqual(response["total"], 5)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertIn("FAKE_ID_4", result_ids)
        self.assertIn("FAKE_ID_5", result_ids)

        response = self.searcher.search(exclude_dictionary={"org": ["MITX", "HarvardX"]})
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertNotIn("FAKE_ID_3", result_ids)
        self.assertNotIn("FAKE_ID_4", result_ids)
        self.assertIn("FAKE_ID_5", result_ids)

        response = self.searcher.search(exclude_dictionary={"org": "edX"})
        self.assertEqual(response["total"], 2)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertNotIn("FAKE_ID_1", result_ids)
        self.assertNotIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertIn("FAKE_ID_4", result_ids)
        self.assertNotIn("FAKE_ID_5", result_ids)

        # Combo excludes
        response = self.searcher.search(
            exclude_dictionary={"org": ["MITX", "HarvardX"], "course": ["XYZ", "LMN", "DEF"]}
        )
        self.assertEqual(response["total"], 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertNotIn("FAKE_ID_2", result_ids)
        self.assertNotIn("FAKE_ID_3", result_ids)
        self.assertNotIn("FAKE_ID_4", result_ids)
        self.assertNotIn("FAKE_ID_5", result_ids)

        # ignore with include
        response = self.searcher.search(
            field_dictionary={"course": ["XYZ", "LMN", "DEF"]},
            exclude_dictionary={"org": ["MITX", "HarvardX"]}
        )
        self.assertEqual(response["total"], 2)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertNotIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertNotIn("FAKE_ID_3", result_ids)
        self.assertNotIn("FAKE_ID_4", result_ids)
        self.assertIn("FAKE_ID_5", result_ids)

    def test_exclude_filter_empty(self):
        """ Test that search works when exclude filter is an empty list """
        self.searcher.index([
            {"course": "ABC", "org": "edX", "id": "FAKE_ID_1"},
            {"course": "XYZ", "org": "edX", "id": "FAKE_ID_2"},
            {"course": "DEF", "org": "MITX", "id": "FAKE_ID_3"},
            {"course": "GHI", "org": "HarvardX", "id": "FAKE_ID_4"},
            {"course": "LMN", "org": "edX", "id": "FAKE_ID_5"},
        ])

        response = self.searcher.search(exclude_dictionary={"org": []})
        self.assertEqual(response["total"], 5)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertIn("FAKE_ID_4", result_ids)
        self.assertIn("FAKE_ID_5", result_ids)

        response = self.searcher.search(
            field_dictionary={"course": ["XYZ", "LMN", "DEF"]},
            exclude_dictionary={"org": []}
        )
        self.assertEqual(response["total"], 3)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertNotIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertNotIn("FAKE_ID_4", result_ids)
        self.assertIn("FAKE_ID_5", result_ids)

    def _index_for_aggs(self):
        """ Prepare index for aggregation tests """
        self.searcher.index([
            {"id": "FAKE_ID_1", "subject": "mathematics", "org": "edX"},
            {"id": "FAKE_ID_2", "subject": "mathematics", "org": "MIT"},
            {"id": "FAKE_ID_3", "subject": "physics", "org": "MIT"},
            {"id": "FAKE_ID_4", "subject": "history", "org": "Harvard"},
            {"id": "FAKE_ID_5", "subject": "mathematics", "org": "Harvard"},
            {"id": "FAKE_ID_6", "subject": "physics", "org": "Harvard"},
            {"id": "FAKE_ID_7", "no_subject": "not_a_subject", "org": "Harvard"},
        ])

    def test_aggregation_search(self):
        """ Test that aggregation works well """
        self._index_for_aggs()

        response = self.searcher.search()
        self.assertEqual(response["total"], 7)
        self.assertNotIn("aggs", response)

        aggregation_terms = {
            "subject": {},
            "org": {}
        }
        response = self.searcher.search(aggregation_terms=aggregation_terms)
        self.assertEqual(response["total"], 7)
        self.assertIn("aggs", response)
        aggregation_results = response["aggs"]

        self.assertEqual(aggregation_results["subject"]["total"], 6)
        subject_term_counts = aggregation_results["subject"]["terms"]
        self.assertEqual(subject_term_counts["mathematics"], 3)
        self.assertEqual(subject_term_counts["history"], 1)
        self.assertEqual(subject_term_counts["physics"], 2)

        self.assertEqual(aggregation_results["org"]["total"], 7)
        org_term_counts = aggregation_results["org"]["terms"]
        self.assertEqual(org_term_counts["edX"], 1)
        self.assertEqual(org_term_counts["MIT"], 2)
        self.assertEqual(org_term_counts["Harvard"], 4)

    def test_filtered_aggregation_search(self):
        """ Test that aggregation works well alongside filtered results """
        self._index_for_aggs()

        aggregation_terms = {
            "subject": {},
            "org": {}
        }
        response = self.searcher.search(
            field_dictionary={"org": "Harvard"},
            aggregation_terms=aggregation_terms
        )
        self.assertEqual(response["total"], 4)
        self.assertIn("aggs", response)
        aggregation_results = response["aggs"]

        self.assertEqual(aggregation_results["subject"]["total"], 3)
        subject_term_counts = aggregation_results["subject"]["terms"]
        self.assertEqual(subject_term_counts["mathematics"], 1)
        self.assertEqual(subject_term_counts["history"], 1)
        self.assertEqual(subject_term_counts["physics"], 1)

        self.assertEqual(aggregation_results["org"]["total"], 4)
        org_term_counts = aggregation_results["org"]["terms"]
        self.assertNotIn("edX", org_term_counts)
        self.assertNotIn("MIT", org_term_counts)
        self.assertEqual(org_term_counts["Harvard"], 4)

        response = self.searcher.search(
            field_dictionary={"subject": ["physics", "history"]},
            aggregation_terms=aggregation_terms
        )
        self.assertEqual(response["total"], 3)
        self.assertIn("aggs", response)
        aggregation_results = response["aggs"]

        self.assertEqual(aggregation_results["subject"]["total"], 3)
        subject_term_counts = aggregation_results["subject"]["terms"]
        self.assertNotIn("mathematics", subject_term_counts)
        self.assertEqual(subject_term_counts["history"], 1)
        self.assertEqual(subject_term_counts["physics"], 2)

        self.assertEqual(aggregation_results["org"]["total"], 3)
        org_term_counts = aggregation_results["org"]["terms"]
        self.assertNotIn("edX", org_term_counts)
        self.assertEqual(org_term_counts["MIT"], 1)
        self.assertEqual(org_term_counts["Harvard"], 2)

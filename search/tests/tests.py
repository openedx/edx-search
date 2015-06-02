#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
# pylint: disable=too-few-public-methods
# pylint: disable=too-many-ancestors
""" Tests for search functionalty """
from datetime import datetime
import json
import os

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings
from elasticsearch import Elasticsearch, exceptions

from search.search_engine_base import SearchEngine
from search.elastic import ElasticSearchEngine, RESERVED_CHARACTERS
from search.tests.utils import ErroringElasticImpl, SearcherMixin, TEST_INDEX_NAME
from search.utils import ValueRange, DateRange
from search.api import perform_search, NoSearchEngineError

from .mock_search_engine import MockSearchEngine, json_date_to_datetime


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

    def test_factory_creator(self):
        """ Make sure that search object implements SearchEngine interface """
        self.assertTrue(isinstance(self.searcher, SearchEngine))

    def test_abstract_impl(self):
        """ Make sure that if one tries to use the abstract base, then operations yeild NotImplementedError s """
        abstract = SearchEngine("test_index_name")
        test_string = "A test string"
        with self.assertRaises(NotImplementedError):
            abstract.index("test_doc", {"name": test_string})
        with self.assertRaises(NotImplementedError):
            abstract.search(test_string)
        with self.assertRaises(NotImplementedError):
            abstract.remove("test_doc", "test_id")

    def test_find_all(self):
        """ Make sure that null search finds everything in the index """
        test_string = "A test string"
        self.searcher.index("test_doc", {"name": test_string})

        # search everything
        response = self.searcher.search(None)
        self.assertEqual(response["total"], 1)
        results = response["results"]
        self.assertEqual(results[0]["data"]["name"], test_string)

        self.searcher.index("not_test_doc", {"value": test_string})

        response = self.searcher.search(None)
        self.assertEqual(response["total"], 2)
        results = response["results"]
        test_0 = results[0]["data"] if "name" in results[0]["data"] else results[1]["data"]
        test_1 = results[1]["data"] if "name" in results[0]["data"] else results[0]["data"]
        self.assertEqual(test_0["name"], test_string)
        self.assertEqual(test_1["value"], test_string)

    def test_find_doctype(self):
        """ Make sure that searches for specific doc_type only return requested doc_type """
        test_string = "A test string"
        self.searcher.index("test_doc", {"name": test_string})

        # search by doc_type
        response = self.searcher.search(None, doc_type="test_doc")
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(None, doc_type="not_test_doc")
        self.assertEqual(response["total"], 0)

        self.searcher.index("not_test_doc", {"value": test_string})

        response = self.searcher.search(None, doc_type="not_test_doc")
        self.assertEqual(response["total"], 1)

    def test_find_string(self):
        """ Find a string within the object "content" node """
        test_string = "A test string"
        self.searcher.index("test_doc", {"content": {"name": test_string}})

        # search string
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.index("not_test_doc", {"content": {"value": test_string}})

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 2)

        response = self.searcher.search_string(test_string, doc_type="test_doc")
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string("something else")
        self.assertEqual(response["total"], 0)

        self.searcher.index("test_doc", {"content": {"deep": {"down": test_string}}})
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
        self.searcher.index("test_doc", test_object)

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
        self.searcher.index("test_doc", test_object)

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
        self.searcher.index("test_doc", test_object)

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

        response = self._searcher.search(
            field_dictionary={"tags.shape": "square", "tags.color": "blue"}, use_field_match=True)
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
        self.searcher.index("test_doc", test_object1)
        self.searcher.index("test_doc", test_object2)

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
        self.searcher.index("test_doc", test_object1)
        self.searcher.index("test_doc", test_object2)

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
        self.searcher.index("test_doc", {"content": {"name": test_string}})

        # search string
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.index("not_test_doc", {"content": {"value": test_string}})

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 2)

    def test_delete_item(self):
        """ make sure that we can remove an item from the index """
        test_string = "This is a test of the emergency broadcast system"
        self.searcher.index("test_doc", {"id": "FAKE_ID", "content": {"name": test_string}})

        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.remove("test_doc", response["results"][0]["data"]["id"])
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 0)

    def test_delete_item_slashes(self):
        """ make sure that we can remove an item from the index with complex id """
        test_string = "This is a test of the emergency broadcast system"
        self.searcher.index(
            "test_doc", {
                "id": "i4x://edX/DemoX/google-document/e3369ea4aa0749a7ba29c461d1c819a4",
                "content": {"name": test_string}
            }
        )

        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.remove("test_doc", "i4x://edX/DemoX/google-document/e3369ea4aa0749a7ba29c461d1c819a4")
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 0)

    def test_delete_item_not_present(self):
        """ make sure that we get no error removing an item that does not exist """
        test_string = "This is a test of the emergency broadcast system"
        self.searcher.index("test_doc", {"id": "FAKE_ID", "content": {"name": "abc"}})
        self.searcher.remove("test_doc", "FAKE_ID")

        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 0)

        self.searcher.remove("test_doc", "FAKE_ID")
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 0)

    def test_filter_items(self):
        """ Make sure that filters work """
        self.searcher.index("test_doc", {"id": "FAKE_ID_1", "test_value": "1", "filter_field": "my_filter_value"})
        self.searcher.index("test_doc", {"id": "FAKE_ID_2", "test_value": "2"})

        response = self.searcher.search(filter_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 2)

        response = self.searcher.search(field_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 1)

        self.searcher.index("test_doc", {"id": "FAKE_ID_3", "test_value": "3", "filter_field": "not_my_filter_value"})
        response = self.searcher.search(filter_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 2)

        response = self.searcher.search()
        self.assertEqual(response["total"], 3)

    def test_filter_where_null(self):
        """
        Make sure that filtering with `None` value finds only fields where item
        is not present or where explicitly None
        """
        self.searcher.index("test_doc", {"id": "FAKE_ID_1", "test_value": "1", "filter_field": "my_filter_value"})
        self.searcher.index("test_doc", {"id": "FAKE_ID_2", "test_value": "2"})
        self.searcher.index("test_doc", {"id": "FAKE_ID_3", "test_value": "3", "filter_field": "not_my_filter_value"})

        response = self.searcher.search(filter_dictionary={"filter_field": "my_filter_value"})
        self.assertEqual(response["total"], 2)

        response = self.searcher.search(filter_dictionary={"filter_field": None})
        self.assertEqual(response["total"], 1)

        self.searcher.index("test_doc", {"id": "FAKE_ID_4", "test_value": "4", "filter_field": None})
        response = self.searcher.search(filter_dictionary={"filter_field": None})
        self.assertEqual(response["total"], 2)

    def test_date_range(self):
        """ Make sure that date ranges can be searched """
        self.searcher.index("test_doc", {"id": "FAKE_ID_1", "test_value": "1", "start_date": datetime(2010, 1, 1)})
        self.searcher.index("test_doc", {"id": "FAKE_ID_2", "test_value": "2", "start_date": datetime(2100, 1, 1)})

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
        self.searcher.index("test_doc", {"id": "FAKE_ID_1", "test_value": "1", "age": 20})
        self.searcher.index("test_doc", {"id": "FAKE_ID_2", "test_value": "2", "age": 30})
        self.searcher.index("test_doc", {"id": "FAKE_ID_3", "test_value": "3", "age": 40})

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

        self.searcher.index("test_doc", {"id": "FAKE_ID_4", "test_value": "4", "age": 50})

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

        self.searcher.index("test_doc", {"id": "FAKE_ID_5", "test_value": "5", "not_age": 50})
        test_age_range_filter(19, 29, 2)
        test_age_range_filter(19, 39, 3)
        test_age_range_filter(19, 49, 4)
        test_age_range_filter(29, 49, 3)
        test_age_range_filter(39, 49, 2)
        test_age_range_filter(None, 29, 2)
        test_age_range_filter(39, None, 3)

    def test_range_filter(self):
        """ Make sure that ranges can be used in field_dictionary and filter_dictionary """
        self.searcher.index("test_doc", {"id": "FAKE_ID_1", "test_value": "1", "age": 20})
        self.searcher.index("test_doc", {"id": "FAKE_ID_2", "test_value": "2", "age": 30})
        self.searcher.index("test_doc", {"id": "FAKE_ID_3", "test_value": "3", "not_age": 40})

        response = self.searcher.search()
        self.assertEqual(response["total"], 3)

        response = self.searcher.search(field_dictionary={"age": ValueRange(19, 29)})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(filter_dictionary={"age": ValueRange(19, 29)})
        self.assertEqual(response["total"], 2)

    def test_pagination(self):
        """ Test paging operation """
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC",
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling Little Darling Little Darling, it's been a long long lonely winter"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC",
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling Little Darling, it's been a year since you've been gone"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "XYZ",
                "id": "FAKE_ID_3",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )

        response = self.searcher.search(query_string="Little Darling")
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 3)

        response = self.searcher.search(query_string="Little Darling", size=1)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids)

        response = self.searcher.search(query_string="Little Darling", size=1, from_=0)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids)

        response = self.searcher.search(query_string="Little Darling", size=1, from_=1)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertTrue("FAKE_ID_2" in result_ids)

        response = self.searcher.search(query_string="Little Darling", size=1, from_=2)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids)

        response = self.searcher.search(query_string="Little Darling", size=2)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 2)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        response = self.searcher.search(query_string="Little Darling", size=2, from_=0)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 2)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        response = self.searcher.search(query_string="Little Darling", size=2, from_=2)
        self.assertEqual(response["total"], 3)
        self.assertEqual(len(response["results"]), 1)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids)

    def test_exclude_ids(self):
        """ Test that ids that would normally be present in the resultset will not be present if in the exclude list """
        self.searcher.index("test_doc", {"course": "ABC", "id": "FAKE_ID_1"})
        self.searcher.index("test_doc", {"course": "ABC", "id": "FAKE_ID_2"})
        self.searcher.index("test_doc", {"course": "ABC", "id": "FAKE_ID_3"})
        self.searcher.index("test_doc", {"course": "XYZ", "id": "FAKE_ID_11"})
        self.searcher.index("test_doc", {"course": "XYZ", "id": "FAKE_ID_12"})
        self.searcher.index("test_doc", {"course": "XYZ", "id": "FAKE_ID_13"})

        response = self.searcher.search()
        self.assertEqual(response["total"], 6)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertIn("FAKE_ID_11", result_ids)
        self.assertIn("FAKE_ID_12", result_ids)
        self.assertIn("FAKE_ID_13", result_ids)

        response = self.searcher.search(exclude_ids=["FAKE_ID_1", "FAKE_ID_2", "FAKE_ID_11", "FAKE_ID_12"])
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
        self.searcher.index("test_doc", {"course": "ABC", "org": "edX", "id": "FAKE_ID_1"})
        self.searcher.index("test_doc", {"course": "XYZ", "org": "edX", "id": "FAKE_ID_2"})
        self.searcher.index("test_doc", {"course": "LMN", "org": "MITX", "id": "FAKE_ID_3"})

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
        self.searcher.index("test_doc", {"course": "ABC", "org": "edX", "id": "FAKE_ID_1"})
        self.searcher.index("test_doc", {"course": "XYZ", "org": "edX", "id": "FAKE_ID_2"})
        self.searcher.index("test_doc", {"course": "DEF", "org": "MITX", "id": "FAKE_ID_3"})
        self.searcher.index("test_doc", {"course": "GHI", "org": "HarvardX", "id": "FAKE_ID_4"})
        self.searcher.index("test_doc", {"course": "LMN", "org": "edX", "id": "FAKE_ID_5"})

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
        self.searcher.index("test_doc", {"course": "ABC", "org": "edX", "id": "FAKE_ID_1"})
        self.searcher.index("test_doc", {"course": "XYZ", "org": "edX", "id": "FAKE_ID_2"})
        self.searcher.index("test_doc", {"course": "DEF", "org": "MITX", "id": "FAKE_ID_3"})
        self.searcher.index("test_doc", {"course": "GHI", "org": "HarvardX", "id": "FAKE_ID_4"})
        self.searcher.index("test_doc", {"course": "LMN", "org": "edX", "id": "FAKE_ID_5"})

        response = self.searcher.search(exclude_dictionary={"org": []})
        self.assertEqual(response["total"], 5)
        result_ids = [r["data"]["id"] for r in response["results"]]
        self.assertIn("FAKE_ID_1", result_ids)
        self.assertIn("FAKE_ID_2", result_ids)
        self.assertIn("FAKE_ID_3", result_ids)
        self.assertIn("FAKE_ID_4", result_ids)
        self.assertIn("FAKE_ID_5", result_ids)


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
class ElasticSearchTests(MockSearchTests):
    """ Override that runs the same tests for ElasticSearchEngine instead of MockSearchEngine """

    def test_reserved_characters(self):
        """ Make sure that we handle when reserved characters were passed into query_string """
        test_string = "What the ! is this?"
        self.searcher.index("test_doc", {"content": {"name": test_string}})

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string("something else !")
        self.assertEqual(response["total"], 0)

        response = self.searcher.search_string("something ! else")
        self.assertEqual(response["total"], 0)

        for char in RESERVED_CHARACTERS:
            # previously these would throw exceptions
            response = self.searcher.search_string(char)
            self.assertEqual(response["total"], 0)


@override_settings(MOCK_SEARCH_BACKING_FILE="./testfile.pkl")
class FileBackedMockSearchTests(MockSearchTests):
    """ Override that runs the same tests with file-backed MockSearchEngine """

    def setUp(self):
        MockSearchEngine.create_test_file()
        self._searcher = None

    def tearDown(self):
        MockSearchEngine.destroy_test_file()
        self._searcher = None

    # Commenting test for now - it runs locally, but not on travis system
    # def test_file_reopen(self):
    #     """ make sure that the file contents can be reopened and the data therein is reflected as expected """
    #     test_object = {
    #         "content": {
    #             "name": "John Lester",
    #         },
    #         "course_id": "A/B/C",
    #         "abc": "xyz"
    #     }
    #     self.searcher.index("test_doc", test_object)

    # fake it out to destory a different file, leaving this one in place
    # will force reload from the original file
    #     settings.MOCK_SEARCH_BACKING_FILE = "./fakeout_destroy.pkl"
    #     MockSearchEngine.destroy()

    # now search should fail
    #     response = self.searcher.search(query_string="John Lester")
    #     self.assertEqual(response["total"], 0)

    # go back to existing file for the reload
    #     settings.MOCK_SEARCH_BACKING_FILE = "./testfile.pkl"

    # now search should be successful
    #     response = self.searcher.search(query_string="John Lester")
    #     self.assertEqual(response["total"], 1)

    def test_file_value_formats(self):
        """ test the format of values that write/read from the file """
        # json serialization removes microseconds part of the datetime object, so
        # we strip it at the beginning to allow equality comparison to be correct
        this_moment = datetime.utcnow().replace(microsecond=0)
        test_object = {
            "content": {
                "name": "How did 11 of 12 balls get deflated during the game"
            },
            "my_date_value": this_moment,
            "my_integer_value": 172,
            "my_float_value": 57.654,
            "my_string_value": "If the officials just blew it, would they come out and admit it?"
        }

        self.searcher.index("test_doc", test_object)

        # now search should be successful
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 1)

        # and values should be what we desire
        returned_result = response["results"][0]["data"]
        self.assertEqual(json_date_to_datetime(returned_result["my_date_value"]), this_moment)
        self.assertEqual(returned_result["my_integer_value"], 172)
        self.assertEqual(returned_result["my_float_value"], 57.654)
        self.assertEqual(
            returned_result["my_string_value"],
            "If the officials just blew it, would they come out and admit it?"
        )

    def test_disabled_index(self):
        """
        Make sure that searchengine operations are shut down when mock engine has a filename, but file does
        not exist - this is helpful for test scenarios where we essentially want to not slow anything down
        """
        this_moment = datetime.utcnow()
        test_object = {
            "id": "FAKE_ID",
            "content": {
                "name": "How did 11 of 12 balls get deflated during the game"
            },
            "my_date_value": this_moment,
            "my_integer_value": 172,
            "my_float_value": 57.654,
            "my_string_value": "If the officials just blew it, would they come out and admit it?"
        }

        self.searcher.index("test_doc", test_object)
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 1)

        # copy content, and then erase file so that backed file is not present and work is disabled
        initial_file_content = None
        with open("testfile.pkl", "r") as dict_file:
            initial_file_content = json.load(dict_file)
        os.remove("testfile.pkl")

        response = self.searcher.search(query_string="ABC")
        self.assertEqual(response["total"], 0)

        self.searcher.index("test_doc", {"content": {"name": "ABC"}})
        # now search should be unsuccessful because file does not exist
        response = self.searcher.search(query_string="ABC")
        self.assertEqual(response["total"], 0)

        # remove it, and then we'll reload file and it still should be there
        self.searcher.remove("test_doc", "FAKE_ID")

        MockSearchEngine.create_test_file("fakefile.pkl", initial_file_content)

        # now search should be successful because file did exist in file
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 1)

        self.searcher.remove("not_a_test_doc", "FAKE_ID")
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 1)

        self.searcher.remove("test_doc", "FAKE_ID")
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 0)


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
@override_settings(ELASTIC_SEARCH_IMPL=ErroringElasticImpl)
class ErroringElasticTests(TestCase, SearcherMixin):
    """ testing handling of elastic exceptions when they happen """

    def test_index_failure(self):
        """ the index operation should fail """
        with self.assertRaises(exceptions.ElasticsearchException):
            self.searcher.index("test_doc", {"name": "abc test"})

    def test_search_failure(self):
        """ the search operation should fail """
        with self.assertRaises(exceptions.ElasticsearchException):
            self.searcher.search("abc test")

    def test_remove_failure(self):
        """ the remove operation should fail """
        with self.assertRaises(exceptions.ElasticsearchException):
            self.searcher.remove("test_doc", "test_id")


@override_settings(SEARCH_ENGINE=None)
class TestNone(TestCase):
    """ Tests correct skipping of operation when no search engine is defined """

    def test_perform_search(self):
        """ search opertaion should yeild an exception with no search engine """
        with self.assertRaises(NoSearchEngineError):
            perform_search("abc test")

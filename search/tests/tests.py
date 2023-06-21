#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
# pylint: disable=abstract-class-not-used
# pylint: disable=too-few-public-methods
""" Tests for search functionalty """
from datetime import datetime
import json
import os

from django.core.urlresolvers import resolve, Resolver404
from django.test import TestCase, Client
from django.test.utils import override_settings
from elasticsearch import Elasticsearch, exceptions

from search.search_engine_base import SearchEngine
from search.elastic import ElasticSearchEngine, RESERVED_CHARACTERS
from search.result_processor import SearchResultProcessor
from search.utils import ValueRange, DateRange
from search.api import perform_search, NoSearchEngine

from .mock_search_engine import MockSearchEngine, _find_field, _filter_intersection, json_date_to_datetime
from mock import patch, call

TEST_INDEX_NAME = "test_index"

# Any class that inherits from TestCase will cause too-many-public-methods pylint error
# pylint: disable=too-many-public-methods

# We override ElasticSearchEngine class in order to force an index refresh upon index
# otherwise we often get results from the prior state, rendering the tests less useful


class ForceRefreshElasticSearchEngine(ElasticSearchEngine):
    """
    Override of ElasticSearchEngine that forces the update of the index,
    so that tests can relaibly search right afterward
    """

    def index(self, doc_type, body, **kwargs):
        kwargs.update({
            "refresh": True
        })
        super(ForceRefreshElasticSearchEngine, self).index(doc_type, body, **kwargs)

    def remove(self, doc_type, doc_id, **kwargs):
        kwargs.update({
            "refresh": True
        })
        super(ForceRefreshElasticSearchEngine, self).remove(doc_type, doc_id, **kwargs)


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
@override_settings(MOCK_SEARCH_BACKING_FILE=None)
class MockSearchTests(TestCase):
    """ Test operation of search activities """
    _searcher = None

    @property
    def searcher(self):
        """ cached instance of search engine """
        if self._searcher is None:
            self._searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        return self._searcher

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
            "course_id": "A/B/C",
            "abc": "xyz",
        }
        self.searcher.index("test_doc", test_object)

        response = self.searcher.search(query_string="find me")
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"course_id": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(query_string="find me", field_dictionary={"course_id": "X/Y/Z"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(query_string="find me", field_dictionary={"course_id": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string("find me", field_dictionary={"course_id": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"course_id": "A/B/C"}, query_string="find me")
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


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
class MockSpecificSearchTests(TestCase):
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


@override_settings(SEARCH_ENGINE="search.tests.tests.ForceRefreshElasticSearchEngine")
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


class SearchResultProcessorTests(TestCase):
    """ Tests to check SearchResultProcessor is working as desired """

    def test_strings_in_dictionary(self):
        """ Test finding strings within dictionary item """
        test_dict = {
            "a": "This is a string that should show up"
        }

        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 1)
        self.assertEqual(get_strings[0], test_dict["a"])

        test_dict.update({
            "b": "This is another string that should show up"
        })
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 2)
        self.assertEqual(get_strings[0], test_dict["a"])
        self.assertEqual(get_strings[1], test_dict["b"])

        test_dict.update({
            "CASCADE": {
                "z": "This one should be found too"
            }
        })
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 3)
        self.assertEqual(get_strings[0], test_dict["a"])
        self.assertEqual(get_strings[1], test_dict["b"])
        self.assertEqual(get_strings[2], test_dict["CASCADE"]["z"])

        test_dict.update({
            "DEEP": {
                "DEEPER": {
                    "STILL_GOING": {
                        "MORE": {
                            "here": "And here, again and again"
                        }
                    }
                }
            }
        })
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 4)
        self.assertEqual(get_strings[0], test_dict["a"])
        self.assertEqual(get_strings[1], test_dict["b"])
        self.assertEqual(get_strings[2], test_dict["CASCADE"]["z"])
        self.assertEqual(get_strings[3], test_dict["DEEP"]["DEEPER"]["STILL_GOING"]["MORE"]["here"])

    def test_find_matches(self):
        """ test finding matches """
        words = ["hello"]
        strings = [
            "hello there",
            "goodbye",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 2)
        self.assertTrue(strings[0] in matches)
        self.assertFalse(strings[1] in matches)
        self.assertTrue(strings[2] in matches)

        words = ["hello", "there"]
        strings = [
            "hello there",
            "goodbye",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 2)
        self.assertTrue(strings[0] in matches)
        self.assertFalse(strings[1] in matches)
        self.assertTrue(strings[2] in matches)

        words = ["hello", "there"]
        strings = [
            "hello there",
            "goodbye there",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 3)
        self.assertTrue(strings[0] in matches)
        self.assertTrue(strings[1] in matches)
        self.assertTrue(strings[2] in matches)

        words = ["goodbye there", "goodbye", "there"]
        strings = [
            "goodbye",
            "goodbye there",
            "Sail away to say GOODBYE",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertTrue(strings[0] in matches)
        self.assertTrue(strings[1] in matches)
        self.assertTrue(strings[2] in matches)

        words = ["none of these are present"]
        strings = [
            "goodbye",
            "goodbye there",
            "Sail away to say GOODBYE",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 0)

    def test_too_long_find_matches(self):
        """ make sure that we keep the expert snippets short enough """
        words = ["edx", "afterward"]
        strings = [
            ("Here is a note about edx and it is very long - more than the desirable length of 100 characters"
             " - indeed this should show up"),
            "This matches too but comes afterward",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 1)

    def test_excerpt(self):
        """ test that we return an excerpt """
        test_result = {
            "content": {
                "notes": u"Here is a الاستحسان about edx",
                "name": "edX search a lot",
            }
        }
        srp = SearchResultProcessor(test_result, u"الاستحسان")
        self.assertEqual(srp.excerpt, u"Here is a <b>الاستحسان</b> about edx")

        srp = SearchResultProcessor(test_result, u"edx")
        self.assertEqual(srp.excerpt, u"Here is a الاستحسان about <b>edx</b>&hellip;<b>edX</b> search a lot")

    def test_too_long_excerpt(self):
        """ test that we shorten an excerpt that is too long appropriately """
        test_string = (
            u"Here is a note about الاستحسان and it is very long - more than the desirable length of 100"
            u" characters - indeed this should show up but it should trim the characters around in"
            u" order to show the selected text in bold"
        )
        test_result = {
            "content": {
                "notes": test_string,
            }
        }
        srp = SearchResultProcessor(test_result, u"الاستحسان")
        test_string_compare = SearchResultProcessor.decorate_matches(test_string, u"الاستحسان")
        excerpt = srp.excerpt
        self.assertNotEqual(excerpt, test_string_compare)
        self.assertTrue(u"note about <b>الاستحسان</b> and it is" in excerpt)

        test_string = (
            u"Here is a note about stuff and it is very long - more than the desirable length of 100"
            u" characters - indeed this should show up but it should trim the الاستحسان characters around in"
            u" order to show the selected text in bold"
        )
        test_result = {
            "content": {
                "notes": test_string,
            }
        }
        srp = SearchResultProcessor(test_result, u"الاستحسان")
        test_string_compare = SearchResultProcessor.decorate_matches(test_string, u"الاستحسان")
        excerpt = srp.excerpt
        self.assertNotEqual(excerpt, test_string_compare)
        self.assertTrue(u"should trim the <b>الاستحسان</b> characters around" in excerpt)

    def test_excerpt_front(self):
        """ test that we process correctly when match is at the front of the excerpt """
        test_result = {
            "content": {
                "notes": "Dog - match upon first word",
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt, "<b>Dog</b> - match upon first word")

        test_result = {
            "content": {
                "notes": (
                    "Dog - match upon first word "
                    "The long and winding road "
                    "That leads to your door "
                    "Will never disappear "
                    "I've seen that road before "
                    "It always leads me here "
                    "Lead me to you door "
                    "The wild and windy night "
                    "That the rain washed away "
                    "Has left a pool of tears "
                    "Crying for the day "
                    "Why leave me standing here "
                    "Let me know the way "
                    "Many times I've been alone "
                    "And many times I've cried "
                    "Any way you'll never know "
                    "The many ways I've tried "
                    "But still they lead me back "
                    "To the long winding road "
                    "You left me standing here "
                    "A long long time ago "
                    "Don't leave me waiting here "
                    "Lead me to your door "
                    "But still they lead me back "
                    "To the long winding road "
                    "You left me standing here "
                    "A long long time ago "
                    "Don't leave me waiting here "
                    "Lead me to your door "
                    "Yeah, yeah, yeah, yeah "
                ),
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt[0:34], "<b>Dog</b> - match upon first word")

    def test_excerpt_back(self):
        """ test that we process correctly when match is at the end of the excerpt """
        test_result = {
            "content": {
                "notes": "Match upon last word - Dog",
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt, "Match upon last word - <b>Dog</b>")

        test_result = {
            "content": {
                "notes": (
                    "The long and winding road "
                    "That leads to your door "
                    "Will never disappear "
                    "I've seen that road before "
                    "It always leads me here "
                    "Lead me to you door "
                    "The wild and windy night "
                    "That the rain washed away "
                    "Has left a pool of tears "
                    "Crying for the day "
                    "Why leave me standing here "
                    "Let me know the way "
                    "Many times I've been alone "
                    "And many times I've cried "
                    "Any way you'll never know "
                    "The many ways I've tried "
                    "But still they lead me back "
                    "To the long winding road "
                    "You left me standing here "
                    "A long long time ago "
                    "Don't leave me waiting here "
                    "Lead me to your door "
                    "But still they lead me back "
                    "To the long winding road "
                    "You left me standing here "
                    "A long long time ago "
                    "Don't leave me waiting here "
                    "Lead me to your door "
                    "Yeah, yeah, yeah, yeah "
                    "Match upon last word - Dog"
                ),
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt[-33:], "Match upon last word - <b>Dog</b>")


class TestSearchResultProcessor(SearchResultProcessor):
    """
    Override the SearchResultProcessor so that we get the additional (inferred) properties
    and can identify results that should be removed due to access restriction
    """
    # pylint: disable=no-self-use
    @property
    def additional_property(self):
        """ additional property that should appear within processed results """
        return "Should have an extra value"

    @property
    def url(self):
        """
        Property to display the url for the given location, useful for allowing navigation
        """
        if "course" not in self._results_fields or "id" not in self._results_fields:
            raise ValueError("expect this error when not providing a course and/or id")

        return u"/courses/{course_id}/jump_to/{location}".format(
            course_id=self._results_fields["course"],
            location=self._results_fields["id"],
        )

    def should_remove(self, user):
        """ remove items when url is None """
        return "remove_me" in self._results_fields


@override_settings(SEARCH_RESULT_PROCESSOR="search.tests.tests.TestSearchResultProcessor")
class TestOverrideSearchResultProcessor(TestCase):
    """ test the correct processing of results using the SEARCH_RESULT_PROCESSOR specified class """

    def test_additional_property(self):
        """ make sure the addition properties are returned """
        test_result = {
            "course": "testmetestme",
            "id": "herestheid"
        }
        new_result = SearchResultProcessor.process_result(test_result, "fake search pattern", None)
        self.assertEqual(new_result, test_result)
        self.assertEqual(test_result["url"], "/courses/testmetestme/jump_to/herestheid")
        self.assertIsNone(test_result["excerpt"])
        self.assertEqual(test_result["additional_property"], "Should have an extra value")

    def test_removal(self):
        """ make sure that the override of should remove let's the application prevent access to a result """
        test_result = {
            "course": "remove_course",
            "id": "remove_id",
            "remove_me": True
        }
        new_result = SearchResultProcessor.process_result(test_result, "fake search pattern", None)
        self.assertIsNone(new_result)

    def test_property_error(self):
        """ result should be removed from list if there is an error in the handler properties """
        test_result = {
            "not_course": "asdasda",
            "not_id": "rthrthretht"
        }
        new_result = SearchResultProcessor.process_result(test_result, "fake search pattern", None)
        self.assertIsNone(new_result)


def _post_request(body, course_id=None):
    """ Helper method to post the request and process the response """
    address = '/' if course_id is None else '/{}'.format(course_id)
    response = Client().post(address, body)

    return getattr(response, "status_code", 500), json.loads(getattr(response, "content", None))


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
@override_settings(COURSEWARE_INDEX_NAME=TEST_INDEX_NAME)
class MockSearchUrlTest(TestCase):
    """
    Make sure that requests to the url get routed to the correct view handler
    """
    _searcher = None

    def _reset_mocked_tracker(self):
        """ reset mocked tracker and clear logged emits """
        self.mock_tracker.reset_mock()

    def setUp(self):
        MockSearchEngine.destroy()
        self._searcher = None
        patcher = patch('search.views.track')
        self.mock_tracker = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        MockSearchEngine.destroy()
        self._searcher = None

    @property
    def searcher(self):
        """ return instance of searcher """
        if self._searcher is None:
            self._searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        return self._searcher

    def assert_no_events_were_emitted(self):
        """Ensures no events were emitted since the last event related assertion"""
        self.assertFalse(self.mock_tracker.emit.called)  # pylint: disable=maybe-no-member

    def assert_search_initiated_event_was_emitted(self, search_term, size, page):
        """Ensures an search initiated event was emitted"""
        initiated_search_call = self.mock_tracker.emit.mock_calls[0]
        expected_result = call('edx.course.search.initiated', {
            "search_term": unicode(search_term),
            "page_size": size,
            "page_number": page,
        })
        self.assertEqual(expected_result, initiated_search_call)

    def assert_results_returned_event_was_emitted(self, search_term, size, page, total):
        """Ensures an results returned event was emitted"""
        returned_results_call = self.mock_tracker.emit.mock_calls[1]
        expected_result = call('edx.course.search.results_displayed', {
            "search_term": unicode(search_term),
            "page_size": size,
            "page_number": page,
            "results_count": total,
        })
        self.assertEqual(expected_result, returned_results_call)

    def assert_search_initiated_returned_events_were_emitted(self, search_term, size, page, total):
        """Asserts search initiated and results returned events were emitted"""
        self.assertEqual(self.mock_tracker.emit.call_count, 2)
        self.assert_search_initiated_event_was_emitted(search_term, size, page)
        self.assert_results_returned_event_was_emitted(search_term, size, page, total)

    def test_url_resolution(self):
        """ make sure that the url is resolved as expected """
        resolver = resolve('/')
        self.assertEqual(resolver.view_name, 'do_search')

        with self.assertRaises(Resolver404):
            resolver = resolve('/blah')

        resolver = resolve('/edX/DemoX/Demo_Course')
        self.assertEqual(resolver.view_name, 'do_search')
        self.assertEqual(resolver.kwargs['course_id'], 'edX/DemoX/Demo_Course')

    def test_search_from_url(self):
        """ test searching using the url """
        self.searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                },
                "test_date": datetime(2015, 1, 1),
                "test_string": "ABC, It's easy as 123"
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling, it's been a year since sun been gone"
                }
            }
        )
        self.searcher.index("test_doc", {"id": "FAKE_ID_3", "content": {"text": "Here comes the sun"}})

        # Test no events called  yet after setup
        self.assert_no_events_were_emitted()
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "sun"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("sun", 20, 0, 2)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Darling"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Darling", 20, 0, 2)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "winter"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" not in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("winter", 20, 0, 1)
        self._reset_mocked_tracker()

        self.assertTrue(results["results"][0]["data"]["test_date"], datetime(2015, 1, 1).isoformat())
        self.assertTrue(results["results"][0]["data"]["test_string"], "ABC, It's easy as 123")

    def test_course_search_url(self):
        """ test searching using the course url """
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC/DEF/GHI",
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC/DEF/GHI",
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling, it's been a year since you've been gone"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "LMN/OPQ/RST",
                "id": "FAKE_ID_3",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )

        # Test no events called  yet after setup
        self.assert_no_events_were_emitted()
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 20, 0, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Darling"}, "ABC/DEF/GHI")
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Darling", 20, 0, 2)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "winter"}, "ABC/DEF/GHI")
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" not in result_ids and "FAKE_ID_3" not in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("winter", 20, 0, 1)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "winter"}, "LMN/OPQ/RST")
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" not in result_ids and "FAKE_ID_2" not in result_ids and "FAKE_ID_3" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("winter", 20, 0, 1)
        self._reset_mocked_tracker()

    def test_empty_search_string(self):
        """ test when search string is provided as empty or null (None) """
        code, results = _post_request({"search_string": ""})
        self.assertTrue(code > 499)
        self.assertEqual(results["error"], "No search term provided for search")

        code, results = _post_request({"no_search_string_provided": ""})
        self.assertTrue(code > 499)
        self.assertEqual(results["error"], "No search term provided for search")

    def test_pagination(self):
        """ test searching using the course url """
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

        # Test no events called  yet after setup
        self.assert_no_events_were_emitted()
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 3)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 20, 0, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling", "page_size": 1})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 1, 0, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling", "page_size": 1, "page_index": 0})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 1, 0, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling", "page_size": 1, "page_index": 1})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 1, 1, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling", "page_size": 1, "page_index": 2})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 1, 2, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling", "page_size": 2})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 2, 0, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling", "page_size": 2, "page_index": 0})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 2, 0, 3)
        self._reset_mocked_tracker()

        code, results = _post_request({"search_string": "Little Darling", "page_size": 2, "page_index": 1})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_search_initiated_returned_events_were_emitted("Little Darling", 2, 1, 3)
        self._reset_mocked_tracker()

    def test_page_size_too_large(self):
        """ test searching with too-large page_size """
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC/DEF/GHI",
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )

        code, results = _post_request({"search_string": "Little Darling", "page_size": 101})
        self.assertEqual(code, 500)
        self.assertTrue("error" in results)


class ErroringSearchEngine(MockSearchEngine):
    """ Override to generate search engine error to test """

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None, **kwargs):
        raise StandardError("There is a problem here")


class ErroringElasticImpl(Elasticsearch):
    """ Elasticsearch implementation that throws exceptions"""

    # pylint: disable=unused-argument
    def index(self, **kwargs):
        """ this operation will fail """
        raise exceptions.ElasticsearchException("This index operation failed")

    # pylint: disable=unused-argument
    def delete(self, **kwargs):
        """ this operation will definitely fail """
        raise exceptions.ElasticsearchException("This delete operation failed")

    # pylint: disable=unused-argument
    def search(self, **kwargs):
        """ this will definitely fail """
        raise exceptions.ElasticsearchException("This search operation failed")


@override_settings(SEARCH_ENGINE="search.tests.tests.ForceRefreshElasticSearchEngine")
@override_settings(ELASTIC_SEARCH_IMPL=ErroringElasticImpl)
class ErroringElasticTests(TestCase):
    """ testing handling of elastic exceptions when they happen """
    _searcher = None

    @property
    def searcher(self):
        """ cached instance of search engine """
        if self._searcher is None:
            self._searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        return self._searcher

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
        with self.assertRaises(NoSearchEngine):
            perform_search("abc test")


@override_settings(SEARCH_ENGINE="search.tests.tests.ErroringSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
@override_settings(COURSEWARE_INDEX_NAME=TEST_INDEX_NAME)
class BadSearchTest(TestCase):
    """ Make sure that we can error message when there is a problem """
    _searcher = None

    def setUp(self):
        MockSearchEngine.destroy()

    def tearDown(self):
        MockSearchEngine.destroy()

    def test_search_from_url(self):
        """ ensure that we get the error back when the backend fails """
        searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )
        searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling, it's been a year since sun been gone"
                }
            }
        )
        searcher.index("test_doc", {"id": "FAKE_ID_3", "content": {"text": "Here comes the sun"}})

        code, results = _post_request({"search_string": "sun"})
        self.assertTrue(code > 499)
        self.assertEqual(results["error"], 'An error occurred when searching for "sun"')

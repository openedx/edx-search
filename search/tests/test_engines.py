#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
# pylint: disable=too-few-public-methods
""" Tests for search functionality """

import json
import os
from datetime import datetime

from django.test import TestCase
from django.test.utils import override_settings
from elasticsearch import exceptions
from elasticsearch.helpers import BulkIndexError
from mock import patch

from search.api import perform_search, NoSearchEngineError
from search.elastic import RESERVED_CHARACTERS
from search.tests.mock_search_engine import MockSearchEngine, json_date_to_datetime
from search.tests.tests import MockSearchTests
from search.tests.utils import ErroringElasticImpl, SearcherMixin


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
class ElasticSearchTests(MockSearchTests):
    """ Override that runs the same tests for ElasticSearchEngine instead of MockSearchEngine """

    def test_reserved_characters(self):
        """ Make sure that we handle when reserved characters were passed into query_string """
        test_string = "What the ! is this?"
        self.searcher.index([{"content": {"name": test_string}}])

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

    def test_aggregation_options(self):
        """
        Test that aggregate options work alongside aggregations - notice
        unsupported in mock for now size - is the only option for now
        """
        self._index_for_aggs()

        response = self.searcher.search()
        self.assertEqual(response["total"], 7)
        self.assertNotIn("aggs", response)

        aggregation_terms = {
            "subject": {"size": 2},
            "org": {"size": 2}
        }
        response = self.searcher.search(aggregation_terms=aggregation_terms)
        self.assertEqual(response["total"], 7)
        self.assertIn("aggs", response)
        aggregation_results = response["aggs"]
        self.assertEqual(aggregation_results["subject"]["total"], 6)
        subject_term_counts = aggregation_results["subject"]["terms"]
        self.assertEqual(subject_term_counts["mathematics"], 3)
        self.assertEqual(subject_term_counts["physics"], 2)
        self.assertNotIn("history", subject_term_counts)
        self.assertEqual(aggregation_results["subject"]["other"], 1)

        self.assertEqual(aggregation_results["org"]["total"], 7)
        org_term_counts = aggregation_results["org"]["terms"]
        self.assertEqual(org_term_counts["Harvard"], 4)
        self.assertEqual(org_term_counts["MIT"], 2)
        self.assertNotIn("edX", org_term_counts)
        self.assertEqual(aggregation_results["org"]["other"], 1)


@override_settings(MOCK_SEARCH_BACKING_FILE="./testfile.pkl")
class FileBackedMockSearchTests(MockSearchTests):
    """ Override that runs the same tests with file-backed MockSearchEngine """

    def setUp(self):
        super(FileBackedMockSearchTests, self).setUp()
        MockSearchEngine.create_test_file()
        self._searcher = None

    def tearDown(self):
        MockSearchEngine.destroy_test_file()
        self._searcher = None
        super(FileBackedMockSearchTests, self).tearDown()

    def test_file_value_formats(self):
        """ test the format of values that write/read from the file """
        # json serialization removes microseconds part of the datetime object, so
        # we strip it at the beginning to allow equality comparison to be correct
        this_moment = datetime.utcnow().replace(microsecond=0)
        self.searcher.index([
            {
                "content": {
                    "name": "How did 11 of 12 balls get deflated during the game"
                },
                "my_date_value": this_moment,
                "my_integer_value": 172,
                "my_float_value": 57.654,
                "my_string_value": "If the officials just blew it, would they come out and admit it?"
            },
        ])

        # now search should be successful
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 1)

        # and values should be what we desire
        returned_result = response["results"][0]["data"]
        self.assertEqual(
            json_date_to_datetime(returned_result["my_date_value"]),
            this_moment
        )
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
        this_moment = datetime.utcnow().replace(microsecond=0)
        self.searcher.index([
            {
                "id": "FAKE_ID",
                "content": {
                    "name": "How did 11 of 12 balls get deflated during the game"
                },
                "my_date_value": this_moment,
                "my_integer_value": 172,
                "my_float_value": 57.654,
                "my_string_value": "If the officials just blew it, would they come out and admit it?"
            },
        ])
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 1)

        # copy content, and then erase file so that backed file is not present and work is disabled
        initial_file_content = None
        with open("testfile.pkl", "r") as dict_file:
            initial_file_content = json.load(dict_file)
        os.remove("testfile.pkl")

        response = self.searcher.search(query_string="ABC")
        self.assertEqual(response["total"], 0)

        self.searcher.index([{"content": {"name": "ABC"}}])
        # now search should be unsuccessful because file does not exist
        response = self.searcher.search(query_string="ABC")
        self.assertEqual(response["total"], 0)

        # remove it, and then we'll reload file and it still should be there
        self.searcher.remove(["FAKE_ID"])

        MockSearchEngine.create_test_file("fakefile.pkl", initial_file_content)

        # now search should be successful because file did exist in file
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 1)

        self.searcher.remove(["FAKE_ID"])
        response = self.searcher.search(query_string="deflated")
        self.assertEqual(response["total"], 0)


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
@override_settings(ELASTIC_SEARCH_IMPL=ErroringElasticImpl)
class ErroringElasticTests(TestCase, SearcherMixin):
    """ testing handling of elastic exceptions when they happen """

    def test_index_failure_bulk(self):
        """ the index operation should fail """
        with patch('search.elastic.bulk', return_value=[0, [exceptions.ElasticsearchException()]]):
            with self.assertRaises(exceptions.ElasticsearchException):
                self.searcher.index([{"name": "abc test"}])

    def test_index_failure_general(self):
        """ the index operation should fail """
        with patch('search.elastic.bulk', side_effect=Exception()):
            with self.assertRaises(Exception):
                self.searcher.index([{"name": "abc test"}])

    def test_search_failure(self):
        """ the search operation should fail """
        with self.assertRaises(exceptions.ElasticsearchException):
            self.searcher.search("abc test")

    def test_remove_failure_bulk(self):
        """ the remove operation should fail """
        doc_id = 'test_id'
        error = {'delete': {
            'status': 500, '_index': 'test_index', '_version': 1, 'found': True, '_id': doc_id
        }}
        with patch('search.elastic.bulk', side_effect=BulkIndexError('Simulated error', [error])):
            with self.assertRaises(BulkIndexError):
                self.searcher.remove(["test_id"])

    def test_remove_failure_general(self):
        """ the remove operation should fail """
        with patch('search.elastic.bulk', side_effect=Exception()):
            with self.assertRaises(Exception):
                self.searcher.remove(["test_id"])


@override_settings(SEARCH_ENGINE=None)
class TestNone(TestCase):
    """ Tests correct skipping of operation when no search engine is defined """

    def test_perform_search(self):
        """ search opertaion should yeild an exception with no search engine """
        with self.assertRaises(NoSearchEngineError):
            perform_search("abc test")


@override_settings(SEARCH_ENGINE="search.elastic.ElasticSearchEngine")
@override_settings(ELASTIC_SEARCH_CONFIG=[{'host': '127.0.0.1'}, {'host': 'localhost'}])
class TestElasticConfig(TestCase, SearcherMixin):
    """ Tests correct configuration of the elasticsearch instance. """

    def test_config(self):
        """ should be configured with the correct hosts """
        elasticsearch = self.searcher._es  # pylint: disable=protected-access
        hosts = elasticsearch.transport.hosts
        self.assertEqual(hosts, [{'host': '127.0.0.1'}, {'host': 'localhost'}])

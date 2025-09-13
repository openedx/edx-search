#!/usr/bin/env python
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
""" Tests for search functionality """

import json
import os
from datetime import datetime

from django.test import TestCase
from django.test.utils import override_settings
from search.api import NoSearchEngineError, perform_search
from search.tests.mock_search_engine import MockSearchEngine, json_date_to_datetime
from search.tests.tests import MockSearchTests


@override_settings(MOCK_SEARCH_BACKING_FILE="./testfile.pkl")
class FileBackedMockSearchTests(MockSearchTests):
    """ Override that runs the same tests with file-backed MockSearchEngine """

    def setUp(self):
        super().setUp()
        MockSearchEngine.create_test_file()
        self._searcher = None

    def tearDown(self):
        MockSearchEngine.destroy_test_file()
        self._searcher = None
        super().tearDown()

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
        with open("testfile.pkl", encoding='utf-8') as dict_file:
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


@override_settings(SEARCH_ENGINE=None)
class TestNone(TestCase):
    """ Tests correct skipping of operation when no search engine is defined """

    def test_perform_search(self):
        """ search opertaion should yeild an exception with no search engine """
        with self.assertRaises(NoSearchEngineError):
            perform_search("abc test")

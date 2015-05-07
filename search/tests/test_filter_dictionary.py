# -*- coding: utf-8 -*-
""" Tests for result processors """
from django.test import TestCase
from django.test.utils import override_settings
from mock import patch

from search.api import perform_search
from search.filter_generator import SearchFilterGenerator
from search.search_engine_base import SearchEngine
from search.tests.utils import post_request

from .mock_search_engine import MockSearchEngine

# Any class that inherits from TestCase will cause too-many-public-methods pylint error
# pylint: disable=too-many-public-methods

TEST_INDEX_NAME = "test_index"

# pylint: disable=too-few-public-methods


class UserSearchFilterGenerator(SearchFilterGenerator):
    """
    Override the SearchFilterGenerator so that we only have where the "user" matches the value herein
    """

    def field_dictionary(self, **kwargs):
        filters = super(UserSearchFilterGenerator, self).field_dictionary(**kwargs)
        if "user" in kwargs and kwargs["user"]:
            filters["has_user"] = kwargs["user"]

        return filters


class CourseSearchFilterGenerator(SearchFilterGenerator):
    """
    Override the SearchFilterGenerator so that we only have where the "course" matches the value provided
    """

    def field_dictionary(self, **kwargs):
        filters = super(CourseSearchFilterGenerator, self).field_dictionary(**kwargs)
        if "course_id" in kwargs and kwargs["course_id"]:
            filters["course"] = kwargs["course_id"]

        return filters


class RequestSearchFilterGenerator(SearchFilterGenerator):
    """
    Override the SearchFilterGenerator so that we only have where the "request" matches the value herein
    """

    def field_dictionary(self, **kwargs):
        filters = super(RequestSearchFilterGenerator, self).field_dictionary(**kwargs)
        if "request" in kwargs and kwargs["request"]:
            filters["request_test"] = "test_request"

        return filters


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(COURSEWARE_INDEX_NAME=TEST_INDEX_NAME)
class TestOverrideSearchFilterGenerator(TestCase):
    """ test the correct processing of results using the SEARCH_FILTER_GENERATOR specified class """

    def setUp(self):
        MockSearchEngine.destroy()
        self._searcher = None
        patcher = patch('search.views.track')
        self.mock_tracker = patcher.start()
        self.addCleanup(patcher.stop)

    @override_settings(SEARCH_FILTER_GENERATOR="search.tests.test_filter_dictionary.UserSearchFilterGenerator")
    def test_user_is_available(self):
        """ Tests that user provided into perform_search function gets added as a filter to the filter_dictionary """
        test_user_name = "test_user"
        indexer = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        indexer.index("courseware_content", {"has_user": test_user_name})
        indexer.index("courseware_content", {"has_user": "another_user"})

        results = perform_search(None)
        self.assertEqual(results["total"], 2)

        results = perform_search(None, user=test_user_name)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["has_user"], test_user_name)

    @override_settings(SEARCH_FILTER_GENERATOR="search.tests.test_filter_dictionary.CourseSearchFilterGenerator")
    def test_course_id_is_available(self):
        """ Tests that the given course_id gets added as a filter to the filter_dictionary """
        test_course_id = "ABC/123/DEF"
        indexer = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        indexer.index("courseware_content", {"course": test_course_id})
        indexer.index("courseware_content", {"course": "XYZ/789/UVW"})

        results = perform_search(None)
        self.assertEqual(results["total"], 2)

        results = perform_search(None, course_id=test_course_id)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["course"], test_course_id)

    @override_settings(SEARCH_FILTER_GENERATOR="search.tests.test_filter_dictionary.RequestSearchFilterGenerator")
    def test_request_is_available(self):
        """
        Tests that request object reference makes it through to be processed by filter_dictionary processor
        We come in from a test request, so that we confirm that the request is a real one
        """
        indexer = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        indexer.index("courseware_content", {
            "id": "FAKE_ID_1",
            "request_test": "test_request",
            "content": {"display_name": "This RedSox 8th inning break brought to you by Southwest"}
        })
        indexer.index("courseware_content", {
            "id": "FAKE_ID_1",
            "request_test": "should_not_show",
            "content": {"display_name": "So it's root, root, root for the RedSox; if they don't win it's a shame"}
        })

        code, results = post_request({"search_string": "RedSox"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 1)

#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
# pylint: disable=too-few-public-methods
""" Tests for search functionalty """
import copy
from datetime import datetime

from django.test import TestCase
from django.test.utils import override_settings
from elasticsearch import Elasticsearch

from search.api import course_discovery_search, NoSearchEngineError
from search.elastic import ElasticSearchEngine
from search.tests.utils import SearcherMixin, TEST_INDEX_NAME

from .mock_search_engine import MockSearchEngine


class DemoCourse(object):
    """ Class for dispensing demo courses """
    DEMO_COURSE_ID = "edX/DemoX/Demo_Course"
    DEMO_COURSE = {
        "start": datetime(2014, 2, 1),
        "number": "DemoX",
        "content": {
            "short_description": "Short description",
            "overview": "Long overview page",
            "display_name": "edX Demonstration Course",
            "number": "DemoX"
        },
        "course": "edX/DemoX/Demo_Course",
        "image_url": "/c4x/edX/DemoX/asset/images_course_image.jpg",
        "effort": "5:30",
        "id": DEMO_COURSE_ID,
        "enrollment_start": datetime(2014, 1, 1),
    }

    demo_course_count = 0

    @classmethod
    def get(cls, update_dict=None, remove_fields=None):
        """ get a new demo course """
        cls.demo_course_count += 1
        course_copy = copy.deepcopy(cls.DEMO_COURSE)
        if update_dict:
            if "content" in update_dict:
                course_copy["content"].update(update_dict["content"])
                del update_dict["content"]
            course_copy.update(update_dict)
        course_copy.update({"id": "{}_{}".format(course_copy["id"], cls.demo_course_count), })
        if remove_fields:
            for remove_field in remove_fields:
                del course_copy[remove_field]
        return course_copy

    @classmethod
    def reset_count(cls):
        """ go back to zero """
        cls.demo_course_count = 0

    @staticmethod
    def index(searcher, course_info):
        """ Adds course info dictionary to the index """
        searcher.index(doc_type="course_info", body=course_info)

    @classmethod
    def get_and_index(cls, searcher, update_dict=None, remove_fields=None):
        """ Adds course info dictionary to the index """
        cls.index(searcher, cls.get(update_dict, remove_fields))


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={
    "start_date": {"type": "date"},
    "enrollment_start": {"type": "date"},
    "enrollment_end": {"type": "date"}
})
@override_settings(MOCK_SEARCH_BACKING_FILE=None)
@override_settings(COURSEWARE_INDEX_NAME=TEST_INDEX_NAME)
# Any class that inherits from TestCase will cause too-many-public-methods pylint error
class TestMockCourseDiscoverySearch(TestCase, SearcherMixin):  # pylint: disable=too-many-public-methods
    """
    Tests course discovery activities
    """
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
        DemoCourse.reset_count()
        self._searcher = None

    def tearDown(self):
        # ignore unexpected-keyword-arg; ES python client documents that it can be used
        # pylint: disable=unexpected-keyword-arg
        if self._is_elastic:
            _elasticsearch = Elasticsearch()
            _elasticsearch.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])
        else:
            MockSearchEngine.destroy()

        self._searcher = None

    def test_course_list(self):
        """ No arguments to course_discovery_search should show all available courses"""
        results = course_discovery_search()
        self.assertEqual(results["total"], 0)

        DemoCourse.get_and_index(self.searcher)
        results = course_discovery_search()
        self.assertEqual(results["total"], 1)

    def test_course_matching(self):
        """ Make sure that matches within content can be located and processed """
        results = course_discovery_search("defensive")
        self.assertEqual(results["total"], 0)

        DemoCourse.get_and_index(self.searcher, {"content": {"short_description": "This is a defensive move"}})
        DemoCourse.get_and_index(self.searcher, {"content": {"overview": "Defensive teams often win"}})
        DemoCourse.get_and_index(self.searcher)

        results = course_discovery_search()
        self.assertEqual(results["total"], 3)
        results = course_discovery_search("defensive")
        self.assertEqual(results["total"], 2)

    def test_enroll_date(self):
        """
        Test that we don't show any courses that have no published enrollment date, or an enrollment date in the future
        """
        # demo_course_1 should be found cos it has a date that is valid
        DemoCourse.get_and_index(self.searcher, {"enrollment_start": datetime(2014, 1, 1)})

        # demo_course_2 should not be found because it has enrollment_start date set explicitly to None
        DemoCourse.get_and_index(self.searcher, {"enrollment_start": None})

        # demo_course_3 should not be found because it has enrollment_start date in the future
        DemoCourse.get_and_index(self.searcher, {"enrollment_start": datetime(2114, 1, 1)})

        # demo_course_4 should not be found because it has no enrollment_start specification
        DemoCourse.get_and_index(self.searcher, {}, ["enrollment_start"])

        results = course_discovery_search()
        self.assertEqual(results["total"], 1)

        additional_course = DemoCourse.get()
        DemoCourse.index(self.searcher, additional_course)

        results = course_discovery_search()
        self.assertEqual(results["total"], 2)

        # Mark the course as having ended enrollment
        additional_course["enrollment_end"] = datetime(2015, 1, 1)
        DemoCourse.index(self.searcher, additional_course)

        results = course_discovery_search()
        self.assertEqual(results["total"], 1)


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
class TestElasticCourseDiscoverySearch(TestMockCourseDiscoverySearch):
    """ version of tests that use Elastic Backed index instead of mock """

    def setUp(self):
        super(TestElasticCourseDiscoverySearch, self).setUp()
        self.searcher.index("doc_type_that_is_meaninless_to_bootstrap_index", {"test_doc_type": "bootstrap"})


@override_settings(SEARCH_ENGINE=None)
class TestNone(TestCase):
    """ Tests correct skipping of operation when no search engine is defined """

    def test_perform_search(self):
        """ search opertaion should yeild an exception with no search engine """
        with self.assertRaises(NoSearchEngineError):
            course_discovery_search("abc test")

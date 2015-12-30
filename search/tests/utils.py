""" Test utilities """
import copy
import json
from datetime import datetime

from django.test import Client
from elasticsearch import Elasticsearch, exceptions

from search.elastic import ElasticSearchEngine
from search.search_engine_base import SearchEngine
from search.tests.mock_search_engine import MockSearchEngine

TEST_INDEX_NAME = "test_index"


def post_request(body, course_id=None):
    """ Helper method to post the request and process the response """
    address = '/' if course_id is None else '/{}'.format(course_id)
    response = Client().post(address, body)

    return getattr(response, "status_code", 500), json.loads(getattr(response, "content", None))


def post_discovery_request(body):
    """ Helper method to post the request and process the response """
    address = '/course_discovery/'
    response = Client().post(address, body)

    return getattr(response, "status_code", 500), json.loads(getattr(response, "content", None))


# pylint: disable=too-few-public-methods
class SearcherMixin(object):
    """ Mixin to provide searcher for the tests """
    _searcher = None

    @property
    def searcher(self):
        """ cached instance of search engine """
        if self._searcher is None:
            self._searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        return self._searcher


# We override ElasticSearchEngine class in order to force an index refresh upon index
# otherwise we often get results from the prior state, rendering the tests less useful
class ForceRefreshElasticSearchEngine(ElasticSearchEngine):
    """
    Override of ElasticSearchEngine that forces the update of the index,
    so that tests can reliably query the index immediately after indexing.
    """

    def index(self, doc_type, sources, **kwargs):
        kwargs.update({
            "refresh": True
        })
        super(ForceRefreshElasticSearchEngine, self).index(doc_type, sources, **kwargs)

    def remove(self, doc_type, doc_ids, **kwargs):
        kwargs.update({
            "refresh": True
        })
        super(ForceRefreshElasticSearchEngine, self).remove(doc_type, doc_ids, **kwargs)


class ErroringSearchEngine(MockSearchEngine):
    """ Override to generate search engine error to test """

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None, **kwargs):
        raise StandardError("There is a problem here")


class ErroringIndexEngine(MockSearchEngine):
    """ Override to generate search engine error to test """

    def index(self, doc_type, sources, **kwargs):  # pylint: disable=unused-argument
        raise StandardError("There is a problem here")


class ErroringElasticImpl(Elasticsearch):
    """ Elasticsearch implementation that throws exceptions"""

    # pylint: disable=unused-argument
    def search(self, **kwargs):
        """ this will definitely fail """
        raise exceptions.ElasticsearchException("This search operation failed")


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
    def get(cls, updated_fields=None, fields_to_delete=None):
        """ Get a copy of the dict representing the demo course.

        Args:
            updated_fields (dict): Dictionary of field-value pairs to be updated in the returned value.
            fields_to_delete (List[str]): List of field names that should be removed from the returned value.

        Returns:
            dict: Dictionary representing the demo course.
        """
        cls.demo_course_count += 1
        course = copy.deepcopy(cls.DEMO_COURSE)

        fields_to_delete = fields_to_delete or []
        updated_fields = updated_fields or {}

        # Perform a nested-update (instead of a complete replacement) of the content field.
        if "content" in updated_fields:
            course["content"].update(updated_fields["content"])
            del updated_fields["content"]

        # All other fields can be replaced entirely.
        course.update(updated_fields)

        # Give the course a unique ID
        course["id"] = "{}_{}".format(course["id"], cls.demo_course_count)

        # Remove fields marked for deletion.
        for field in fields_to_delete:
            course.pop(field, None)

        return course

    @classmethod
    def reset_count(cls):
        """ go back to zero """
        cls.demo_course_count = 0

    @staticmethod
    def index(searcher, course_info):
        """ Adds course info dictionary to the index """
        searcher.index(doc_type="course_info", sources=course_info)

    @classmethod
    def get_and_index(cls, searcher, update_dict=None, remove_fields=None):
        """ Adds course info dictionary to the index """
        source = cls.get(update_dict, remove_fields)
        cls.index(searcher, [source])

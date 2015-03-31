""" Test utilities """
import json
from django.test import Client
from elasticsearch import Elasticsearch, exceptions
from search.search_engine_base import SearchEngine
from search.tests.mock_search_engine import MockSearchEngine
from search.elastic import ElasticSearchEngine


TEST_INDEX_NAME = "test_index"


def post_request(body, course_id=None):
    """ Helper method to post the request and process the response """
    address = '/' if course_id is None else '/{}'.format(course_id)
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
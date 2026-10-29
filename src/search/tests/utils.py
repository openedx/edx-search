""" Test utilities """

import json
import time
from django.test import Client
from elasticsearch import Elasticsearch, exceptions
from meilisearch.errors import MeilisearchApiError
from search.search_engine_base import SearchEngine
from search.tests.mock_search_engine import MockSearchEngine
from search.tests.factories import DemoCourse
from search.elastic import ElasticSearchEngine
from search.meilisearch import create_indexes, get_meilisearch_client


TEST_INDEX_NAME = "test_index"


def post_request(body, course_id=None):
    """
    Helper method to post the request and process the response
    """
    address = '/{}'.format(course_id if course_id else '')
    response = Client().post(address, body)

    return getattr(response, "status_code", 500), json.loads(getattr(response, "content", None).decode('utf-8'))


def post_discovery_request(body, address='/course_discovery/'):
    """ Helper method to post the request and process the response """
    response = Client().post(address, body)

    return getattr(response, "status_code", 500), json.loads(getattr(response, "content", None).decode('utf-8'))


# pylint: disable=too-few-public-methods
class SearcherMixin:
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

    def index(self, sources, **kwargs):
        kwargs["refresh"] = True
        super().index(sources, **kwargs)

    def remove(self, doc_ids, **kwargs):
        kwargs["refresh"] = True
        super().remove(doc_ids, **kwargs)


class ErroringSearchEngine(MockSearchEngine):
    """ Override to generate search engine error to test """

    def search(self,
               query_string=None,
               field_dictionary=None,
               filter_dictionary=None,
               **kwargs):  # pylint: disable=arguments-differ
        raise Exception("There is a problem here")


class ErroringIndexEngine(MockSearchEngine):
    """ Override to generate search engine error to test """

    def index(self, sources, **kwargs):
        raise Exception("There is a problem here")


class ErroringElasticImpl(Elasticsearch):
    """ Elasticsearch implementation that throws exceptions"""

    def search(self, **kwargs):  # pylint: disable=arguments-differ
        """ this will definitely fail """
        raise exceptions.ElasticsearchException("This search operation failed")


def setup_meilisearch(index_name, logger):  # pragma: no cover
    """Helper method to set up Meilisearch engine"""
    client = get_meilisearch_client()
    try:
        task_info = client.get_index(index_name).delete()
        client.wait_for_task(task_info.task_uid, timeout_in_ms=5000)
    except MeilisearchApiError:
        pass
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(f"Unexpected error deleting Meilisearch index: {e}")

    create_indexes({index_name: [
        "language", "modes", "org", "catalog_visibility", "enrollment_start", "enrollment_end",
    ]})

    def wait(seconds=1):
        """Add small delay to wait for Meilisearch tasks to complete"""
        time.sleep(seconds)

    return {"search_engine": "search.meilisearch.MeilisearchEngine", "wait": wait}


def setup_elasticsearch(index_name):
    """Helper method to set up Elasticsearch engine"""
    es = Elasticsearch()
    es.indices.delete(index=index_name, ignore=[400, 404])  # pylint: disable=unexpected-keyword-arg
    es.indices.create(index=index_name, ignore=400, body={})  # pylint: disable=unexpected-keyword-arg

    return {"search_engine": "search.tests.utils.ForceRefreshElasticSearchEngine", "wait": lambda: None}


def setup_democourse(searcher):
    """Set up a demo course to use in api tests"""
    DemoCourse.reset_count()
    DemoCourse.get_and_index(
        searcher,
        {
            "org": "OrgA",
            "language": "en",
            "content": {
                "short_description": "Find this one with the right parameter"
            }
        }
    )
    DemoCourse.get_and_index(
        searcher,
        {
            "org": "OrgB",
            "language": "fr",
            "content": {
                "short_description": "Find this one with another parameter"
            }
        }
    )
    DemoCourse.get_and_index(
        searcher,
        {
            "org": "OrgC",
            "language": "en",
            "content": {
                "short_description": "Find this one somehow"
            }
        }
    )

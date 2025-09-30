""" High-level view tests"""
import ddt
import time

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from elasticsearch.client import Elasticsearch
from meilisearch.errors import MeilisearchApiError

from search.meilisearch import create_indexes, get_meilisearch_client
from search.tests.tests import TEST_INDEX_NAME
from search.tests.utils import post_discovery_request, SearcherMixin
from .test_views import MockSearchUrlTest
from .test_course_discovery import DemoCourse


@override_settings(ELASTIC_FIELD_MAPPINGS={
    "start_date": {"type": "date"},
    "enrollment_start": {"type": "date"},
    "enrollment_end": {"type": "date"}
})
@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(COURSEWARE_CONTENT_INDEX_NAME=TEST_INDEX_NAME)
@override_settings(COURSEWARE_INFO_INDEX_NAME=TEST_INDEX_NAME)
class DiscoveryUrlTest(MockSearchUrlTest):
    """
    Make sure that requests to the url get routed to the correct view handler
    """

    def setUp(self):
        super().setUp()
        DemoCourse.reset_count()
        DemoCourse.get_and_index(
            self.searcher, {"org": "OrgA", "content": {"short_description": "Find this one with the right parameter"}}
        )
        DemoCourse.get_and_index(
            self.searcher, {"org": "OrgB", "content": {"short_description": "Find this one with another parameter"}}
        )
        DemoCourse.get_and_index(
            self.searcher, {"content": {"short_description": "Find this one somehow"}}
        )

    def test_search_from_url(self):
        """ test searching using the url """
        code, results = post_discovery_request({})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)

        code, results = post_discovery_request({"search_string": "right"})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 1)

        code, results = post_discovery_request({"search_string": "parameter"})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 2)

        code, results = post_discovery_request({"search_string": "Find this one"})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)

    def test_pagination(self):
        """ test that paging attributes are correctly applied """
        code, results = post_discovery_request({"search_string": "Find this one"})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 3)

        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 1})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_1", result_ids)

        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 1, "page_index": 0})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_1", result_ids)

        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 1, "page_index": 1})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_2", result_ids)

        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 1, "page_index": 2})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_3", result_ids)

        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 2})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_1", result_ids)
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_2", result_ids)

        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 2, "page_index": 0})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_1", result_ids)
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_2", result_ids)

        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 2, "page_index": 1})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_3", result_ids)

    def test_field_matching(self):
        """ test that requests can specify field matches """
        code, results = post_discovery_request({"org": "OrgA"})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 1)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_1", result_ids)

        code, results = post_discovery_request({"org": "OrgB"})
        self.assertTrue(199 < code < 300)
        self.assertEqual(results["total"], 1)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertIn(DemoCourse.DEMO_COURSE_ID + "_2", result_ids)

    def test_page_size_too_large(self):
        """ test searching with too-large page_size """
        code, results = post_discovery_request({"search_string": "Find this one", "page_size": 101})
        self.assertEqual(code, 500)
        self.assertIn("error", results)

    @override_settings(SEARCH_ENGINE="search.tests.utils.ErroringSearchEngine")
    def test_bad_engine(self):
        """ test in place to see how this module behaves when search engine is not available for some reason """
        code, results = post_discovery_request({"search_string": "sun"})
        self.assertGreater(code, 499)
        self.assertEqual(results["error"], 'An error occurred when searching for "sun"')

test_settings = {
    "COURSEWARE_CONTENT_INDEX_NAME": TEST_INDEX_NAME,
    "COURSEWARE_INFO_INDEX_NAME": TEST_INDEX_NAME,
}

def setup_meilisearch():
    client = get_meilisearch_client()
    try:
        client.get_index(TEST_INDEX_NAME).delete()
    except MeilisearchApiError:
        pass

    create_indexes({TEST_INDEX_NAME: [
        "language", "modes", "org", "catalog_visibility", "enrollment_start", "enrollment_end",
    ]})

    def wait():
        task = client.index(TEST_INDEX_NAME).get_tasks().results[-1]
        if not task:
            return
        client.wait_for_task(task.uid)
        time.sleep(0.1)

    return {"search_engine": "search.meilisearch.MeilisearchEngine", "wait": wait}

def setup_elasticsearch():
    es = Elasticsearch()
    es.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])
    es.indices.create(index=TEST_INDEX_NAME, ignore=400, body={})

    return {"search_engine": "search.tests.utils.ForceRefreshElasticSearchEngine", "wait": lambda: None}

# -------------------------------------------------------------------
# Single-value tests (/course_discovery/)
# -------------------------------------------------------------------
@ddt.ddt
@override_settings(**test_settings)
class CourseListSearchSingleValueTest(TestCase, SearcherMixin):
    url = reverse("course_discovery")

    def _init_engine(self, config):
        from django.conf import settings
        settings.SEARCH_ENGINE = config["search_engine"]
        self._searcher = None
        self.wait = config["wait"]

        DemoCourse.reset_count()
        DemoCourse.get_and_index(
            self.searcher, {"org": "OrgA", "content": {"short_description": "Find this one with the right parameter"}}
        )
        DemoCourse.get_and_index(
            self.searcher, {"org": "OrgB", "content": {"short_description": "Find this one with another parameter"}}
        )
        DemoCourse.get_and_index(
            self.searcher, {"content": {"short_description": "Find this one somehow"}}
        )
        self.wait()

    def _post(self, params):
        return post_discovery_request(params, address=self.url)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_search_string(self, label, config):
        """Tests that keyword search returns correct number of matching documents."""
        self._init_engine(config)

        code, results = self._post({})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        code, results = self._post({"search_string": "right"})
        self.assertEqual(results["total"], 1)

        code, results = self._post({"search_string": "parameter"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_org_filter(self, label, config):
        """Tests filtering results by the 'org' facet."""
        self._init_engine(config)

        code, results = self._post({"org": "OrgA"})
        self.assertEqual(results["total"], 1)

        code, results = self._post({"org": "OrgB"})
        self.assertEqual(results["total"], 1)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_search_with_pagination(self, label, config):
        """Tests that pagination limits and offsets results correctly."""
        self._init_engine(config)

        code, results = self._post({"page_size": 2})
        self.assertEqual(len(results["results"]), 2)

        code, results = self._post({"page_size": 2, "page_index": 1})
        self.assertEqual(len(results["results"]), 1)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_bad_search_string(self, label, config):
        """Tests that non-matching search terms return no results."""
        self._init_engine(config)

        code, results = self._post({"search_string": "doesnotexist123"})
        self.assertEqual(results["total"], 0)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_aggregations_basic(self, label, config):
        """Tests that facet aggregations include all indexed orgs."""
        self._init_engine(config)

        code, results = self._post({})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})

        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"].get("OrgA", 0), 1)
        self.assertEqual(aggs["org"]["terms"].get("OrgB", 0), 1)


    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_aggregations_filtered_down(self, label, config):
        """Tests that aggregations reflect active filters correctly."""
        self._init_engine(config)

        code, results = self._post({"org": "OrgA"})
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"].get("OrgA", 0), 1)
        self.assertNotIn("OrgB", aggs["org"]["terms"])

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_aggregations_empty_search(self, label, config):
        """Tests that aggregations are returned even if there are no matches."""
        self._init_engine(config)
        code, results = self._post({"org": "DoesNotExist"})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"], {})

# -------------------------------------------------------------------
# Multi-value tests (/course_list_search/)
# -------------------------------------------------------------------
@ddt.ddt
@override_settings(**test_settings)
class CourseListSearchMultiValueTest(TestCase, SearcherMixin):
    url = reverse("course_list_search")

    def _init_engine(self, config):
        from django.conf import settings
        settings.SEARCH_ENGINE = config["search_engine"]
        self._searcher = None
        self.wait = config["wait"]

        DemoCourse.reset_count()
        DemoCourse.get_and_index(
            self.searcher, {"org": "OrgA", "language": "en",
                            "content": {"short_description": "Find this one with the right parameter"}}
        )
        DemoCourse.get_and_index(
            self.searcher, {"org": "OrgB", "language": "fr",
                            "content": {"short_description": "Find this one with another parameter"}}
        )
        DemoCourse.get_and_index(
            self.searcher, {"org": "OrgC", "language": "en",
                            "content": {"short_description": "Find this one somehow"}}
        )
        self.wait()

    def _post(self, params):
        return post_discovery_request(params, address=self.url)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_search_string(self, label, config):
        """Tests that keyword search returns correct number of matching documents."""
        self._init_engine(config)

        code, results = self._post({})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        code, results = self._post({"search_string": "right"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)

        code, results = self._post({"search_string": "parameter"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_org_filter(self, label, config):
        """Tests filtering results by the 'org' facet."""
        self._init_engine(config)

        code, results = self._post({"org": "OrgA"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgA")

        code, results = self._post({"org": "OrgB"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgB")

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_search_with_pagination(self, label, config):
        """Tests that pagination limits and offsets results correctly."""
        self._init_engine(config)

        code, results = self._post({"page_size": 2})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 2)

        code, results = self._post({"page_size": 2, "page_index": 1})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 1)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_bad_search_string(self, label, config):
        """Tests that non-matching search terms return no results."""
        self._init_engine(config)

        code, results = self._post({"search_string": "doesnotexist123"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 0)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_no_filters_returns_all_aggregations(self, label, config):
        """Tests that full facet counts are returned when no filters are applied."""
        self._init_engine(config)

        code, results = self._post({})
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("language", aggs)
        self.assertEqual(aggs["org"]["terms"]["OrgA"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgB"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgC"], 1)
        self.assertEqual(aggs["language"]["terms"]["en"], 2)
        self.assertEqual(aggs["language"]["terms"]["fr"], 1)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_single_value_filter_keeps_full_facet(self, label, config):
        """Tests that single-value filters preserve all facet options in aggregations."""
        self._init_engine(config)

        code, results = self._post({"language": ["en"]})
        aggs = results.get("aggs", {})
        self.assertIn("fr", aggs["language"]["terms"])

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_multi_value_filter_keeps_full_facet(self, label, config):
        """Tests that multi-value filters preserve all facet options in aggregations."""
        self._init_engine(config)

        code, results = self._post({"language": ["en", "fr"]})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        aggs = results.get("aggs", {})
        self.assertIn("language", aggs)
        self.assertIn("en", aggs["language"]["terms"])
        self.assertIn("fr", aggs["language"]["terms"])
        self.assertEqual(aggs["language"]["terms"]["en"], 2)
        self.assertEqual(aggs["language"]["terms"]["fr"], 1)

    @ddt.data(("meili", setup_meilisearch()), ("es", setup_elasticsearch()))
    @ddt.unpack
    def test_combined_facet_filter_aggregated_correctly(self, label, config):
        """Tests that combining multiple facet filters returns correct aggregations."""
        self._init_engine(config)

        code, results = self._post({"language": ["en"], "org": ["OrgA", "OrgC"]})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("OrgA", aggs["org"]["terms"])
        self.assertIn("OrgC", aggs["org"]["terms"])

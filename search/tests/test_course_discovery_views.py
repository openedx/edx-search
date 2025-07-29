""" High-level view tests"""
import time

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from elasticsearch.client import Elasticsearch

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


@override_settings(
    SEARCH_ENGINE="search.meilisearch.MeilisearchEngine",
    COURSEWARE_CONTENT_INDEX_NAME=TEST_INDEX_NAME,
    COURSEWARE_INFO_INDEX_NAME=TEST_INDEX_NAME,
)
class TestMeilisearchSingleValueDiscoveryUrl(TestCase, SearcherMixin):
    """
    Integration tests for Meilisearch + /course_discovery/ endpoint
    """
    meilisearch_client = get_meilisearch_client()

    def setUp(self):
        super().setUp()
        try:
            self.meilisearch_client.get_index(TEST_INDEX_NAME).delete()
        except Exception:  # pylint: disable=W0718
            pass
        create_indexes({TEST_INDEX_NAME: [
            "language",
            "modes",
            "org",
            "catalog_visibility",
            "enrollment_start",
            "enrollment_end",
        ]})
        self.wait_for_meilisearch_indexing()

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
        self.wait_for_meilisearch_indexing()

    def wait_for_meilisearch_indexing(self):
        """Helper method adding a tiny delay for Meilisearch to finish updating the index."""
        task = self.meilisearch_client.index(TEST_INDEX_NAME).get_tasks().results[-1]
        if not task:
            return
        self.meilisearch_client.wait_for_task(task.uid)
        time.sleep(0.2)

    def test_search_string(self):
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        code, results = post_discovery_request({"search_string": "right"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)

        code, results = post_discovery_request({"search_string": "parameter"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

    def test_org_filter(self):
        code, results = post_discovery_request({"org": "OrgA"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgA")

        code, results = post_discovery_request({"org": "OrgB"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgB")

    def test_search_with_pagination(self):
        code, results = post_discovery_request({"page_size": 2})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 2)

        code, results = post_discovery_request({"page_size": 2, "page_index": 1})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 1)

    def test_bad_search_string(self):
        code, results = post_discovery_request({"search_string": "doesnotexist123"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 0)

    def test_aggregations_basic(self):
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"].get("OrgA", 0), 1)
        self.assertEqual(aggs["org"]["terms"].get("OrgB", 0), 1)

    def test_aggregations_filtered_down(self):
        code, results = post_discovery_request({"org": "OrgA"})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"].get("OrgA", 0), 1)
        self.assertNotIn("OrgB", aggs["org"]["terms"])

    def test_aggregations_empty_search(self):
        code, results = post_discovery_request({"org": "DoesNotExist"})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"], {})


@override_settings(
    SEARCH_ENGINE="search.meilisearch.MeilisearchEngine",
    COURSEWARE_CONTENT_INDEX_NAME=TEST_INDEX_NAME,
    COURSEWARE_INFO_INDEX_NAME=TEST_INDEX_NAME,
)
class TestMeilisearchMultiValueDiscoveryUrl(TestCase, SearcherMixin):
    """
    Integration tests for Meilisearch + /course_discovery_multivalue/ endpoint
    """
    meilisearch_client = get_meilisearch_client()
    multivalue_search_url = reverse("course_discovery_multivalue")

    def setUp(self):
        super().setUp()
        try:
            self.meilisearch_client.get_index(TEST_INDEX_NAME).delete()
        except Exception:  # pylint: disable=W0718
            pass
        create_indexes({TEST_INDEX_NAME: [
            "language",
            "modes",
            "org",
            "catalog_visibility",
            "enrollment_start",
            "enrollment_end",
        ]})
        self.wait_for_meilisearch_indexing()

        DemoCourse.reset_count()
        DemoCourse.get_and_index(
            self.searcher, {
                "org": "OrgA",
                "language": "en",
                "content": {"short_description": "Find this one with the right parameter"}
            }
        )
        DemoCourse.get_and_index(
            self.searcher, {
                "org": "OrgB",
                "language": "fr",
                "content": {"short_description": "Find this one with another parameter"}
            }
        )
        DemoCourse.get_and_index(
            self.searcher, {
                "org": "OrgC",
                "language": "en",
                "content": {"short_description": "Find this one somehow"}
            }
        )
        self.wait_for_meilisearch_indexing()

    def wait_for_meilisearch_indexing(self):
        """Helper method adding a tiny delay for Meilisearch to finish updating the index."""
        task = self.meilisearch_client.index(TEST_INDEX_NAME).get_tasks().results[-1]
        if not task:
            return
        self.meilisearch_client.wait_for_task(task.uid)
        time.sleep(0.2)

    def test_search_string(self):
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        code, results = post_discovery_request({"search_string": "right"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)

        code, results = post_discovery_request({"search_string": "parameter"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

    def test_org_filter(self):
        code, results = post_discovery_request({"org": "OrgA"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgA")

        code, results = post_discovery_request({"org": "OrgB"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgB")

    def test_search_with_pagination(self):
        code, results = post_discovery_request({"page_size": 2})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 2)

        code, results = post_discovery_request({"page_size": 2, "page_index": 1})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 1)

    def test_bad_search_string(self):
        code, results = post_discovery_request(
            {"search_string": "doesnotexist123"}
        )
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 0)

    def test_no_filters_returns_all_aggregations(self):
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("language", aggs)
        self.assertEqual(aggs["org"]["terms"]["OrgA"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgB"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgC"], 1)
        self.assertEqual(aggs["language"]["terms"]["en"], 2)
        self.assertEqual(aggs["language"]["terms"]["fr"], 1)

    def test_single_value_filter_keeps_full_facet(self):
        code, results = post_discovery_request(
            {"language": ["en"]}
        )
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("language", aggs)
        # This is the key difference with multi-facet logic:
        # all language options should be returned, even though "en" is selected
        self.assertIn("en", aggs["language"]["terms"])
        self.assertIn("fr", aggs["language"]["terms"])
        self.assertEqual(results["total"], 2)

    def test_multi_value_filter_keeps_full_facet(self):
        code, results = post_discovery_request(
            {"language": ["en", "fr"]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        aggs = results.get("aggs", {})
        self.assertIn("language", aggs)
        self.assertIn("en", aggs["language"]["terms"])
        self.assertIn("fr", aggs["language"]["terms"])
        self.assertEqual(aggs["language"]["terms"]["en"], 2)
        self.assertEqual(aggs["language"]["terms"]["fr"], 1)

    def test_combined_facet_filter_aggregated_correctly(self):
        code, results = post_discovery_request({"language": ["en"], "org": ["OrgA", "OrgC"]})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("OrgA", aggs["org"]["terms"])
        self.assertIn("OrgC", aggs["org"]["terms"])


@override_settings(
    SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine",
    COURSEWARE_CONTENT_INDEX_NAME=TEST_INDEX_NAME,
    COURSEWARE_INFO_INDEX_NAME=TEST_INDEX_NAME,
)
class TestElasticsearchSingleValueDiscoveryUrl(TestCase, SearcherMixin):
    """
    Integration tests for Elasticsearch + /course_discovery/ endpoint
    """

    def setUp(self):
        super().setUp()
        _elasticsearch = Elasticsearch()
        _elasticsearch.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])  # pylint: disable=unexpected-keyword-arg
        cache.clear()
        config_body = {}
        _elasticsearch.indices.create(index=TEST_INDEX_NAME, ignore=400, body=config_body)  # pylint: disable=unexpected-keyword-arg
        DemoCourse.reset_count()
        self._searcher = None

        DemoCourse.get_and_index(self.searcher, {
            "org": "OrgA", "content": {"short_description": "Find this one with the right parameter"}
        })
        DemoCourse.get_and_index(self.searcher, {
            "org": "OrgB", "content": {"short_description": "Find this one with another parameter"}
        })
        DemoCourse.get_and_index(self.searcher, {
            "content": {"short_description": "Find this one somehow"}
        })

    def tearDown(self):
        _elasticsearch = Elasticsearch()
        _elasticsearch.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])  # pylint: disable=unexpected-keyword-arg
        self._searcher = None
        super().tearDown()

        DemoCourse.reset_count()

    def test_search_string(self):
        """Tests that keyword search returns correct number of matching documents."""
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        code, results = post_discovery_request({"search_string": "right"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)

        code, results = post_discovery_request({"search_string": "parameter"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

    def test_org_filter(self):
        """Tests filtering results by the 'org' facet."""
        code, results = post_discovery_request({"org": "OrgA"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgA")

        code, results = post_discovery_request({"org": "OrgB"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgB")

    def test_search_with_pagination(self):
        """Tests that pagination limits and offsets results correctly."""
        code, results = post_discovery_request({"page_size": 2})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 2)

        code, results = post_discovery_request({"page_size": 2, "page_index": 1})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 1)

    def test_bad_search_string(self):
        """Tests that non-matching search terms return no results."""
        code, results = post_discovery_request({"search_string": "doesnotexist123"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 0)

    def test_aggregations_basic(self):
        """Tests that facet aggregations include all indexed orgs."""
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"].get("OrgA", 0), 1)
        self.assertEqual(aggs["org"]["terms"].get("OrgB", 0), 1)

    def test_aggregations_filtered_down(self):
        """Tests that aggregations reflect active filters correctly."""
        code, results = post_discovery_request({"org": "OrgA"})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"].get("OrgA", 0), 1)
        self.assertNotIn("OrgB", aggs["org"]["terms"])

    def test_aggregations_empty_search(self):
        """Tests that aggregations are returned even if there are no matches."""
        code, results = post_discovery_request({"org": "DoesNotExist"})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertEqual(aggs["org"]["terms"], {})


@override_settings(
    SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine",
    COURSEWARE_CONTENT_INDEX_NAME=TEST_INDEX_NAME,
    COURSEWARE_INFO_INDEX_NAME=TEST_INDEX_NAME,
)
class TestElasticsearchMultiValueDiscoveryUrl(TestCase, SearcherMixin):
    """
    Integration tests for Elasticsearch + /course_discovery_multivalue/ endpoint
    """

    multivalue_search_url = reverse("course_discovery_multivalue")

    def setUp(self):
        super().setUp()
        _elasticsearch = Elasticsearch()
        _elasticsearch.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])  # pylint: disable=unexpected-keyword-arg
        cache.clear()
        config_body = {}
        _elasticsearch.indices.create(index=TEST_INDEX_NAME, ignore=400, body=config_body)  # pylint: disable=unexpected-keyword-arg
        DemoCourse.reset_count()
        self._searcher = None

        DemoCourse.get_and_index(
            self.searcher, {
                "org": "OrgA",
                "language": "en",
                "content": {"short_description": "Find this one with the right parameter"}
            }
        )
        DemoCourse.get_and_index(
            self.searcher, {
                "org": "OrgB",
                "language": "fr",
                "content": {"short_description": "Find this one with another parameter"}
            }
        )
        DemoCourse.get_and_index(
            self.searcher, {
                "org": "OrgC",
                "language": "en",
                "content": {"short_description": "Find this one somehow"}
            }
        )

    def test_search_string(self):
        """Tests that keyword search returns correct number of matching documents."""
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        code, results = post_discovery_request({"search_string": "right"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)

        code, results = post_discovery_request({"search_string": "parameter"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

    def test_org_filter(self):
        """Tests filtering results by the 'org' facet."""
        code, results = post_discovery_request({"org": "OrgA"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgA")

        code, results = post_discovery_request({"org": "OrgB"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgB")

    def test_search_with_pagination(self):
        """Tests that pagination limits and offsets results correctly."""
        code, results = post_discovery_request({"page_size": 2})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 2)

        code, results = post_discovery_request({"page_size": 2, "page_index": 1})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 1)

    def test_bad_search_string(self):
        """Tests that non-matching search terms return no results."""
        code, results = post_discovery_request(
            {"search_string": "doesnotexist123"}
        )
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 0)

    def test_no_filters_returns_all_aggregations(self):
        """Tests that full facet counts are returned when no filters are applied."""
        code, results = post_discovery_request({})
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("language", aggs)
        self.assertEqual(aggs["org"]["terms"]["OrgA"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgB"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgC"], 1)
        self.assertEqual(aggs["language"]["terms"]["en"], 2)
        self.assertEqual(aggs["language"]["terms"]["fr"], 1)

    def test_single_value_filter_keeps_full_facet(self):
        """Tests that single-value filters preserve all facet options in aggregations."""
        code, results = post_discovery_request(
            {"language": ["en"]}
        )
        self.assertEqual(code, 200)
        aggs = results.get("aggs", {})
        self.assertIn("language", aggs)
        # This is the key difference with multi-facet logic:
        # all language options should be returned, even though "en" is selected
        self.assertIn("en", aggs["language"]["terms"])
        self.assertIn("fr", aggs["language"]["terms"])
        self.assertEqual(results["total"], 2)

    def test_multi_value_filter_keeps_full_facet(self):
        """Tests that multi-value filters preserve all facet options in aggregations."""
        code, results = post_discovery_request(
            {"language": ["en", "fr"]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 3)

        aggs = results.get("aggs", {})
        self.assertIn("language", aggs)
        self.assertIn("en", aggs["language"]["terms"])
        self.assertIn("fr", aggs["language"]["terms"])
        self.assertEqual(aggs["language"]["terms"]["en"], 2)
        self.assertEqual(aggs["language"]["terms"]["fr"], 1)

    def test_combined_facet_filter_aggregated_correctly(self):
        """Tests that combining multiple facet filters returns correct aggregations."""
        code, results = post_discovery_request({"language": ["en"], "org": ["OrgA", "OrgC"]})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("OrgA", aggs["org"]["terms"])
        self.assertIn("OrgC", aggs["org"]["terms"])

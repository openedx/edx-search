""" High-level view tests"""
import uuid
import logging
import ddt

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from search.search_engine_base import SearchEngine
from search.tests.utils import post_discovery_request, setup_meilisearch, setup_elasticsearch, setup_democourse


index_name = f"test_index_{uuid.uuid4().hex}"
logger = logging.getLogger(__name__)


@ddt.ddt
@override_settings(COURSEWARE_CONTENT_INDEX_NAME=index_name, COURSEWARE_INFO_INDEX_NAME=index_name)
class CourseListSearchMultiValueTest(TestCase):
    """
    Multi-value tests (/course_list_search/) for both Meilisearch and Elasticsearch engines.
    """
    url = reverse("course_list_search")
    searcher = ...
    wait = ...

    def _init_engine(self, config):
        """Helper method to initialize the search engine"""
        from django.conf import settings  # pylint: disable=import-outside-toplevel
        settings.SEARCH_ENGINE = config["search_engine"]
        self.searcher = SearchEngine.get_search_engine(settings.COURSEWARE_INFO_INDEX_NAME)
        setup_democourse(self.searcher)
        self.wait = config["wait"]
        self.wait()

    def _post(self, params):
        """Helper method to send a post request"""
        return post_discovery_request(params, address=self.url)

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_search_string(self, label, config):  # pylint: disable=unused-argument
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

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_org_filter(self, label, config):  # pylint: disable=unused-argument
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

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_search_with_pagination(self, label, config):  # pylint: disable=unused-argument
        """Tests that pagination limits and offsets results correctly."""
        self._init_engine(config)

        code, results = self._post({"page_size": 2})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 2)

        code, results = self._post({"page_size": 2, "page_index": 1})
        self.assertEqual(code, 200)
        self.assertEqual(len(results["results"]), 1)

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_bad_search_string(self, label, config):  # pylint: disable=unused-argument
        """Tests that non-matching search terms return no results."""
        self._init_engine(config)

        code, results = self._post({"search_string": "doesnotexist123"})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 0)

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_no_filters_returns_all_aggregations(self, label, config):  # pylint: disable=unused-argument
        """Tests that full facet counts are returned when no filters are applied."""
        self._init_engine(config)

        code, results = self._post({})  # pylint: disable=unused-variable
        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("language", aggs)
        self.assertEqual(aggs["org"]["terms"]["OrgA"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgB"], 1)
        self.assertEqual(aggs["org"]["terms"]["OrgC"], 1)
        self.assertEqual(aggs["language"]["terms"]["en"], 2)
        self.assertEqual(aggs["language"]["terms"]["fr"], 1)

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_single_value_filter_keeps_full_facet(self, label, config):  # pylint: disable=unused-argument
        """Tests that single-value filters preserve all facet options in aggregations."""
        self._init_engine(config)

        _, results = self._post({"language": ["en"]})
        aggs = results.get("aggs", {})
        self.assertIn("fr", aggs["language"]["terms"])

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_multi_value_filter_keeps_full_facet(self, label, config):  # pylint: disable=unused-argument
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

    @ddt.data(("meili", setup_meilisearch(index_name, logger)), ("es", setup_elasticsearch(index_name)))
    @ddt.unpack
    def test_combined_facet_filter_aggregated_correctly(self, label, config):  # pylint: disable=unused-argument
        """Tests that combining multiple facet filters returns correct aggregations."""
        self._init_engine(config)

        code, results = self._post({"language": ["en"], "org": ["OrgA", "OrgC"]})
        self.assertEqual(code, 200)
        self.assertEqual(results["total"], 2)

        aggs = results.get("aggs", {})
        self.assertIn("org", aggs)
        self.assertIn("OrgA", aggs["org"]["terms"])
        self.assertIn("OrgC", aggs["org"]["terms"])

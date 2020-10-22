""" High-level view tests"""

from django.test.utils import override_settings

from search.tests.tests import TEST_INDEX_NAME
from search.tests.utils import post_discovery_request
from .test_views import MockSearchUrlTest
from .test_course_discovery import DemoCourse

# Any class that inherits from TestCase will cause too-many-public-methods pylint error
# pylint: disable=too-many-public-methods


@override_settings(ELASTIC_FIELD_MAPPINGS={  # pylint: disable=too-many-ancestors
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
        super(DiscoveryUrlTest, self).setUp()
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

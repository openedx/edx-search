""" High-level view tests"""
from datetime import datetime
from django.core.urlresolvers import resolve
from django.core.urlresolvers import Resolver404
from django.test import TestCase
from django.test.utils import override_settings
from mock import patch, call
from search.search_engine_base import SearchEngine
from search.tests.mock_search_engine import MockSearchEngine
from search.tests.tests import TEST_INDEX_NAME
from search.tests.utils import post_request, SearcherMixin


# Any class that inherits from TestCase will cause too-many-public-methods pylint error
# pylint: disable=too-many-public-methods
@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
@override_settings(COURSEWARE_INDEX_NAME=TEST_INDEX_NAME)
class MockSearchUrlTest(TestCase, SearcherMixin):
    """
    Make sure that requests to the url get routed to the correct view handler
    """

    def _reset_mocked_tracker(self):
        """ reset mocked tracker and clear logged emits """
        self.mock_tracker.reset_mock()

    def setUp(self):
        MockSearchEngine.destroy()
        self._searcher = None
        patcher = patch('search.views.track')
        self.mock_tracker = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        MockSearchEngine.destroy()
        self._searcher = None

    def assert_no_events_were_emitted(self):
        """Ensures no events were emitted since the last event related assertion"""
        self.assertFalse(self.mock_tracker.emit.called)  # pylint: disable=maybe-no-member

    def assert_search_initiated_event(self, search_term, size, page):
        """Ensures an search initiated event was emitted"""
        initiated_search_call = self.mock_tracker.emit.mock_calls[0]  # pylint: disable=maybe-no-member
        expected_result = call('edx.course.search.initiated', {
            "search_term": unicode(search_term),
            "page_size": size,
            "page_number": page,
        })
        self.assertEqual(expected_result, initiated_search_call)

    def assert_results_returned_event(self, search_term, size, page, total):
        """Ensures an results returned event was emitted"""
        returned_results_call = self.mock_tracker.emit.mock_calls[1]  # pylint: disable=maybe-no-member
        expected_result = call('edx.course.search.results_displayed', {
            "search_term": unicode(search_term),
            "page_size": size,
            "page_number": page,
            "results_count": total,
        })
        self.assertEqual(expected_result, returned_results_call)

    def assert_initiated_return_events(self, search_term, size, page, total):
        """Asserts search initiated and results returned events were emitted"""
        self.assertEqual(self.mock_tracker.emit.call_count, 2)  # pylint: disable=maybe-no-member
        self.assert_search_initiated_event(search_term, size, page)
        self.assert_results_returned_event(search_term, size, page, total)

    def test_url_resolution(self):
        """ make sure that the url is resolved as expected """
        resolver = resolve('/')
        self.assertEqual(resolver.view_name, 'do_search')

        with self.assertRaises(Resolver404):
            resolver = resolve('/blah')

        resolver = resolve('/edX/DemoX/Demo_Course')
        self.assertEqual(resolver.view_name, 'do_search')
        self.assertEqual(resolver.kwargs['course_id'], 'edX/DemoX/Demo_Course')

    def test_search_from_url(self):
        """ test searching using the url """
        self.searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                },
                "test_date": datetime(2015, 1, 1),
                "test_string": "ABC, It's easy as 123"
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling, it's been a year since sun been gone"
                }
            }
        )
        self.searcher.index("test_doc", {"id": "FAKE_ID_3", "content": {"text": "Here comes the sun"}})

        # Test no events called  yet after setup
        self.assert_no_events_were_emitted()
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "sun"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("sun", 20, 0, 2)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Darling"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Darling", 20, 0, 2)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "winter"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" not in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("winter", 20, 0, 1)
        self._reset_mocked_tracker()

        self.assertTrue(results["results"][0]["data"]["test_date"], datetime(2015, 1, 1).isoformat())
        self.assertTrue(results["results"][0]["data"]["test_string"], "ABC, It's easy as 123")

    def test_course_search_url(self):
        """ test searching using the course url """
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC/DEF/GHI",
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC/DEF/GHI",
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling, it's been a year since you've been gone"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "LMN/OPQ/RST",
                "id": "FAKE_ID_3",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )

        # Test no events called  yet after setup
        self.assert_no_events_were_emitted()
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 20, 0, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Darling"}, "ABC/DEF/GHI")
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Darling", 20, 0, 2)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "winter"}, "ABC/DEF/GHI")
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" not in result_ids and "FAKE_ID_3" not in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("winter", 20, 0, 1)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "winter"}, "LMN/OPQ/RST")
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" not in result_ids and "FAKE_ID_2" not in result_ids and "FAKE_ID_3" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("winter", 20, 0, 1)
        self._reset_mocked_tracker()

    def test_empty_search_string(self):
        """ test when search string is provided as empty or null (None) """
        code, results = post_request({"search_string": ""})
        self.assertTrue(code > 499)
        self.assertEqual(results["error"], "No search term provided for search")

        code, results = post_request({"no_search_string_provided": ""})
        self.assertTrue(code > 499)
        self.assertEqual(results["error"], "No search term provided for search")

    def test_pagination(self):  # pylint: disable=too-many-statements
        """ test searching using the course url """
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC",
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling Little Darling Little Darling, it's been a long long lonely winter"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC",
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling Little Darling, it's been a year since you've been gone"
                }
            }
        )
        self.searcher.index(
            "test_doc",
            {
                "course": "XYZ",
                "id": "FAKE_ID_3",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )

        # Test no events called  yet after setup
        self.assert_no_events_were_emitted()
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling"})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 3)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 20, 0, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling", "page_size": 1})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 1, 0, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling", "page_size": 1, "page_index": 0})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 1, 0, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling", "page_size": 1, "page_index": 1})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 1, 1, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling", "page_size": 1, "page_index": 2})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 1, 2, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling", "page_size": 2})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 2, 0, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling", "page_size": 2, "page_index": 0})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 2)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_1" in result_ids and "FAKE_ID_2" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 2, 0, 3)
        self._reset_mocked_tracker()

        code, results = post_request({"search_string": "Little Darling", "page_size": 2, "page_index": 1})
        self.assertTrue(code < 300 and code > 199)
        self.assertEqual(results["total"], 3)
        self.assertEqual(len(results["results"]), 1)
        result_ids = [r["data"]["id"] for r in results["results"]]
        self.assertTrue("FAKE_ID_3" in result_ids)

        # Test initiate search and return results were called - and clear mocked tracker
        self.assert_initiated_return_events("Little Darling", 2, 1, 3)
        self._reset_mocked_tracker()

    def test_page_size_too_large(self):
        """ test searching with too-large page_size """
        self.searcher.index(
            "test_doc",
            {
                "course": "ABC/DEF/GHI",
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )

        code, results = post_request({"search_string": "Little Darling", "page_size": 101})
        self.assertEqual(code, 500)
        self.assertTrue("error" in results)


@override_settings(SEARCH_ENGINE="search.tests.utils.ErroringSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={"start_date": {"type": "date"}})
@override_settings(COURSEWARE_INDEX_NAME=TEST_INDEX_NAME)
class BadSearchTest(TestCase, SearcherMixin):
    """ Make sure that we can error message when there is a problem """

    def setUp(self):
        MockSearchEngine.destroy()

    def tearDown(self):
        MockSearchEngine.destroy()

    def test_search_from_url(self):
        """ ensure that we get the error back when the backend fails """
        searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_1",
                "content": {
                    "text": "Little Darling, it's been a long long lonely winter"
                }
            }
        )
        searcher.index(
            "test_doc",
            {
                "id": "FAKE_ID_2",
                "content": {
                    "text": "Little Darling, it's been a year since sun been gone"
                }
            }
        )
        searcher.index("test_doc", {"id": "FAKE_ID_3", "content": {"text": "Here comes the sun"}})

        code, results = post_request({"search_string": "sun"})
        self.assertTrue(code > 499)
        self.assertEqual(results["error"], 'An error occurred when searching for "sun"')

        with self.assertRaises(StandardError):
            searcher.search(query_string="test search")


@override_settings(SEARCH_ENGINE="search.tests.utils.ErroringIndexEngine")
class BadIndexTest(TestCase, SearcherMixin):
    """ Make sure that we can error message when there is a problem """

    def setUp(self):
        MockSearchEngine.destroy()

    def tearDown(self):
        MockSearchEngine.destroy()

    def test_search_from_url(self):
        """ ensure that we get the error back when the backend fails """
        searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        with self.assertRaises(StandardError):
            searcher.index("test_doc", {"id": "FAKE_ID_3", "content": {"text": "Here comes the sun"}})

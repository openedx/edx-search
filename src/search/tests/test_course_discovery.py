#!/usr/bin/env python
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
""" Tests for search functionalty """

import time
from datetime import datetime
import logging
import ddt

from django.core.cache import cache
from django.test import TestCase
from django.test.utils import override_settings
from elasticsearch import Elasticsearch
from meilisearch.errors import MeilisearchApiError

from search.api import course_discovery_search, NoSearchEngineError
from search.elastic import ElasticSearchEngine
from search.tests.factories import DemoCourse
from search.tests.utils import SearcherMixin, TEST_INDEX_NAME
from search.meilisearch import get_meilisearch_client, create_indexes
from .mock_search_engine import MockSearchEngine


logger = logging.getLogger(__name__)


@override_settings(SEARCH_ENGINE="search.tests.mock_search_engine.MockSearchEngine")
@override_settings(ELASTIC_FIELD_MAPPINGS={
    "start_date": {"type": "date"},
    "enrollment_start": {"type": "date"},
    "enrollment_end": {"type": "date"}
})
@override_settings(MOCK_SEARCH_BACKING_FILE=None)
@override_settings(COURSEWARE_CONTENT_INDEX_NAME=TEST_INDEX_NAME)
@override_settings(COURSEWARE_INFO_INDEX_NAME=TEST_INDEX_NAME)
# Any class that inherits from TestCase will cause too-many-public-methods pylint error
class TestMockCourseDiscoverySearch(TestCase, SearcherMixin):
    """
    Tests course discovery activities
    """

    @property
    def _is_elastic(self):
        """ check search engine implementation, to manage cleanup differently """
        return isinstance(self.searcher, ElasticSearchEngine)

    def setUp(self):
        super().setUp()
        # ignore unexpected-keyword-arg; ES python client documents that it can be used
        # pylint: disable=unexpected-keyword-arg
        if self._is_elastic:
            _elasticsearch = Elasticsearch()
            # Make sure that we are fresh
            _elasticsearch.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])

            # remove cached property mappings (along with everything else)
            cache.clear()

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
        super().tearDown()

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
        DemoCourse.index(self.searcher, [additional_course])

        results = course_discovery_search()
        self.assertEqual(results["total"], 2)

        # Mark the course as having ended enrollment
        additional_course["enrollment_end"] = datetime(2015, 1, 1)
        DemoCourse.index(self.searcher, [additional_course])

        results = course_discovery_search()
        self.assertEqual(results["total"], 1)

    @override_settings(SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING=True)
    def test_enroll_date_skipped(self):
        """
        Test feature flag to skip enrollment start date filtering for course search
        """
        DemoCourse.get_and_index(self.searcher, {"enrollment_start": datetime(2014, 1, 1)})
        DemoCourse.get_and_index(self.searcher, {"enrollment_start": None})
        DemoCourse.get_and_index(self.searcher, {"enrollment_start": datetime(2114, 1, 1)})
        DemoCourse.get_and_index(self.searcher, {}, ["enrollment_start"])
        results = course_discovery_search()
        self.assertEqual(results["total"], 4)

    def test_discovery_field_matching(self):
        """ Test that field specifications only show those results with the desired field values """
        DemoCourse.get_and_index(self.searcher, {"org": "OrgA"})
        DemoCourse.get_and_index(self.searcher, {"org": "OrgB"})

        results = course_discovery_search()
        self.assertEqual(results["total"], 2)

        results = course_discovery_search(field_dictionary={"org": "OrgA"})
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgA")

        results = course_discovery_search(field_dictionary={"org": "OrgB"})
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgB")

    def test_multivalue_field_matching(self):
        """
        Test that field specifications only show those results with the desired
        field values - even when there is an array of possible values
        """
        DemoCourse.get_and_index(self.searcher, {"modes": ["honor", "verified"]})
        DemoCourse.get_and_index(self.searcher, {"modes": ["honor"]})

        results = course_discovery_search()
        self.assertEqual(results["total"], 2)

        results = course_discovery_search(field_dictionary={"modes": "honor"})
        self.assertEqual(results["total"], 2)

        results = course_discovery_search(field_dictionary={"modes": "verified"})
        self.assertEqual(results["total"], 1)

    def test_aggregating(self):
        """
        Tests bucket aggregation results.

        Test that aggregation results are incorporated (org and modes are
        default aggregations).
        """
        DemoCourse.get_and_index(self.searcher, {"org": "OrgA", "modes": ["honor", "verified"]})
        DemoCourse.get_and_index(self.searcher, {"org": "OrgA", "modes": ["honor"]})
        DemoCourse.get_and_index(self.searcher, {"org": "OrgB", "modes": ["honor"]})
        DemoCourse.get_and_index(self.searcher, {"org": "OrgB", "modes": ["verified"]})
        DemoCourse.get_and_index(self.searcher, {"modes": ["other"]})

        results = course_discovery_search()
        self.assertEqual(results["total"], 5)
        self.assertIn("aggs", results)

        self.assertIn("org", results["aggs"])
        self.assertEqual(results["aggs"]["org"]["total"], 4)
        self.assertEqual(results["aggs"]["org"]["terms"]["OrgA"], 2)
        self.assertEqual(results["aggs"]["org"]["terms"]["OrgB"], 2)

        self.assertIn("modes", results["aggs"])
        self.assertEqual(results["aggs"]["modes"]["total"], 6)
        self.assertEqual(results["aggs"]["modes"]["terms"]["honor"], 3)
        self.assertEqual(results["aggs"]["modes"]["terms"]["verified"], 2)
        self.assertEqual(results["aggs"]["modes"]["terms"]["other"], 1)

    @override_settings(COURSE_DISCOVERY_FILTERS=["test_name", "modes"])
    def test_aggregating_filters(self):
        """
        Test that aggregation under consideration can be specified
        by virtue of filters being overriden
        """
        DemoCourse.get_and_index(self.searcher, {"test_name": "Test Name 1", "modes": ["honor", "verified"]})
        DemoCourse.get_and_index(self.searcher, {"test_name": "Test Name 1", "modes": ["honor"]})
        DemoCourse.get_and_index(self.searcher, {"test_name": "Test Name 2", "modes": ["honor"]})
        DemoCourse.get_and_index(self.searcher, {"test_name": "Test Name 2", "modes": ["verified"]})
        DemoCourse.get_and_index(self.searcher, {"modes": ["other"]})

        results = course_discovery_search()
        self.assertEqual(results["total"], 5)
        self.assertIn("aggs", results)

        self.assertNotIn("org", results["aggs"])

        self.assertIn("modes", results["aggs"])
        self.assertEqual(results["aggs"]["modes"]["total"], 6)
        self.assertEqual(results["aggs"]["modes"]["terms"]["honor"], 3)
        self.assertEqual(results["aggs"]["modes"]["terms"]["verified"], 2)
        self.assertEqual(results["aggs"]["modes"]["terms"]["other"], 1)

        self.assertIn("test_name", results["aggs"])
        self.assertEqual(results["aggs"]["test_name"]["total"], 4)
        self.assertEqual(results["aggs"]["test_name"]["terms"]["Test Name 1"], 2)
        self.assertEqual(results["aggs"]["test_name"]["terms"]["Test Name 2"], 2)

    @override_settings(COURSE_DISCOVERY_AGGREGATIONS={"subject": {}, "lang": {}})
    def test_aggregating_override(self):
        """
        Test that aggregation under consideration can be specified
        with custom setting
        """
        DemoCourse.get_and_index(self.searcher, {"subject": "Mathematics", "lang": ["en", "fr"]})
        DemoCourse.get_and_index(self.searcher, {"subject": "Mathematics", "lang": ["en"]})
        DemoCourse.get_and_index(self.searcher, {"subject": "History", "lang": ["en"]})
        DemoCourse.get_and_index(self.searcher, {"subject": "History", "lang": ["fr"]})
        DemoCourse.get_and_index(self.searcher, {"lang": ["de"]})

        results = course_discovery_search()
        self.assertEqual(results["total"], 5)
        self.assertIn("aggs", results)

        self.assertNotIn("org", results["aggs"])
        self.assertNotIn("modes", results["aggs"])

        self.assertIn("subject", results["aggs"])
        self.assertEqual(results["aggs"]["subject"]["total"], 4)
        self.assertEqual(results["aggs"]["subject"]["terms"]["Mathematics"], 2)
        self.assertEqual(results["aggs"]["subject"]["terms"]["History"], 2)

        self.assertIn("lang", results["aggs"])
        self.assertEqual(results["aggs"]["lang"]["total"], 6)
        self.assertEqual(results["aggs"]["lang"]["terms"]["en"], 3)
        self.assertEqual(results["aggs"]["lang"]["terms"]["fr"], 2)
        self.assertEqual(results["aggs"]["lang"]["terms"]["de"], 1)

    def test_sorting_by_start_date(self):
        """
        Test course sorting by start date
        """

        def _transform_dates(results):
            dates = []
            for result in results["results"]:
                start_date = result["data"]["start"]

                if isinstance(start_date, str):
                    start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                dates.append(start_date)
            return dates

        DemoCourse.get_and_index(self.searcher, {"start": datetime(2014, 2, 1)})
        DemoCourse.get_and_index(self.searcher, {"start": datetime(2014, 3, 1)})
        DemoCourse.get_and_index(self.searcher, {"start": datetime(2014, 1, 1)})

        unsorted_results = course_discovery_search()
        self.assertEqual(unsorted_results["total"], 3)

        unsorted_dates = _transform_dates(unsorted_results)
        self.assertEqual(unsorted_dates[0].month, 2)
        self.assertEqual(unsorted_dates[1].month, 3)
        self.assertEqual(unsorted_dates[2].month, 1)

        sorted_results = course_discovery_search(enable_course_sorting_by_start_date=True)
        self.assertEqual(sorted_results["total"], 3)

        sorted_dates = _transform_dates(sorted_results)
        self.assertEqual(sorted_dates[0].month, 1)
        self.assertEqual(sorted_dates[1].month, 2)
        self.assertEqual(sorted_dates[2].month, 3)


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
@ddt.ddt
class TestElasticCourseDiscoverySearch(TestMockCourseDiscoverySearch):
    """ version of tests that use Elastic Backed index instead of mock """

    def test_course_matching_empty_index(self):
        """ Check for empty result count before indexing """
        results = course_discovery_search("defensive")
        self.assertEqual(results["total"], 0)

    @ddt.data(
        ('defensive', 1),
        ('offensive', 2),
        ('move', 3),
        ('"offensive move"', 1),
        ('"offensive move"', 1),
        ('"highly-offensive"', 1),
        ('"highly-offensive teams"', 1),
    )
    @ddt.unpack
    # pylint: disable=arguments-differ
    def test_course_matching(self, term, result_count):
        """ Make sure that matches within content can be located and processed """
        DemoCourse.get_and_index(self.searcher, {
            "content": {
                "short_description": "This is a defensive move",
                "overview": "Defensive teams often win"
            }
        })

        DemoCourse.get_and_index(self.searcher, {
            "content": {
                "short_description": "This is an offensive move",
                "overview": "Offensive teams often win"
            }
        })

        DemoCourse.get_and_index(self.searcher, {
            "content": {
                "short_description": "This is a hyphenated move",
                "overview": "Highly-offensive teams often win"
            }
        })

        results = course_discovery_search()

        self.assertEqual(results["total"], 3)

        results = course_discovery_search(term)
        self.assertEqual(results["total"], result_count)

    def test_aggregating_with_single_values_in_two_facets(self):
        DemoCourse.get_and_index(self.searcher, {
            "language": "en",
            "org": "EDX",
            "modes": "audit",
        })

        DemoCourse.get_and_index(self.searcher, {
            "language": "en",
            "org": "ORG2",
            "modes": "honor",
        })

        results = course_discovery_search(
            search_term="",
            field_dictionary={"language": "en", "org": "EDX"}
        )

        self.assertIn("audit", results["aggs"]["modes"]["terms"])
        self.assertNotIn("honor", results["aggs"]["modes"]["terms"])
        self.assertEqual(results["aggs"]["language"]["terms"], {"en": 1})
        self.assertEqual(results["aggs"]["org"]["terms"], {"EDX": 1})

    def test_aggregating_with_multi_value_facet(self):
        DemoCourse.get_and_index(self.searcher, {
            "org": "EDX",
            "language": "en",
            "modes": "audit",
        })

        DemoCourse.get_and_index(self.searcher, {
            "org": "EDX",
            "language": "fr",
            "modes": "honor",
        })

        DemoCourse.get_and_index(self.searcher, {
            "org": "ORG2",
            "language": "uk",
            "modes": "verified",
        })

        results = course_discovery_search(
            search_term="",
            field_dictionary={"language": ["en", "fr"]},
            is_multivalue=True
        )

        aggregations = results["aggs"]
        self.assertIn("en", aggregations["language"]["terms"])
        self.assertIn("fr", aggregations["language"]["terms"])
        self.assertIn("uk", aggregations["language"]["terms"])
        self.assertDictEqual(aggregations["language"]["terms"], {"en": 1, "fr": 1, "uk": 1})

        self.assertNotIn("verified", aggregations["modes"]["terms"])
        self.assertDictEqual(aggregations["modes"]["terms"], {"audit": 1, "honor": 1})

        self.assertNotIn("ORG2", aggregations["org"]["terms"])
        self.assertDictEqual(aggregations["org"]["terms"], {"EDX": 2})

    def test_aggregating_facet_narrowed_if_single_value_search(self):
        DemoCourse.get_and_index(self.searcher, {"language": "en", "modes": "audit"})

        DemoCourse.get_and_index(self.searcher, {"language": "en", "modes": "honor"})

        results = course_discovery_search(
            search_term="",
            field_dictionary={"modes": ["honor"]},
            is_multivalue=False
        )

        self.assertNotIn("audit", results["aggs"]["modes"]["terms"])
        self.assertDictEqual(results["aggs"]["modes"]["terms"], {'honor': 1})


@override_settings(SEARCH_ENGINE=None)
class TestNone(TestCase):
    """ Tests correct skipping of operation when no search engine is defined """

    def test_perform_search(self):
        """ search opertaion should yeild an exception with no search engine """
        with self.assertRaises(NoSearchEngineError):
            course_discovery_search("abc test")


@override_settings(SEARCH_ENGINE="search.meilisearch.MeilisearchEngine")
@override_settings(COURSEWARE_INFO_INDEX_NAME=TEST_INDEX_NAME)
class TestMeilisearchCourseDiscoverySearch(TestCase, SearcherMixin):
    """
    Integration tests using real Meilisearch engine.
    """

    meilisearch_client = get_meilisearch_client()

    def setUp(self):  # pragma: no cover
        super().setUp()
        try:
            self.meilisearch_client.index(TEST_INDEX_NAME).delete()
        except MeilisearchApiError:
            pass
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning(f"Unexpected error deleting Meilisearch index: {e}")

        create_indexes({TEST_INDEX_NAME: [
            "language",
            "modes",
            "org",
            "catalog_visibility",
            "enrollment_start",
            "enrollment_end",
        ]})
        self.wait_for_meilisearch_indexing()

    def wait_for_meilisearch_indexing(self):  # pragma: no cover
        """Helper method adding a tiny delay for Meilisearch to finish updating the index."""
        task = self.meilisearch_client.index(TEST_INDEX_NAME).get_tasks().results[-1]
        if not task:
            return
        self.meilisearch_client.wait_for_task(task.uid)
        time.sleep(0.1)

    def test_course_matching_empty_index(self):
        """ Check for empty result count before indexing """
        results = course_discovery_search("defensive")
        self.assertEqual(results["total"], 0)

    def test_course_matching(self):
        """ Make sure that matches within content can be located and processed """
        DemoCourse.get_and_index(self.searcher, {
            "content": {
                "short_description": "This is a defensive move",
                "overview": "Defensive teams often win"
            }
        })
        self.wait_for_meilisearch_indexing()

        DemoCourse.get_and_index(self.searcher, {
            "content": {
                "short_description": "This is an offensive move",
                "overview": "Offensive teams often win"
            }
        })
        self.wait_for_meilisearch_indexing()

        DemoCourse.get_and_index(self.searcher, {
            "content": {
                "short_description": "This is a hyphenated move",
                "overview": "Highly-offensive teams often win"
            }
        })
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search()
        self.assertEqual(results["total"], 3)

    @override_settings(COURSE_DISCOVERY_AGGREGATIONS={"subject": {}, "lang": {}})
    def test_aggregating_override(self):
        """
        Test that aggregation under consideration can be specified
        with custom setting
        """
        create_indexes({TEST_INDEX_NAME: [
            "lang",
            "subject",
        ]})

        DemoCourse.get_and_index(self.searcher, {"subject": "Mathematics", "lang": ["en", "fr"]})
        DemoCourse.get_and_index(self.searcher, {"subject": "Mathematics", "lang": ["en"]})
        DemoCourse.get_and_index(self.searcher, {"subject": "History", "lang": ["en"]})
        DemoCourse.get_and_index(self.searcher, {"subject": "History", "lang": ["fr"]})
        DemoCourse.get_and_index(self.searcher, {"lang": ["de"]})
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search()
        self.assertEqual(results["total"], 5)
        self.assertIn("aggs", results)

        self.assertNotIn("org", results["aggs"])
        self.assertNotIn("modes", results["aggs"])

        self.assertIn("subject", results["aggs"])
        self.assertEqual(results["aggs"]["subject"]["total"], 4)
        self.assertEqual(results["aggs"]["subject"]["terms"]["Mathematics"], 2)
        self.assertEqual(results["aggs"]["subject"]["terms"]["History"], 2)

        self.assertIn("lang", results["aggs"])
        self.assertEqual(results["aggs"]["lang"]["total"], 6)
        self.assertEqual(results["aggs"]["lang"]["terms"]["en"], 3)
        self.assertEqual(results["aggs"]["lang"]["terms"]["fr"], 2)
        self.assertEqual(results["aggs"]["lang"]["terms"]["de"], 1)

    def test_course_list(self):
        """ No arguments to course_discovery_search should show all available courses"""
        results = course_discovery_search()
        self.assertEqual(results["total"], 0)

        DemoCourse.get_and_index(self.searcher)
        self.wait_for_meilisearch_indexing()
        results = course_discovery_search()
        self.assertEqual(results["total"], 1)

    def test_discovery_field_matching(self):
        """ Test that field specifications only show those results with the desired field values """
        DemoCourse.get_and_index(self.searcher, {"org": "OrgA"})
        DemoCourse.get_and_index(self.searcher, {"org": "OrgB"})
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search()
        self.assertEqual(results["total"], 2)

        results = course_discovery_search(field_dictionary={"org": "OrgA"})
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgA")

        results = course_discovery_search(field_dictionary={"org": "OrgB"})
        self.assertEqual(results["total"], 1)
        self.assertEqual(results["results"][0]["data"]["org"], "OrgB")

    def test_multivalue_field_matching(self):
        """
        Test that field specifications only show those results with the desired
        field values - even when there is an array of possible values
        """
        DemoCourse.get_and_index(self.searcher, {"modes": ["honor", "verified"]})
        DemoCourse.get_and_index(self.searcher, {"modes": ["honor"]})
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search()
        self.assertEqual(results["total"], 2)

        results = course_discovery_search(field_dictionary={"modes": "honor"})
        self.assertEqual(results["total"], 2)

        results = course_discovery_search(field_dictionary={"modes": "verified"})
        self.assertEqual(results["total"], 1)

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

        self.wait_for_meilisearch_indexing()

        results = course_discovery_search()
        self.assertEqual(results["total"], 1)

        additional_course = DemoCourse.get()
        DemoCourse.index(self.searcher, [additional_course])
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search()
        self.assertEqual(results["total"], 2)

        # Mark the course as having ended enrollment
        additional_course["enrollment_end"] = datetime(2015, 1, 1)
        DemoCourse.index(self.searcher, [additional_course])
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search()
        self.assertEqual(results["total"], 1)

    def test_aggregating(self):
        DemoCourse.get_and_index(self.searcher, {"language": "en", "org": "EDX"})
        self.wait_for_meilisearch_indexing()

        DemoCourse.get_and_index(self.searcher, {"language": "fr", "org": "ORG2"})
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search(
            search_term="",
            field_dictionary={"language": ["en"]},
            is_multivalue=True
        )
        self.assertDictEqual(results["aggs"]["language"]["terms"], {"en": 1, "fr": 1})
        self.assertDictEqual(results["aggs"]["org"]["terms"], {"EDX": 1})

    def test_aggregating_with_single_values_in_two_facets(self):
        DemoCourse.get_and_index(self.searcher, {
            "language": "en",
            "org": "EDX",
            "modes": "audit",
        })
        self.wait_for_meilisearch_indexing()

        DemoCourse.get_and_index(self.searcher, {
            "language": "en",
            "org": "ORG2",
            "modes": "honor",
        })
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search(
            search_term="",
            field_dictionary={"language": "en", "org": "EDX"}
        )

        self.assertIn("audit", results["aggs"]["modes"]["terms"])
        self.assertNotIn("honor", results["aggs"]["modes"]["terms"])
        self.assertEqual(results["aggs"]["language"]["terms"], {"en": 1})
        self.assertEqual(results["aggs"]["org"]["terms"], {"EDX": 1})

    def test_aggregating_with_multi_value_facet(self):
        DemoCourse.get_and_index(self.searcher, {
            "org": "EDX",
            "language": "en",
            "modes": "audit",
        })
        self.wait_for_meilisearch_indexing()

        DemoCourse.get_and_index(self.searcher, {
            "org": "EDX",
            "language": "fr",
            "modes": "honor",
        })
        self.wait_for_meilisearch_indexing()

        DemoCourse.get_and_index(self.searcher, {
            "org": "ORG2",
            "language": "uk",
            "modes": "verified",
        })
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search(
            search_term="",
            field_dictionary={"language": ["en", "fr"]},
            is_multivalue=True
        )

        aggregations = results["aggs"]
        self.assertIn("en", aggregations["language"]["terms"])
        self.assertIn("fr", aggregations["language"]["terms"])
        self.assertIn("uk", aggregations["language"]["terms"])
        self.assertDictEqual(aggregations["language"]["terms"], {"en": 1, "fr": 1, "uk": 1})

        self.assertNotIn("verified", aggregations["modes"]["terms"])
        self.assertDictEqual(aggregations["modes"]["terms"], {"audit": 1, "honor": 1})

        self.assertNotIn("ORG2", aggregations["org"]["terms"])
        self.assertDictEqual(aggregations["org"]["terms"], {"EDX": 2})

    def test_aggregating_facet_narrowed_if_single_value_search(self):
        DemoCourse.get_and_index(self.searcher, {"language": "en", "modes": "audit"})
        self.wait_for_meilisearch_indexing()

        DemoCourse.get_and_index(self.searcher, {"language": "en", "modes": "honor"})
        self.wait_for_meilisearch_indexing()

        results = course_discovery_search(
            search_term="",
            field_dictionary={"modes": ["honor"]},
            is_multivalue=False
        )

        self.assertNotIn("audit", results["aggs"]["modes"]["terms"])
        self.assertDictEqual(results["aggs"]["modes"]["terms"], {'honor': 1})

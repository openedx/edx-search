#!/usr/bin/env python
# Some of the subclasses that get used as settings-overrides will yield this pylint
# error, but they do get used when included as part of the override_settings
""" Tests for search functionality """

from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.test.utils import override_settings
from elasticsearch import exceptions
from elasticsearch.helpers import BulkIndexError
from search.elastic import RESERVED_CHARACTERS, ElasticSearchEngine
from search.tests.tests import MockSearchTests
from search.tests.utils import TEST_INDEX_NAME, ErroringElasticImpl, SearcherMixin


@override_settings(ELASTIC_SEARCH_INDEX_PREFIX='prefixed_')
@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
class ElasticSearchPrefixTests(MockSearchTests):
    """
    Override that runs the same tests for ElasticSearchTests,
    but with a prefixed index name.
    """

    @property
    def index_name(self):
        """
        The search index name to be used for this test.
        """
        return f"prefixed_{TEST_INDEX_NAME}"


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
class ElasticSearchTests(MockSearchTests):
    """ Override that runs the same tests for ElasticSearchEngine instead of MockSearchEngine """

    def test_reserved_characters(self):
        """ Make sure that we handle when reserved characters were passed into query_string """
        test_string = "What the ! is this?"
        self.searcher.index([{"content": {"name": test_string}}])

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string("something else !")
        self.assertEqual(response["total"], 0)

        response = self.searcher.search_string("something ! else")
        self.assertEqual(response["total"], 0)

        for char in RESERVED_CHARACTERS:
            # previously these would throw exceptions
            response = self.searcher.search_string(char)
            self.assertEqual(response["total"], 0)

    def test_aggregation_options(self):
        """
        Test that aggregate options work alongside aggregations - notice
        unsupported in mock for now size - is the only option for now
        """
        self._index_for_aggs()

        response = self.searcher.search()
        self.assertEqual(response["total"], 7)
        self.assertNotIn("aggs", response)

        aggregation_terms = {
            "subject": {"size": 2},
            "org": {"size": 2}
        }
        response = self.searcher.search(aggregation_terms=aggregation_terms)
        self.assertEqual(response["total"], 7)
        self.assertIn("aggs", response)
        aggregation_results = response["aggs"]
        self.assertEqual(aggregation_results["subject"]["total"], 6)
        subject_term_counts = aggregation_results["subject"]["terms"]
        self.assertEqual(subject_term_counts["mathematics"], 3)
        self.assertEqual(subject_term_counts["physics"], 2)
        self.assertNotIn("history", subject_term_counts)
        self.assertEqual(aggregation_results["subject"]["other"], 1)

        self.assertEqual(aggregation_results["org"]["total"], 7)
        org_term_counts = aggregation_results["org"]["terms"]
        self.assertEqual(org_term_counts["Harvard"], 4)
        self.assertEqual(org_term_counts["MIT"], 2)
        self.assertNotIn("edX", org_term_counts)
        self.assertEqual(aggregation_results["org"]["other"], 1)


@override_settings(SEARCH_ENGINE="search.tests.utils.ForceRefreshElasticSearchEngine")
@override_settings(ELASTIC_SEARCH_IMPL=ErroringElasticImpl)
class ErroringElasticTests(TestCase, SearcherMixin):
    """ testing handling of elastic exceptions when they happen """

    def test_index_failure_bulk(self):
        """ the index operation should fail """
        with patch('search.elastic.bulk', return_value=[0, [exceptions.ElasticsearchException()]]):
            with self.assertRaises(exceptions.ElasticsearchException):
                self.searcher.index([{"name": "abc test"}])

    def test_index_failure_general(self):
        """ the index operation should fail """
        with patch('search.elastic.bulk', side_effect=Exception()):
            with self.assertRaises(Exception):
                self.searcher.index([{"name": "abc test"}])

    def test_search_failure(self):
        """ the search operation should fail """
        with self.assertRaises(exceptions.ElasticsearchException):
            self.searcher.search("abc test")

    def test_remove_failure_bulk(self):
        """ the remove operation should fail """
        doc_id = 'test_id'
        error = {'delete': {
            'status': 500, '_index': 'test_index', '_version': 1, 'found': True, '_id': doc_id
        }}
        with patch('search.elastic.bulk', side_effect=BulkIndexError('Simulated error', [error])):
            with self.assertRaises(BulkIndexError):
                self.searcher.remove(["test_id"])

    def test_remove_failure_general(self):
        """ the remove operation should fail """
        with patch('search.elastic.bulk', side_effect=Exception()):
            with self.assertRaises(Exception):
                self.searcher.remove(["test_id"])


@override_settings(SEARCH_ENGINE="search.elastic.ElasticSearchEngine")
@override_settings(ELASTIC_SEARCH_CONFIG=[{'host': '127.0.0.1'}, {'host': 'localhost'}])
class ElasticConfigTest(TestCase, SearcherMixin):
    """ Tests correct configuration of the elasticsearch instance. """

    def test_config(self):
        """ should be configured with the correct hosts """
        elasticsearch = self.searcher._es  # pylint: disable=protected-access
        hosts = elasticsearch.transport.hosts
        self.assertEqual(hosts, [{'host': '127.0.0.1'}, {'host': 'localhost'}])


class ElasticSearchUnitTests(TestCase):
    """
    ElasticSearch tests.
    """

    @patch("search.elastic.Elasticsearch")
    def test_multivalue_aggregations_translated_correctly(self, mock_elasticsearch_class):
        """Tests that multivalue facet aggregations return full facet buckets despite filtering."""
        mock_es = MagicMock()
        mock_elasticsearch_class.return_value = mock_es

        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 2},
                "max_score": 1.0,
                "hits": [
                    {
                        "_source": {"org": "OrgA", "language": "en"},
                        "_score": 1.0
                    },
                    {
                        "_source": {"org": "OrgC", "language": "en"},
                        "_score": 0.8
                    }
                ]
            },
            "aggregations": {
                "global_aggs": {
                    "language": {
                        "doc_count": 3,
                        "values": {
                            "buckets": [
                                {"key": "en", "doc_count": 2},
                                {"key": "fr", "doc_count": 1}
                            ]
                        }
                    },
                    "org": {
                        "doc_count": 2,
                        "values": {
                            "buckets": [
                                {"key": "OrgA", "doc_count": 1},
                                {"key": "OrgC", "doc_count": 1}
                            ]
                        }
                    }
                }
            },
            "took": 2,
        }

        engine = ElasticSearchEngine(index=TEST_INDEX_NAME)

        result = engine.search(
            field_dictionary={"language": ["en"]},
            aggregation_terms={
                "language": {},
                "org": {},
            },
            is_multivalue=True
        )

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["aggs"]["language"]["terms"]["en"], 2)
        self.assertEqual(result["aggs"]["language"]["terms"]["fr"], 1)
        self.assertEqual(set(result["aggs"]["org"]["terms"].keys()), {"OrgA", "OrgC"})

        mock_es.search.assert_called_once()

    @patch("search.elastic.Elasticsearch")
    def test_multivalue_with_empty_filters_uses_match_all(self, mock_elasticsearch_class):
        """Tests that multivalue aggregation works when no filters are applied."""
        mock_es = MagicMock()
        mock_elasticsearch_class.return_value = mock_es

        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 3},
                "max_score": 0.0,
                "hits": []
            },
            "aggregations": {
                "global_aggs": {
                    "language": {
                        "doc_count": 3,
                        "values": {
                            "buckets": [
                                {"key": "en", "doc_count": 2},
                                {"key": "fr", "doc_count": 1}
                            ]
                        }
                    }
                }
            },
            "took": 2,
        }

        engine = ElasticSearchEngine(index=TEST_INDEX_NAME)

        result = engine.search(
            aggregation_terms={"language": {}},
            field_dictionary={},
            is_multivalue=True
        )

        self.assertEqual(result["total"], 3)
        self.assertEqual(result["aggs"]["language"]["terms"]["en"], 2)
        self.assertEqual(result["aggs"]["language"]["terms"]["fr"], 1)

    @patch("search.elastic.Elasticsearch")
    def test_regular_aggregations_do_not_use_global_aggs(self, mock_elasticsearch_class):
        """Tests that single-value aggregation does not include global_aggs wrapper."""
        mock_es = MagicMock()
        mock_elasticsearch_class.return_value = mock_es
        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 1},
                "max_score": 1.0,
                "hits": [{
                    "_source": {"org": "OrgX", "language": "en"},
                    "_score": 1.0
                }]
            },
            "aggregations": {
                "language": {
                    "buckets": [
                        {"key": "en", "doc_count": 1}
                    ],
                    "doc_count_error_upper_bound": 0,
                    "sum_other_doc_count": 0
                },
                "total_language_docs": {"value": 1.0},
                "total_modes_docs": {"value": 1.0},
                "total_org_docs": {"value": 1.0}
            },
            "took": 2,
        }

        engine = ElasticSearchEngine(index=TEST_INDEX_NAME)

        result = engine.search(
            field_dictionary={"language": ["en"]},
            aggregation_terms={"language": {}},
            is_multivalue=False
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["aggs"]["language"]["terms"]["en"], 1)

        call_args = mock_es.search.call_args[1]
        search_body = call_args["body"]
        self.assertIn("aggs", search_body)
        self.assertIn("language", search_body["aggs"])
        self.assertNotIn("global_aggs", search_body["aggs"])

    @patch("search.elastic.Elasticsearch")
    def test_multivalue_aggregations_use_global_aggs(self, mock_elasticsearch_class):
        """Tests that multi-value aggregation includes global_aggs wrapper."""
        mock_es = MagicMock()
        mock_elasticsearch_class.return_value = mock_es
        mock_es.search.return_value = {
            "hits": {
                "total": {"value": 1},
                "max_score": 1.0,
                "hits": [{
                    "_source": {"org": "OrgX", "language": "en"},
                    "_score": 1.0
                }]
            },
            "aggregations": {
                "global_aggs": {
                    "language": {
                        "doc_count": 1,
                        "values": {
                            "buckets": [
                                {"key": "en", "doc_count": 1}
                            ]
                        }
                    }
                }
            },
            "took": 2,
        }

        engine = ElasticSearchEngine(index=TEST_INDEX_NAME)

        result = engine.search(
            field_dictionary={"language": ["en"]},
            aggregation_terms={"language": {}},
            is_multivalue=True
        )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["aggs"]["language"]["terms"]["en"], 1)

        call_args = mock_es.search.call_args[1]
        search_body = call_args["body"]

        self.assertIn("aggs", search_body)
        self.assertIn("global_aggs", search_body["aggs"])
        self.assertIn("language", search_body["aggs"]["global_aggs"]["aggs"])

"""
Test for the Meilisearch search engine.
"""

from datetime import datetime
from unittest.mock import Mock, patch, MagicMock, PropertyMock

import django.test
from django.utils import timezone
import meilisearch
import pytest
from requests import Response

from search.api import course_discovery_aggregations
from search.utils import DateRange, ValueRange
import search.meilisearch


class DocumentEncoderTests(django.test.TestCase):
    """
    JSON encoder unit tests.
    """

    def test_document_encode_without_timezone(self):
        document = {
            "date": timezone.datetime(2024, 12, 31, 5, 0, 0),
        }
        encoder = search.meilisearch.DocumentEncoder()
        encoded = encoder.encode(document)
        assert '{"date": "2024-12-31 05:00:00"}' == encoded

    def test_document_encode_with_timezone(self):
        document = {
            "date": timezone.datetime(
                2024, 12, 31, 5, 0, 0, tzinfo=timezone.get_fixed_timezone(0)
            ),
        }
        encoder = search.meilisearch.DocumentEncoder()
        encoded = encoder.encode(document)
        assert '{"date": "2024-12-31 05:00:00+00:00"}' == encoded

    def test_document_encode_string(self):
        document = {
            "description": "I ♥ strings!",
        }
        encoder = search.meilisearch.DocumentEncoder()
        encoded = encoder.encode(document)
        assert '{"description": "I \\u2665 strings!"}' == encoded


class EngineTests(django.test.TestCase):
    """
    MeilisearchEngine tests.
    """

    aggregation_terms = course_discovery_aggregations()

    def test_index_empty_document(self):
        assert not search.meilisearch.process_nested_document({})

    def test_index_empty_document_raises_key_error(self):
        with pytest.raises(KeyError):
            search.meilisearch.process_document({})

    def test_index(self):
        document = {
            "id": "abcd",
            "name": "My name",
            "title": "My title",
        }
        processed = search.meilisearch.process_document(document)

        # Check that the source document was not modified
        self.assertNotIn(search.meilisearch.PRIMARY_KEY_FIELD_NAME, document)

        # "id" field is preserved
        assert "abcd" == processed["id"]

        # Primary key field
        # can be verified with: echo -n "abcd" | sha1sum
        pk = "81fe8bfe87576c3ecb22426f8e57847382917acf"
        assert pk == processed[search.meilisearch.PRIMARY_KEY_FIELD_NAME]

        # Additional fields
        assert "My name" == processed["name"]
        assert "My title" == processed["title"]

    def test_index_recursive(self):
        document = {"field": {"value": timezone.datetime(2024, 1, 1)}}
        processed = search.meilisearch.process_nested_document(document)
        assert {
            "field": {
                "value": 1704067200.0,
                "value__utcoffset": None,
            }
        } == processed

    def test_index_datetime_no_tz(self):
        # No timezone
        document = {"id": "1", "dt": timezone.datetime(2024, 1, 1)}
        processed = search.meilisearch.process_document(document)
        assert 1704067200.0 == processed["dt"]
        assert processed["dt__utcoffset"] is None
        # reverse serialisation
        reverse = search.meilisearch.process_hit(processed)
        assert document == reverse

    def test_index_datetime_with_tz(self):
        # With timezone
        document = {
            "id": "1",
            "dt": timezone.datetime(
                2024,
                1,
                1,
                tzinfo=timezone.get_fixed_timezone(timezone.timedelta(seconds=3600)),
            ),
        }
        processed = search.meilisearch.process_document(document)
        assert 1704063600.0 == processed["dt"]
        assert 3600 == processed["dt__utcoffset"]
        # reverse serialisation
        reverse = search.meilisearch.process_hit(processed)
        assert document == reverse

    def test_search(self):
        meilisearch_results = {
            "hits": [
                {
                    "id": "id1",
                    search.meilisearch.PRIMARY_KEY_FIELD_NAME: search.meilisearch.id2pk(
                        "id1"
                    ),
                    "title": "title 1",
                    "_rankingScore": 0.8,
                },
                {
                    "id": "id2",
                    search.meilisearch.PRIMARY_KEY_FIELD_NAME: search.meilisearch.id2pk(
                        "id2"
                    ),
                    "title": "title 2",
                    "_rankingScore": 0.2,
                },
            ],
            "query": "demo",
            "processingTimeMs": 14,
            "limit": 20,
            "offset": 0,
            "estimatedTotalHits": 2,
        }
        processed_results = search.meilisearch.process_results(
            meilisearch_results, "index_name"
        )
        assert 14 == processed_results["took"]
        assert 2 == processed_results["total"]
        assert 0.8 == processed_results["max_score"]

        assert 2 == len(processed_results["results"])
        assert {
            "_id": "id1",
            "_index": "index_name",
            "_type": "_doc",
            "data": {
                "id": "id1",
                "title": "title 1",
            },
        } == processed_results["results"][0]

        assert {
            "_id": "id2",
            "_index": "index_name",
            "_type": "_doc",
            "data": {
                "id": "id2",
                "title": "title 2",
            },
        } == processed_results["results"][1]

    def test_search_with_facets(self):
        meilisearch_results = {
            "hits": [],
            "query": "",
            "processingTimeMs": 1,
            "limit": 20,
            "offset": 0,
            "estimatedTotalHits": 0,
            "facetDistribution": {
                "modes": {"audit": 1, "honor": 3},
                "facet2": {"val1": 1, "val2": 2, "val3": 3},
            },
        }
        processed_results = search.meilisearch.process_results(
            meilisearch_results, "index_name"
        )
        aggs = processed_results["aggs"]
        assert {
            "terms": {"audit": 1, "honor": 3},
            "total": 4.0,
            "other": 0,
        } == aggs["modes"]

    def test_search_params(self):
        params = search.meilisearch.get_search_params(aggregation_terms=self.aggregation_terms)
        self.assertTrue(params["showRankingScore"])

        params = search.meilisearch.get_search_params(from_=0, aggregation_terms=self.aggregation_terms)
        assert 0 == params["offset"]

    def test_search_params_exclude_dictionary(self):
        # Simple value
        params = search.meilisearch.get_search_params(
            exclude_dictionary={"course_visibility": "none"},
            aggregation_terms=self.aggregation_terms
        )
        assert ['NOT course_visibility = "none"'] == params["filter"]

        # Multiple IDs
        params = search.meilisearch.get_search_params(
            exclude_dictionary={"id": ["1", "2"]},
            aggregation_terms=self.aggregation_terms
        )
        assert [
            f'NOT {search.meilisearch.PRIMARY_KEY_FIELD_NAME} = "{search.meilisearch.id2pk("1")}"',
            f'NOT {search.meilisearch.PRIMARY_KEY_FIELD_NAME} = "{search.meilisearch.id2pk("2")}"',
        ] == params["filter"]

        params = search.meilisearch.get_search_params(
            exclude_dictionary={"language": ["en", "fr"]},
            aggregation_terms=self.aggregation_terms
        )
        assert ['NOT language = "en"', 'NOT language = "fr"'] == params["filter"]

    def test_search_params_field_dictionary(self):
        params = search.meilisearch.get_search_params(
            field_dictionary={
                "course": "course-v1:testorg+test1+alpha",
                "org": "testorg",
            },
            aggregation_terms=self.aggregation_terms,
        )
        assert [
            'course = "course-v1:testorg+test1+alpha"',
            'org = "testorg"',
        ] == params["filter"]

    def test_engine_search_orgs_list(self):
        params = search.meilisearch.get_search_params(
            field_dictionary={
                'mode': 'honor',
                "org": ["testorg", "testorg2"],
            },
            aggregation_terms=self.aggregation_terms,
        )

        assert [
            'mode = "honor"',
            'org = "testorg" OR org = "testorg2"',
        ] == params["filter"]

    def test_search_params_filter_dictionary(self):
        params = search.meilisearch.get_search_params(
            filter_dictionary={"key": "value"},
            aggregation_terms=self.aggregation_terms,
        )
        assert ['key = "value" OR key NOT EXISTS'] == params["filter"]

    def test_search_params_value_range(self):
        params = search.meilisearch.get_search_params(
            filter_dictionary={"value": ValueRange(lower=1, upper=2)},
            aggregation_terms=self.aggregation_terms,
        )
        assert ["(value >= 1 AND value <= 2) OR value NOT EXISTS"] == params["filter"]

        params = search.meilisearch.get_search_params(
            filter_dictionary={"value": ValueRange(lower=1)},
            aggregation_terms=self.aggregation_terms,
        )
        assert ["value >= 1 OR value NOT EXISTS"] == params["filter"]

    def test_search_params_date_range(self):
        params = search.meilisearch.get_search_params(
            filter_dictionary={
                "enrollment_end": DateRange(
                    lower=datetime(2024, 1, 1), upper=datetime(2024, 1, 2)
                )
            },
            aggregation_terms=self.aggregation_terms,
        )
        assert [
            "(enrollment_end >= 1704067200.0 AND enrollment_end <= 1704153600.0) OR enrollment_end NOT EXISTS"
        ] == params["filter"]

        params = search.meilisearch.get_search_params(
            filter_dictionary={"enrollment_end": DateRange(lower=datetime(2024, 1, 1))},
            aggregation_terms=self.aggregation_terms,
        )
        assert [
            "enrollment_end >= 1704067200.0 OR enrollment_end NOT EXISTS"
        ] == params["filter"]

    def test_search_params_sort_by(self):
        params = search.meilisearch.get_search_params(
            aggregation_terms=self.aggregation_terms,
            sort_by=[
                search.dataclasses.SortField(name="start", order="asc"),
                search.dataclasses.SortField(name="title", order="desc"),
            ]
        )
        assert [
            search.dataclasses.SortField(name="start", order="asc"),
            search.dataclasses.SortField(name="title", order="desc"),
        ] == params["sort"]

        # No sort by
        params = search.meilisearch.get_search_params(aggregation_terms=self.aggregation_terms, sort_by=[])
        assert params.get("sort") is None

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_engine_init(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index
        engine = search.meilisearch.MeilisearchEngine(index="my_index")
        assert engine.index_name == "my_index"

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_engine_index(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index
        engine = search.meilisearch.MeilisearchEngine(index="my_index")
        engine.meilisearch_index.add_documents = Mock()
        document = {
            "id": "abcd",
            "name": "My name",
            "title": "My title",
        }
        processed_document = {
            # Primary key field
            # can be verified with: echo -n "abcd" | sha1sum
            "_pk": "81fe8bfe87576c3ecb22426f8e57847382917acf",
            "id": "abcd",
            "name": "My name",
            "title": "My title",
        }
        engine.index(sources=[document])
        engine.meilisearch_index.add_documents.assert_called_with(
            [processed_document],
            serializer=search.meilisearch.DocumentEncoder,
        )

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_engine_search(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="my_index")
        mock_index.search.return_value = {
            "hits": [
                {
                    "pk": "f381d4f1914235c9532576c0861d09b484ade634",
                    "id": "course-v1:OpenedX+DemoX+DemoCourse",
                    "_rankingScore": 0.865,
                },
            ],
            "query": "demo",
            "processingTimeMs": 0,
            "limit": 20,
            "offset": 0,
            "estimatedTotalHits": 1,
        }

        result = engine.search(
            query_string="abc",
            field_dictionary={
                "course": "course-v1:testorg+test1+alpha",
                "org": "testorg",
            },
            filter_dictionary={"key": "value"},
            exclude_dictionary={"id": ["abcd"]},
            aggregation_terms={"org": 1, "course": 2},
            log_search_params=True,
        )

        engine.meilisearch_index.search.assert_called_once_with(
            "abc",
            {
                "showRankingScore": True,
                "facets": ["org", "course"],
                "filter": [
                    'course = "course-v1:testorg+test1+alpha"',
                    'org = "testorg"',
                    'key = "value" OR key NOT EXISTS',
                    'NOT _pk = "81fe8bfe87576c3ecb22426f8e57847382917acf"',
                ],
            },
        )

        self.assertGreaterEqual(
            result.items(),
            {
                "max_score": 0.865,
                "took": 0,
                "results": [
                    {
                        "_id": "course-v1:OpenedX+DemoX+DemoCourse",
                        "_index": "my_index",
                        "_type": "_doc",
                        "data": {
                            "id": "course-v1:OpenedX+DemoX+DemoCourse",
                            "pk": "f381d4f1914235c9532576c0861d09b484ade634",
                        },
                    },
                ]
            }.items()
        )

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_engine_remove(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index
        engine = search.meilisearch.MeilisearchEngine(index="my_index")
        mock_index.delete_documents = Mock()
        # Primary key field
        # can be verified with: echo -n "abcd" | sha1sum
        doc_id = "abcd"
        doc_pk = "81fe8bfe87576c3ecb22426f8e57847382917acf"
        engine.remove(doc_ids=[doc_id])
        engine.meilisearch_index.delete_documents.assert_called_with([doc_pk])

    def test_multivalue_search_uses_or_to_join_rules_within_facet(self):
        filter_dict = {
            "language": ["en", "fr"]
        }
        rules = search.meilisearch.get_filter_rules(filter_dict, or_fields=["org", "modes", "language"])

        self.assertListEqual(rules, ['language = "en" OR language = "fr"'])

    def test_multivalue_search_expands_selected_facet_without_filtering(self):
        multivalue_distribution = {'en': 1, 'fr': 2}

        engine = search.meilisearch.MeilisearchEngine(index="test_index")
        engine.meilisearch_index.search = Mock(
            return_value={
                'hits': [],
                'query': '',
                'processingTimeMs': 0,
                'limit': 0,
                'offset': 0,
                'estimatedTotalHits': 4,
                'facetDistribution':
                    {'language': multivalue_distribution},
                'facetStats': {}
            }
        )

        original_filter = [
            'language = "en" OR language = "fr"',
            'modes = "audit" OR modes = "honor"',
            'org = "EDX"',
        ]
        selected_facet = 'language'
        actual_distribution = engine._get_expanded_distribution(  # pylint: disable=protected-access
            '', selected_facet, original_filter
        )
        self.assertDictEqual(actual_distribution, multivalue_distribution)
        (query, opt_params), _ = engine.meilisearch_index.search.call_args  # pylint: disable=unused-variable
        self.assertIn(selected_facet, opt_params['facets'])
        self.assertFalse(any(rule.startswith(f'{selected_facet} = ') for rule in opt_params['filter']))

    def test_multivalue_search_merges_expanded_facet_distributions(self):
        engine = search.meilisearch.MeilisearchEngine(index='test_index')
        engine.meilisearch_index.search = Mock(side_effect=[
            {
                "hits": [],
                "query": "",
                "processingTimeMs": 5,
                "limit": 20,
                "offset": 0,
                "estimatedTotalHits": 0,
                "facetDistribution": {
                    "language": {"en": 2},  # Narrowed distribution after selecting a facet value
                    "org": {"EDX": 2}
                },
            },
            {
                "hits": [],
                "facetDistribution": {
                    "language": {"en": 2, "fr": 1}  # Expanded distribution for multivalue search
                }
            }
        ])

        results = engine.search(
            query_string='',
            field_dictionary={'language': ['en']},
            aggregation_terms=self.aggregation_terms,
            is_multivalue=True,
        )
        aggregations = results["aggs"]
        self.assertIn("language", aggregations)
        self.assertIn("org", aggregations)
        self.assertDictEqual(
            aggregations["language"]["terms"],
            {"en": 2, "fr": 1}
        )
        self.assertDictEqual(aggregations["org"]["terms"], {"EDX": 2})

    def test_single_value_search_narrows_selected_facet(self):
        engine = search.meilisearch.MeilisearchEngine(index='test_index')
        engine.meilisearch_index.search = Mock(side_effect=[
            {
                "hits": [],
                "query": "",
                "processingTimeMs": 5,
                "limit": 20,
                "offset": 0,
                "estimatedTotalHits": 0,
                "facetDistribution": {
                    "language": {"en": 2},
                    "org": {"EDX": 2}
                },
            },
            {
                "hits": [],
                "facetDistribution": {
                    "language": {"en": 2, "fr": 1}
                }
            }
        ])

        results = engine.search(
            query_string='',
            field_dictionary={'language': ['en']},
            aggregation_terms=self.aggregation_terms,
            is_multivalue=False,
        )
        aggregations = results["aggs"]
        self.assertIn("language", aggregations)
        self.assertIn("org", aggregations)
        self.assertDictEqual(
            aggregations["language"]["terms"],
            {"en": 2}
        )
        self.assertDictEqual(aggregations["org"]["terms"], {"EDX": 2})

    def test_facet_expansion_not_triggered_if_not_multivalue(self):
        engine = search.meilisearch.MeilisearchEngine(index="test_index")
        engine._expand_facet_distibutions = MagicMock()  # pylint: disable=protected-access
        engine.meilisearch_index.search = Mock(
            return_value={
                "hits": [],
                "facetDistribution": {},
                "estimatedTotalHits": 0,
                "processingTimeMs": 1,
            }
        )
        engine.search(
            field_dictionary={"language": "en"},
            aggregation_terms=self.aggregation_terms,
            is_multivalue=False
        )
        engine._expand_facet_distibutions.assert_not_called()  # pylint: disable=protected-access

    def test_facet_expansion_is_triggered_if_multivalue(self):
        engine = search.meilisearch.MeilisearchEngine(index="test_index")
        engine._expand_facet_distibutions = MagicMock()  # pylint: disable=protected-access
        engine.meilisearch_index.search = Mock(
            return_value={
                "hits": [],
                "facetDistribution": {},
                "estimatedTotalHits": 0,
                "processingTimeMs": 1,
            }
        )
        engine.search(
            query_string="demo",
            field_dictionary={"language": ["en"]},
            aggregation_terms=self.aggregation_terms,
            is_multivalue=True
        )
        engine._expand_facet_distibutions.assert_called_once()  # pylint: disable=protected-access

    def test_multivalue_nonfacet_field_expands_to_multiple_rules(self):
        # "title" is not a facet field
        rules = search.meilisearch.get_filter_rules({"title": ["Intro", "Advanced"]})
        self.assertIn('title = "Intro"', rules)
        self.assertIn('title = "Advanced"', rules)
        # Both values should appear separately
        self.assertEqual(len(rules), 2)


class UtilitiesTests(django.test.TestCase):
    """
    Tests associated to the utility functions of the meilisearch engine.
    """

    @patch.object(search.meilisearch, "wait_for_task_to_succeed")
    def test_create_index(self, mock_wait_for_task_to_succeed) -> None:
        class ClientMock:
            """
            Mocked client
            """
            number_of_calls = 0

            def get_index(self, index_name):
                """Mocked client.get_index method"""
                self.number_of_calls += 1
                if self.number_of_calls == 1:
                    error = meilisearch.errors.MeilisearchApiError("", Response())
                    error.code = "index_not_found"
                    raise error
                if self.number_of_calls == 2:
                    return f"index created: {index_name}"
                # We shouldn't be there
                assert False

        client = Mock()
        client.get_index = Mock(side_effect=ClientMock().get_index)
        result = search.meilisearch.get_or_create_meilisearch_index(client, "my_index")
        assert result == "index created: my_index"
        mock_wait_for_task_to_succeed.assert_called_once()


class IndexSortablesAndTransformTests(django.test.TestCase):
    """
    Tests for INDEX_SORTABLES configuration, sortable attributes functionality, and _transform_sort_by method.
    """

    @patch('search.meilisearch.get_meilisearch_client')
    def test_meilisearch_index_calls_update_sortable_attributes(self, mock_get_client):
        mock_index = Mock()
        mock_client = Mock()
        mock_get_client.return_value = mock_client
        mock_client.index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="test_index")

        # Access the property to trigger the call
        _ = engine.meilisearch_index

        # Verify update_sortable_attributes was called
        mock_index.update_sortable_attributes.assert_called()

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_transform_sort_by_single_field_asc(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="test_index")

        sort_fields = [search.dataclasses.SortField(name="start", order="asc")]
        result = engine._transform_sort_by(sort_fields)  # pylint: disable=protected-access

        assert result == ["start:asc"]

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_transform_sort_by_single_field_desc(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="test_index")

        sort_fields = [search.dataclasses.SortField(name="title", order="desc")]
        result = engine._transform_sort_by(sort_fields)  # pylint: disable=protected-access

        assert result == ["title:desc"]

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_transform_sort_by_multiple_fields(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="test_index")

        sort_fields = [
            search.dataclasses.SortField(name="start", order="desc"),
            search.dataclasses.SortField(name="title", order="asc"),
            search.dataclasses.SortField(name="score", order="desc"),
        ]
        result = engine._transform_sort_by(sort_fields)  # pylint: disable=protected-access

        assert result == ["start:desc", "title:asc", "score:desc"]

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_transform_sort_by_empty_list(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="test_index")

        sort_fields = []
        result = engine._transform_sort_by(sort_fields)  # pylint: disable=protected-access

        assert result == []

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_search_with_sort_by_integration(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="test_index")
        mock_index.search.return_value = {
            "hits": [],
            "query": "",
            "processingTimeMs": 0,
            "limit": 20,
            "offset": 0,
            "estimatedTotalHits": 0,
        }

        sort_fields = [
            search.dataclasses.SortField(name="start", order="desc"),
            search.dataclasses.SortField(name="title", order="asc"),
        ]

        engine.search(query_string="test", sort_by=sort_fields)

        mock_index.search.assert_called_with(
            "test",
            {
                "showRankingScore": True,
                "sort": ["start:desc", "title:asc"],
            },
        )

    @patch('search.meilisearch.MeilisearchEngine.meilisearch_index', new_callable=PropertyMock)
    def test_search_without_sort_by(self, mock_meilisearch_index):
        mock_index = Mock()
        mock_meilisearch_index.return_value = mock_index

        engine = search.meilisearch.MeilisearchEngine(index="test_index")
        mock_index.search.return_value = {
            "hits": [],
            "query": "",
            "processingTimeMs": 0,
            "limit": 20,
            "offset": 0,
            "estimatedTotalHits": 0,
        }

        engine.search(query_string="test")

        mock_index.search.assert_called_with(
            "test",
            {
                "showRankingScore": True,
            },
        )

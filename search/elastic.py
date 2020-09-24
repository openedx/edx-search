"""
Elastic Search implementation for courseware search index
"""
import copy
import logging

from django.conf import settings
from django.core.cache import cache
from elasticsearch import Elasticsearch, exceptions
from elasticsearch.helpers import bulk, BulkIndexError

from search.search_engine_base import SearchEngine
from search.utils import ValueRange, _is_iterable

# log appears to be standard name used for logger
log = logging.getLogger(__name__)

# These are characters that may have special meaning within Elasticsearch.
# We _may_ want to use these for their special uses for certain queries,
# but for analysed fields these kinds of characters are removed anyway, so
# we can safely remove them from analysed matches
RESERVED_CHARACTERS = "+=><!(){}[]^~*:\\/&|?"


def _translate_hits(es_response):
    """
    Provide result set in our desired format from elasticsearch results.

    {
    'aggs': {
        'language': {'other': 0, 'terms': {}, 'total': 0.0},
        'modes': {'other': 0, 'terms': {'honor': 3, 'other': 1, 'verified': 2}, 'total': 6.0},
        'org': {'other': 0, 'terms': {'OrgA': 2, 'OrgB': 2}, 'total': 4.0}
        },
    'max_score': 1.0,
    'results': [
        {
            '_id': 'edX/DemoX/Demo_Course_1',
            '_index': 'test_index',
            '_type': '_doc',
            'data': {
                'content': {
                    'display_name': 'edX Demonstration Course',
                    'number': 'DemoX',
                    'overview': 'Long overview page',
                    'short_description': 'Short description'
                },
                'course': 'edX/DemoX/Demo_Course',
                'effort': '5:30',
                'enrollment_start': '2014-01-01T00:00:00',
                'id': 'edX/DemoX/Demo_Course_1',
                'image_url': '/c4x/edX/DemoX/asset/images_course_image.jpg',
                'modes': ['honor', 'verified'],
                'number': 'DemoX',
                'org': 'OrgA',
                'start': '2014-02-01T00:00:00'
            },
            'score': 1.0
        },
        {
            '_id': 'edX/DemoX/Demo_Course_2',
            '_index': 'test_index',
            '_type': '_doc',
            'data': {
                'content': {
                    'display_name': 'edX Demonstration Course',
                    'number': 'DemoX',
                    'overview': 'Long overview page',
                    'short_description': 'Short description'
                },
                'course': 'edX/DemoX/Demo_Course',
                'effort': '5:30',
                'enrollment_start': '2014-01-01T00:00:00',
                'id': 'edX/DemoX/Demo_Course_2',
                'image_url': '/c4x/edX/DemoX/asset/images_course_image.jpg',
                'modes': ['honor'],
                'number': 'DemoX',
                'org': 'OrgA',
                'start': '2014-02-01T00:00:00'
            },
            'score': 1.0
        },
    ],
    'took': 2,
    'total': 5
    }

    """

    def translate_result(result):
        """
        Any conversion from ES result syntax into our search engine syntax
        """
        translated_result = copy.copy(result)
        translated_result["data"] = translated_result.pop("_source")
        translated_result["score"] = translated_result.pop("_score")
        return translated_result

    def translate_agg_bucket(bucket, agg_result):
        """
        Any conversion from ES aggregations result into our search engine syntax

        agg_result argument needs for getting total number of
        documents per bucket.

        :param bucket: string
        :param agg_result: dict
        :return: dict
        """
        agg_item = agg_result[bucket]
        terms = {
            bucket["key"]: bucket["doc_count"]
            for bucket in agg_item["buckets"]
        }
        total_docs = (
            agg_result[_get_total_doc_key(bucket)]["value"]
            + agg_item["sum_other_doc_count"]
            + agg_item["doc_count_error_upper_bound"]
        )
        return {
            "terms": terms,
            "total": total_docs,
            "other": agg_item["sum_other_doc_count"],
        }

    results = list(map(translate_result, es_response["hits"]["hits"]))
    response = {
        "took": es_response["took"],
        "total": es_response["hits"]["total"]["value"],
        "max_score": es_response["hits"]["max_score"],
        "results": results,
    }
    if "aggregations" in es_response:
        response["aggs"] = {
            bucket: translate_agg_bucket(bucket, es_response["aggregations"])
            for bucket in es_response["aggregations"]
            if "total_" not in bucket
        }

    return response


def _get_filter_field(field_name, field_value):
    """
    Return field to apply into filter.

    If an array then use a range, otherwise look for a term match.
    """
    filter_query_field = {"term": {field_name: field_value}}
    if isinstance(field_value, ValueRange):
        range_values = {}
        if field_value.lower:
            range_values["gte"] = field_value.lower_string
        if field_value.upper:
            range_values["lte"] = field_value.upper_string
        filter_query_field = {
            "range": {
                field_name: range_values
            }
        }
    elif _is_iterable(field_value):
        filter_query_field = {
            "terms": {
                field_name: field_value
            },
        }
    return filter_query_field


def _process_field_queries(field_dictionary):
    """
    Prepare ES query which must be in the ES record set.
    """
    return [
        _get_filter_field(field, field_value)
        for field, field_value in field_dictionary.items()
    ]


def _process_filters(filter_dictionary):
    """
    Build list for filtering.

    Match records where filtered fields may not exists.
    """
    for field, value in filter_dictionary.items():
        if value:
            yield _get_filter_field(field, value)
        yield {
            "bool": {
                "must_not": {"exists": {"field": field}},
            },
        }


def _process_exclude_dictionary(exclude_dictionary):
    """
    Build a list of term fields which will be excluded from result set.
    """
    for exclude_property, exclude_values in exclude_dictionary.items():
        if not isinstance(exclude_values, list):
            exclude_values = (exclude_values,)
        yield from (
            {"term": {exclude_property: exclude_value}}
            for exclude_value in exclude_values
        )


def _get_total_doc_key(bucket_name):
    """
    Returns additional bucket name for passed bucket.

    Additional buckets are needed for the subsequent counting of
    documents per bucket.
    :param bucket_name: string
    :return: string
    """
    return "total_{}_docs".format(bucket_name)


def _process_aggregation_terms(aggregation_terms):
    """
    We have a list of terms with which we return aggregated result.
    """
    elastic_aggs = {}
    for bucket, options in aggregation_terms.items():
        agg_term = {agg_option: options[agg_option] for agg_option in options}
        agg_term["field"] = bucket
        elastic_aggs[bucket] = {
            "terms": agg_term
        }

        # creates sum bucket which stores total number of documents per bucket
        elastic_aggs[_get_total_doc_key(bucket)] = {
            "sum_bucket": {
                "buckets_path": bucket + "._count"
            }
        }

    return elastic_aggs


class ElasticSearchEngine(SearchEngine):
    """
    ElasticSearch implementation of SearchEngine abstraction
    """

    @staticmethod
    def get_cache_item_name(index_name):
        """
        Name-formatter for cache_item_name
        """
        return "elastic_search_mappings_{}".format(index_name)

    @classmethod
    def get_mappings(cls, index_name):
        """
        Fetch mapped-items structure from cache
        """
        return cache.get(cls.get_cache_item_name(index_name), {})

    @classmethod
    def set_mappings(cls, index_name, mappings):
        """
        Set new mapped-items structure into cache
        """
        cache.set(cls.get_cache_item_name(index_name), mappings)

    @classmethod
    def log_indexing_error(cls, indexing_errors):
        """
        Logs indexing errors and raises a general ElasticSearch Exception
        """
        raise exceptions.ElasticsearchException(', '.join(map(str, indexing_errors)))

    @property
    def mappings(self):
        """
        Get mapping of current index.

        Mappings format in elasticsearch is as follows:
        {
            "properties": {
                "nested_property": {
                    "properties": {
                        "an_analysed_property": {
                            "type": "string"
                        },
                        "another_analysed_property": {
                            "type": "string"
                        }
                    }
                },
                "a_not_analysed_property": {
                    "type": "string",
                },
                "a_date_property": {
                    "type": "date"
                }
            }
        }

        We cache the properties of each index, if they are not available,
        we'll load them again from Elasticsearch
        """
        # Try loading the mapping from the cache.
        mapping = ElasticSearchEngine.get_mappings(self.index_name)

        # Fall back to Elasticsearch
        if not mapping:
            mapping = self._es.indices.get_mapping(
                index=self.index_name
            ).get(self.index_name, {}).get("mappings", {})
            # Cache the mapping, if one was retrieved
            if mapping:
                ElasticSearchEngine.set_mappings(self.index_name, mapping)

        return mapping

    def _clear_mapping(self):
        """
        Remove the cached mappings.

        Next time ES mappings is are requested.
        """
        ElasticSearchEngine.set_mappings(self.index_name, {})

    def __init__(self, index=None):
        super(ElasticSearchEngine, self).__init__(index)
        es_config = getattr(settings, "ELASTIC_SEARCH_CONFIG", [{}])
        self._es = getattr(settings, "ELASTIC_SEARCH_IMPL", Elasticsearch)(es_config)
        if not self._es.indices.exists(index=self.index_name):
            self._es.indices.create(index=self.index_name)

    def _check_mappings(self, body):
        """
        Put mapping to the index.

        We desire to index content so that anything we want to be textually
        searchable(and therefore needing to be analysed), but the other fields
        are designed to be filters, and only require an exact match. So, we want
        to set up the mappings for these fields as "not_analyzed" - this will
        allow our filters to work faster because they only have to work off
        exact matches.
        """

        # Make fields other than content be indexed as unanalyzed terms - content
        # contains fields that are to be analyzed
        exclude_fields = ["content"]
        field_properties = getattr(settings, "ELASTIC_FIELD_MAPPINGS", {})

        def field_property(field_name, field_value):
            """
            Build fields as ES syntax.

            Prepares field as property syntax for providing correct
            mapping desired for field.

            Mappings format in elasticsearch is as follows:
            {
                "properties": {
                    "nested_property": {
                        "properties": {
                            "an_analysed_property": {
                                "type": "string"
                            },
                            "another_analysed_property": {
                                "type": "string"
                            }
                        }
                    },
                    "a_not_analysed_property": {
                        "type": "string"
                    },
                    "a_date_property": {
                        "type": "date"
                    }
                }
            }

            We can only add new ones, but the format is the same
            """
            prop_val = {"type": "keyword"}
            if field_name in field_properties:
                prop_val = field_properties[field_name]
            elif isinstance(field_value, dict):
                props = {fn: field_property(fn, field_value[fn]) for fn in field_value}
                prop_val = {"properties": props}

            return prop_val

        new_properties = {
            field: field_property(field, value)
            for field, value in body.items()
            if (field not in exclude_fields) and (field not in self.mappings.get("properties", {}))
        }

        if new_properties:
            self._es.indices.put_mapping(
                index=self.index_name,
                body={"properties": new_properties}
            )
            self._clear_mapping()

    def index(self, sources, **kwargs):
        """
        Implements call to add documents to the ES index.

        Note the call to _check_mappings which will setup fields with
        the desired mappings.
        """

        try:
            actions = []
            for source in sources:
                self._check_mappings(source)
                id_ = source.get("id")
                log.debug("indexing object with id %s", id_)
                action = {
                    "_index": self.index_name,
                    "_id": id_,
                    "_source": source
                }
                actions.append(action)
            # bulk() returns a tuple with summary information
            # number of successfully executed actions and number of errors
            # if stats_only is set to True.
            _, indexing_errors = bulk(self._es, actions, **kwargs)
            if indexing_errors:
                ElasticSearchEngine.log_indexing_error(indexing_errors)
        # Broad exception handler to protect around bulk call
        except exceptions.ElasticsearchException as ex:
            log.exception("Error during ES bulk operation.")
            raise

    def remove(self, doc_ids, **kwargs):
        """
        Implements call to remove the documents from the index
        """

        try:
            actions = []
            for doc_id in doc_ids:
                log.debug("Removing document with id %s", doc_id)
                action = {
                    "_op_type": "delete",
                    "_index": self.index_name,
                    "_id": doc_id
                }
                actions.append(action)
            bulk(self._es, actions, **kwargs)
        except BulkIndexError as ex:
            valid_errors = [error for error in ex.errors if error["delete"]["status"] != 404]

            if valid_errors:
                log.exception("An error occurred while removing documents from the index: %r", valid_errors)
                raise

    def search(self,
               query_string=None,
               field_dictionary=None,
               filter_dictionary=None,
               exclude_dictionary=None,
               aggregation_terms=None,
               exclude_ids=None,
               use_field_match=False,
               **kwargs):  # pylint: disable=arguments-differ, unused-argument
        """
        Implements call to search the index for the desired content.

        Args:
            query_string (str): the string of values upon which to search within the
            content of the objects within the index

            field_dictionary (dict): dictionary of values which _must_ exist and
            _must_ match in order for the documents to be included in the results

            filter_dictionary (dict): dictionary of values which _must_ match if the
            field exists in order for the documents to be included in the results;
            documents for which the field does not exist may be included in the
            results if they are not otherwise filtered out

            exclude_dictionary(dict): dictionary of values all of which which must
            not match in order for the documents to be included in the results;
            documents which have any of these fields and for which the value matches
            one of the specified values shall be filtered out of the result set

            aggregation_terms (dict): dictionary of terms to include within search
            aggregation list - key is the term desired to aggregate upon, and the value is a
            dictionary of extended information to include. Supported right now is a
            size specification for a cap upon how many aggregation results to return (can
            be an empty dictionary to use default size for underlying engine):

            e.g.
            {
                "org": {"size": 10},  # only show top 10 organizations
                "modes": {}
            }

            (deprecated) use_field_match (bool): flag to indicate whether to use elastic
            filtering or elastic matching for field matches - this is nothing but a
            potential performance tune for certain queries

            (deprecated) exclude_ids (list): list of id values to exclude from the results -
            useful for finding maches that aren't "one of these"

        Returns:
            dict object with results in the desired format
            {
                "_shards": {"failed": 0, "skipped": 0, "successful": 1, "total": 1},
                "took": 3,
                "timed_out": False,
                "hits":  {
                    "hits": [
                        {"_id": "FAKE_ID_1",
                        "_index": "test_index",
                        "_type": "_doc",
                        "data": {"id": "FAKE_ID_1",
                                 "org": "edX",
                                 "subject": "mathematics"},
                        "score": 1.0},
                        {"_id": "FAKE_ID_2",
                        "_index": "test_index",
                        "_type": "_doc",
                        "data": {"id": "FAKE_ID_2",
                                 "org": "MIT",
                                 "subject": "mathematics"},
                        "score": 1.0},
                    ],
                    "max_score": 1.0,
                    "total": {"relation": "eq", "value": 7}
                },
                "aggregation": {
                    "org": {
                        "buckets": [
                            {"doc_count": 4, "key": "Harvard"},
                            {"doc_count": 2, "key": "MIT"}
                        ],
                        "doc_count_error_upper_bound": 0,
                        "sum_other_doc_count": 1
                    },
                    "subject": {
                        "buckets": [
                            {"doc_count": 3, "key": "mathematics"},
                            {"doc_count": 2, "key": "physics"}
                        ],
                        "doc_count_error_upper_bound": 0,
                        "sum_other_doc_count": 1
                    },
                    "total_org_docs": {"value": 6.0},
                    "total_subject_docs": {"value": 5.0}},
                }
            }

        Raises:
            ElasticsearchException when there is a problem with the response from elasticsearch

        Example usage:
            .search(
                "find the words within this string",
                {
                    "must_have_field": "mast_have_value for must_have_field"
                },
                {

                }
            )
        """

        log.debug("searching index with %s", query_string)

        elastic_queries = []
        elastic_filters = []

        # We have a query string, search all fields for matching text
        # within the "content" node
        if query_string:
            query_string = query_string.translate(
                query_string.maketrans("", "", RESERVED_CHARACTERS)
            )

            elastic_queries.append({
                "query_string": {
                    "fields": ["content.*"],
                    "query": query_string
                }
            })

        if field_dictionary:
            # strict match of transferred fields
            elastic_queries.extend(_process_field_queries(field_dictionary))

        if filter_dictionary:
            elastic_filters.extend(_process_filters(filter_dictionary))

        # Support deprecated argument of exclude_ids
        if exclude_ids:
            if not exclude_dictionary:
                exclude_dictionary = {}
            if "_id" not in exclude_dictionary:
                exclude_dictionary["_id"] = []
            exclude_dictionary["_id"].extend(exclude_ids)

        query_segment = {
            "match_all": {}
        }

        if elastic_queries:
            query_segment = {
                "bool": {
                    "must": elastic_queries
                }
            }

        query = query_segment
        if elastic_filters:
            filter_segment = {
                "bool": {
                    "should": elastic_filters
                }
            }
            query = {
                "bool": {
                    "must": query_segment,
                    "filter": filter_segment,
                }
            }

        if exclude_dictionary:
            excluded_fields = list(_process_exclude_dictionary(exclude_dictionary))
            if query.get("bool"):
                query["bool"]["must_not"] = excluded_fields
            else:
                query = {
                    "bool": {
                        "must_not": excluded_fields
                    }
                }

        body = {"query": query}
        if aggregation_terms:
            body["aggs"] = _process_aggregation_terms(aggregation_terms)

        try:
            es_response = self._es.search(index=self.index_name, body=body, **kwargs)
        except exceptions.ElasticsearchException as ex:
            log.exception("error while searching index - %r", ex)
            raise

        return _translate_hits(es_response)

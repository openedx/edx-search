""" Elatic Search implementation for courseware search index """
import copy
import logging

from django.conf import settings
from django.core.cache import cache
from elasticsearch import Elasticsearch, exceptions

from search.manager import SearchEngine
from search.utils import ValueRange

# log appears to be standard name used for logger
log = logging.getLogger(__name__)  # pylint: disable=invalid-name


def _translate_hits(es_response):
    """ Provide resultset in our desired format from elasticsearch results """

    def translate_result(result):
        """ Any conversion from ES result syntax into our search engine syntax """
        translated_result = copy.copy(result)
        data = translated_result.pop("_source")

        translated_result.update({
            "data": data,
            "score": translated_result["_score"]
        })

        return translated_result

    results = [translate_result(hit) for hit in es_response["hits"]["hits"]]
    response = {
        "took": es_response["took"],
        "total": es_response["hits"]["total"],
        "max_score": es_response["hits"]["max_score"],
        "results": results,
    }

    return response


def _get_filter_field(field_name, field_value):
    """ Return field to apply into filter, if an array then use a range, otherwise look for a term match """
    filter_field = None
    if isinstance(field_value, ValueRange):
        range_values = {}
        if field_value.lower:
            range_values.update({"gte": field_value.lower_string})
        if field_value.upper:
            range_values.update({"lte": field_value.upper_string})
        filter_field = {
            "range": {
                field_name: range_values
            }
        }
    else:
        filter_field = {
            "term": {
                field_name: field_value
            }
        }
    return filter_field


def _process_field_dictionary_queries(field_dictionary):
    """
    We have a field_dictionary - we want to match the values for an elasticsearch "match" query
    This is only potentially useful when trying to tune certain search operations
    """
    def field_item(field):
        return {
            "match": {
                field: field_dictionary[field]
            }
        }

    return [field_item(field) for field in field_dictionary]


def _process_field_dictionary_filters(field_dictionary):
    """
    We have a field_dictionary - we match the values using a "term" filter in elasticsearch
    """
    return [_get_filter_field(field, field_value) for field, field_value in field_dictionary.items()]


def _process_filter_dictionary(filter_dictionary):
    """
    We have a filter_dictionary - this means that if the field is included
    and matches, then we can include, OR if the field is undefined, then we
    assume it is safe to include
    """
    def filter_item(field):
        return {
            "or": [
                _get_filter_field(field, filter_dictionary[field]),
                {
                    "missing": {
                        "field": field
                    }
                }
            ]
        }

    return [filter_item(field) for field in filter_dictionary]


class ElasticSearchEngine(SearchEngine):

    """ ElasticSearch implementation of SearchEngine abstraction """

    @staticmethod
    def get_cache_item_name(index_name, doc_type):
        return "elastic_search_mappings_{}".format(
            index_name,
            doc_type
        )

    @classmethod
    def get_mappings(cls, index_name, doc_type):
        return cache.get(cls.get_cache_item_name(index_name, doc_type), {})

    @classmethod
    def set_mappings(cls, index_name, doc_type, mappings):
        cache.set(cls.get_cache_item_name(index_name, doc_type), mappings)

    def _get_mappings(self, doc_type):
        """
        Interfaces with the elasticsearch mappings for the index
        prevents multiple loading of the same mappings from ES when called more than once
        """
        doc_mappings = ElasticSearchEngine.get_mappings(self.index_name, doc_type)
        if not doc_mappings:
            try:
                doc_mappings = self._es.indices.get_mapping(
                    index=self.index_name,
                    doc_type=doc_type,
                )[doc_type]
                ElasticSearchEngine.set_mappings(
                    self.index_name,
                    doc_type,
                    doc_mappings
                )
            except exceptions.NotFoundError:
                # In this case there are no mappings for this doc_type on the elasticsearch server
                # This is a normal case when a new doc_type is being created, and it is expected that
                # we'll hit it for new doc_type s
                return {}

        return doc_mappings

    def _clear_mapping(self, doc_type):
        """ Remove the cached mappings, so that they get loaded from ES next time they are requested """
        ElasticSearchEngine.set_mappings(self.index_name, doc_type, {})

    def __init__(self, index=None):
        super(ElasticSearchEngine, self).__init__(index)
        self._es = Elasticsearch()
        if not self._es.indices.exists(index=self.index_name):
            self._es.indices.create(index=self.index_name)

    def _check_mappings(self, doc_type, body):
        """
        We desire to index content so that anything we want to be textually searchable (and therefore needing to be
        analysed), but the other fields are designed to be filters, and only require an exact match. So, we want to
        set up the mappings for these fields as "not_analyzed" - this will allow our filters to work faster because
        they only have to work off exact matches
        """

        # Make fields other than content be indexed as unanalyzed terms - content
        # contains fields that are to be analyzed
        exclude_fields = ["content"]
        field_properties = getattr(settings, "ELASTIC_FIELD_MAPPINGS", {})

        def field_property(field_name, field_value):
            """ Prepares field as property syntax for providing correct mapping desired for field """
            prop_val = None
            if field_name in field_properties:
                prop_val = field_properties[field_name]
            elif isinstance(field_value, dict):
                props = {fn: field_property(fn, field_value[fn]) for fn in field_value}
                prop_val = {"properties": props}
            else:
                prop_val = {
                    "type": "string",
                    "index": "not_analyzed",
                }

            return prop_val

        new_properties = {
            field: field_property(field, value)
            for field, value in body.items()
            if (field not in exclude_fields) and (field not in self._get_mappings(doc_type))
        }

        if new_properties:
            self._es.indices.put_mapping(
                index=self.index_name,
                doc_type=doc_type,
                body={
                    doc_type: {
                        "properties": new_properties,
                    }
                }
            )
            self._clear_mapping(doc_type)

    def index(self, doc_type, body, **kwargs):
        """
        Implements call to add document to the ES index
        Note the call to _check_mappings which will setup fields with the desired mappings
        """
        id_ = body['id'] if 'id' in body else None

        log.debug("indexing {doc_type} object with id {id_}".format(doc_type=doc_type, id_=id_))

        self._check_mappings(doc_type, body)

        try:
            self._es.index(
                index=self.index_name,
                doc_type=doc_type,
                body=body,
                id=id_,
                ** kwargs
            )
        except exceptions.ElasticsearchException as ex:
            # log information and re-raise
            log.exception("error while indexing - %s", ex.message)
            raise ex

    def remove(self, doc_type, doc_id, **kwargs):
        """ Implements call to remove the document from the index """

        log.debug("remove index for {doc_type} object with id {id_}".format(doc_type=doc_type, id_=doc_id))

        try:
            self._es.delete(
                index=self.index_name,
                doc_type=doc_type,
                id=doc_id,
                # let notfound not cause error
                ignore=[404],
                **kwargs
            )
        except exceptions.ElasticsearchException as ex:
            # log information and re-raise
            log.exception("error while deleting document from index - %s", ex.message)
            raise ex

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None, use_field_match=False, **kwargs):
        """ Implements call to search the index for the desired content """

        log.debug("searching index with %s", query_string)

        elastic_queries = []
        elastic_filters = []

        # We have a query string, search all fields for matching text within the "content" node
        if query_string:
            elastic_queries.append({
                "query_string": {
                    "fields": ["content.*"],
                    "query": query_string
                }
            })

        if field_dictionary:
            if use_field_match:
                elastic_queries.extend(_process_field_dictionary_queries(field_dictionary))
            else:
                elastic_filters.extend(_process_field_dictionary_filters(field_dictionary))

        if filter_dictionary:
            elastic_filters.extend(_process_filter_dictionary(filter_dictionary))

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
                    "must": elastic_filters
                }
            }
            query = {
                "filtered": {
                    "query": query_segment,
                    "filter": filter_segment,
                }
            }

        try:
            es_response = self._es.search(
                index=self.index_name,
                body={"query": query},
                **kwargs
            )
        except exceptions.ElasticsearchException as ex:
            # log information and re-raise
            log.exception("error while searching index - %s", ex.message)
            raise ex

        return _translate_hits(es_response)

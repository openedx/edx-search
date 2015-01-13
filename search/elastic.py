""" Elatic Search implementation for courseware search index """
from django.conf import settings
from elasticsearch import Elasticsearch, exceptions

from search.manager import SearchEngine
from search.utils import ValueRange


def _translate_hits(es_response):
    """ Provide resultset in our desired format from elasticsearch results """

    def process_result(result):
        """ Any conversion from ES result syntax into our search engine syntax """
        data = result.pop("_source")

        result.update({
            "data": data,
            "score": result["_score"]
        })

        return result

    results = [process_result(hit) for hit in es_response["hits"]["hits"]]
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


def _process_field_dictionary(filters, queries, field_dictionary, search_using_fields):
    """
    We have a field_dictionary - make sure that we are matching the values
    Pass switch search_using_fields as True if you want to use "match" query instead of "term" filter
    This is only potentially useful when trying to tune certain search operations
    """
    for field in field_dictionary:
        if search_using_fields:
            queries.append({
                "match": {
                    field: field_dictionary[field]
                }
            })
        else:
            filters.append(_get_filter_field(field, field_dictionary[field]))


def process_filter_dictionary(filters, filter_dictionary):
    """
    We have a filter_dictionary - this means that if the field is included
    and matches, then we can include, OR if the field is undefined, then we
    assume it is safe to include
    """
    for field in filter_dictionary:
        filters.append({
            "or": [
                _get_filter_field(field, filter_dictionary[field]),
                {
                    "missing": {
                        "field": field
                    }
                }
            ]
        })


class ElasticSearchEngine(SearchEngine):

    """ ElasticSearch implementation of SearchEngine abstraction """

    _es = Elasticsearch()
    _known_mappings = {}

    def _get_mappings(self, doc_type):
        """
        Interfaces with the elasticsearch mappings for the index
        prevents multiple loading of the same mappings from ES when called more than once
        """
        known_mappings = ElasticSearchEngine._known_mappings
        if self.index_name not in known_mappings:
            known_mappings[self.index_name] = {}
        if doc_type not in known_mappings[self.index_name]:
            try:
                known_mappings[self.index_name][doc_type] = self._es.indices.get_mapping(
                    index=self.index_name,
                    doc_type=doc_type
                )[doc_type]
            except exceptions.NotFoundError:
                return {}

        return known_mappings[self.index_name][doc_type]

    def _clear_mapping(self, doc_type):
        """ Remove the cached mappings, so that they get loaded from ES next time they are requested """
        known_mappings = ElasticSearchEngine._known_mappings
        if self.index_name in known_mappings and doc_type in known_mappings[self.index_name]:
            del known_mappings[self.index_name][doc_type]

    def __init__(self, index=None):
        super(ElasticSearchEngine, self).__init__(index)
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
        current_mappings = self._get_mappings(doc_type)
        field_properties = getattr(settings, "ELASTIC_FIELD_MAPPINGS", {})
        properties = {}

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

        any_change = False
        for field in body:
            if field not in exclude_fields:
                if field not in current_mappings:
                    any_change = True
                    properties[field] = field_property(field, body[field])

        if any_change:
            self._es.indices.put_mapping(
                index=self.index_name,
                doc_type=doc_type,
                body={
                    doc_type: {
                        "properties": properties,
                    }
                }
            )
            self._clear_mapping(doc_type)

    def index(self, doc_type, body, **kwargs):
        """
        Implements call to add document to the ES index
        Note the call to _check_mappings which will setup fields with the desired mappings
        """
        if 'id' in body:
            kwargs['id'] = body['id']

        self._check_mappings(doc_type, body)

        self._es.index(
            index=self.index_name,
            doc_type=doc_type,
            body=body,
            **kwargs
        )

    def remove(self, doc_type, doc_id, **kwargs):
        """ Implements call to remove the document from the index """

        # let notfound not cause error
        kwargs.update({
            "ignore": [404]
        })

        self._es.delete(
            index=self.index_name,
            doc_type=doc_type,
            id=doc_id,
            **kwargs
        )

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None, **kwargs):
        """ Implements call to search the index for the desired content """

        search_using_fields = "search_fields" in kwargs and kwargs["search_fields"]
        if search_using_fields:
            del kwargs["search_fields"]

        queries = []
        filters = []

        # We have a query string, search all fields for matching text within the "content" node
        if query_string:
            queries.append({
                "query_string": {
                    "fields": ["content.*"],
                    "query": query_string
                }
            })

        if field_dictionary:
            _process_field_dictionary(filters, queries, field_dictionary, search_using_fields)

        if filter_dictionary:
            process_filter_dictionary(filters, filter_dictionary)

        query = {
            "match_all": {}
        }
        if len(queries) > 1:
            query = {
                "bool": {
                    "must": queries
                }
            }
        elif len(queries) > 0:
            query = queries[0]

        if filters:
            filter_dictionary = filters[0]
            if len(filters) > 1:
                filter_dictionary = {
                    "bool": {
                        "must": filters
                    }
                }
            query = {
                "filtered": {
                    "query": query,
                    "filter": filter_dictionary,
                }
            }

        if query:
            kwargs.update({
                "body": {"query": query}
            })

        es_response = self._es.search(
            index=self.index_name,
            **kwargs
        )

        return _translate_hits(es_response)

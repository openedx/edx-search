""" Elatic Search implementation for courseware search index """
import collections

from django.conf import settings
from elasticsearch import Elasticsearch

from search.manager import SearchEngine


class ElasticSearchEngine(SearchEngine):
    """ ElasticSearch implementation of SearchEngine abstraction """

    _es = Elasticsearch()
    _known_mappings = {}

    def _get_mappings(self, doc_type):
        known_mappings = ElasticSearchEngine._known_mappings
        if self.index_name not in known_mappings:
            known_mappings[self.index_name] = {}
        if doc_type not in known_mappings[self.index_name]:
            try:
                known_mappings[self.index_name][doc_type] = self._es.indices.get_mapping(
                        index=self.index_name,
                        doc_type=doc_type
                    )[doc_type]
            except:
                return {}

        return known_mappings[self.index_name][doc_type]

    def _clear_mapping(self, doc_type):
        known_mappings = ElasticSearchEngine._known_mappings
        if self.index_name in known_mappings and doc_type in known_mappings[self.index_name]:
            del known_mappings[self.index_name][doc_type]

    def __init__(self, index=None):
        super(ElasticSearchEngine, self).__init__(index)

    def _check_mappings(self, doc_type, body):
        # Make fields other than content be indexed as unanalyzed terms - content contains fields that are to be analyzed
        exclude_fields = ["content", "id", "course"]
        current_mappings = self._get_mappings(doc_type)
        field_properties = getattr(settings, "ELASTIC_FIELD_MAPPINGS", {})
        properties = {}

        def field_property(field_name, field_value):
            prop_val = None
            if field_name in field_properties:
                prop_val = field_properties[field_name]
            elif isinstance(field_value, dict):
                props = {fn:field_property(fn, field_value[fn]) for fn in field_value}
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
        self._es.delete(
            index=self.index_name,
            doc_type=doc_type,
            id=doc_id,
            **kwargs
        )

    def _translate_hits(self, es_response):
        response = {
            "took": es_response["took"],
            "total": es_response["hits"]["total"],
            "max_score": es_response["hits"]["max_score"],
        }

        def process_result(result):
            data = result.pop("_source")

            result.update({
                "data": data,
                "score": result["_score"]
            })

            return result

        results = [process_result(hit) for hit in es_response["hits"]["hits"]]
        response.update({"results": results})

        return response

    def search(self, query_string=None, field_dictionary=None, filter_dictionary=None, **kwargs):
        search_using_fields = "search_fields" in kwargs and kwargs["search_fields"]
        if search_using_fields:
            del kwargs["search_fields"]

        queries = []
        filters = []

        if query_string:
            queries.append({
                "query_string": {
                    "fields": ["content.*"],
                    "query": query_string
                }
            })

        def get_filter_field(field_name, field_value):
            filter_field = None
            if isinstance(field_value, list) and len(field_value) == 2:
                range_values = {}
                if field_value[0]:
                    range_values.update({"gte": field_value[0]})
                if field_value[1]:
                    range_values.update({"lte": field_value[1]})
                filter_field = {
                    "range": {
                        field: range_values
                    }
                }
            else:
                filter_field = {
                    "term": {
                        field: field_value
                    }
                }
            return filter_field

        if field_dictionary:
            for field in field_dictionary:
                if search_using_fields:
                    queries.append({
                            "match": {
                                field: field_dictionary[field]
                            }
                        })
                else:
                    filters.append(get_filter_field(field, field_dictionary[field]))

        if filter_dictionary:
            for field in filter_dictionary:
                filters.append({
                    "or": [
                        get_filter_field(field, filter_dictionary[field]),
                        {
                            "missing": {
                                "field": field
                            }
                        }
                    ]
                })

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

        return self._translate_hits(es_response)

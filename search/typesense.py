"""
This is a search engine for Typesense. It implements the edx-search's
SearchEngine API, such that it can be setup as a drop-in replacement for the
ElasticSearchEngine, for its two main use cases:
- The "Course Discovery" view (http://local.openedx.io:8000/courses on Tutor
  devstack)
- The Courseware content search in the Learning MFE
  (gated by the `courseware.mfe_courseware_search` waffle flag).

Other use cases are not supported.

To switch to this engine, you should run a Typesense instance and define the
following setting:

    SEARCH_ENGINE = "search.typesense.TypesenseEngine"

You will then need to create the new indices by running:

    ./manage.py lms shell -c "import search.typesense; search.typesense.create_indexes()"

^ This manual index creation is a temporary approach, while we're evaluating
Typesense. A proper long-term approach is needed for all our search engines, as
discussed at https://github.com/openedx/edx-platform/issues/36868 .

For more information about the Typesense API in Python, check
https://github.com/typesense/typesense-python
"""
import logging
import typing as t

from django.conf import settings
from django.utils import timezone
import typesense
from typesense.collection import Collection
from typesense.exceptions import ObjectNotFound, RequestMalformed, ServerError
from typesense.types.collection import RegularCollectionFieldSchema
from typesense.types.document import Hit, SearchResponse

from search.search_engine_base import SearchEngine
from search.utils import (
    convert_doc_datatypes,
    IS_NULL_SUFFIX,
    restore_doc_datatypes,
    UTC_OFFSET_SUFFIX,
    ValueRange,
)

# ====== Indices ======
# Due to the messy architecture of edx-search, we have to encode information about specific indexes in this engine.
# So some information about the indexes is here (in ES, Meilisearch, and Typesense engines separately) and other info
# is in edx-platform, e.g. edx-platform/cms/djangoapps/contentstore/courseware_index.py
#
# The two indexes we're most concerned about are:
#  - Course Info index: stores a list of courses that users can browse and then enroll into
#  - Course Content index: stores the course content (XBlocks) for enrolled users to search within one course
# We need to know which fields store timestamps in order to force them to be stored as 64 bit integers instead of 32
COURSE_INFO_INDEX = getattr(settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
COURSE_CONTENT_INDEX = getattr(settings, "COURSEWARE_CONTENT_INDEX_NAME", "courseware_content")
INDEX_CONFIGURATION = {
    COURSE_INFO_INDEX: {
        # Most fields will be auto-created but some (datetimes, facet fields) need to be specified up front:
        "field_overrides": [
            {"name": "start", "type": "datetime", "optional": True},
            {"name": "end", "type": "datetime", "optional": True},
            {"name": "enrollment_start", "type": "datetime", "optional": True},
            {"name": "enrollment_end", "type": "datetime", "optional": True},
            {"name": "org", "type": "string", "facet": True},
            {"name": "modes", "type": "string[]", "facet": True},
            {"name": "language", "type": "string", "facet": True},
        ],
        # Which fields to use for text matches. Required by Typesense.
        "query_fields": ["course", "org", "number", "content"],
    },
    COURSE_CONTENT_INDEX: {
        # Most fields will be auto-created but some (datetimes, facet fields) need to be specified up front:
        "field_overrides": [
            {"name": "start_date", "type": "datetime", "optional": True},
            # Enable stemming for the "content" field, so that e.g.
            # searching for "run" will match "running", "runs", "ran".
            # Unfortunately, this could break indexing if non-string fields get
            # included in "content", which is possible (XBlocks can return
            # whatever data they want from their `index_dictionary()` method).
            # So far, the core XBlocks seem to always return strings for
            # "content" sub-fields though.
            {"name": "content", "type": "object"},
            {"name": "content\\..*", "type": "string", "stem": True},
        ],
        # Which fields to use for text matches. Required by Typesense.
        "query_fields": ["content"],
    }
}

logger = logging.getLogger(__name__)


class TypesenseEngine(SearchEngine):
    """
    Typesense-compatible search engine. We work very hard to produce an output that is
    API compatible with edx-search's ElasticSearchEngine.
    """

    def __init__(self, index=None) -> None:
        super().__init__(index=index)
        self._typesense_index: Collection | None = None

    @property
    def typesense_index(self) -> Collection:
        """
        Lazy load Typesense index ("Collection").
        """
        if self._typesense_index is None:
            client = get_typesense_client()
            self._typesense_index = client.collections[self.typesense_index_name]
        return self._typesense_index

    @property
    def typesense_index_name(self):
        """
        The full index name, with prefix
        """
        return get_typesense_index_name(self.index_name)

    def index(self, sources: list[dict[str, t.Any]], **kwargs):
        """
        Index a number of documents, which can have just any type.
        """
        logger.info(
            "Index request: index=%s sources=%s kwargs=%s",
            self.typesense_index_name,
            sources,
            kwargs,
        )
        processed_documents = [self.process_document(source) for source in sources]
        result = self.typesense_index.documents.import_(processed_documents, {'action': 'upsert'})
        for idx, doc_result in enumerate(result):
            if doc_result.get("error"):
                logger.error(f"Failed to index document {sources[idx].get('id', idx)}: {doc_result['error']}")

    def process_document(self, doc: dict[str, t.Any]) -> dict[str, t.Any]:
        """
        Process document before indexing.
        """
        index_config = INDEX_CONFIGURATION[self.index_name]
        # Some fields like 'enrollment_start' are absent from the source documents
        # when they are NULL (have no value). But TypeSense doesn't allow filtering
        # by NULL values, so to support filtering we need to explicitly store something
        # else to represent NULLs. So here we set them to None, and convert_doc_datatypes()
        # will create the required separate __is_null field. See its docstring.
        doc_with_nulls = {**doc}
        for field_config in index_config.get("field_overrides", []):
            if field_config.get("optional", False):
                doc_with_nulls.setdefault(field_config["name"])
        # Convert field types recursively, e.g. datetime->int64, NULL -> separate field:
        processed = convert_doc_datatypes(doc_with_nulls, record_nulls=True)
        return processed

    def search(
        self,
        query_string="",
        field_dictionary=None,
        filter_dictionary=None,
        exclude_dictionary=None,
        aggregation_terms=None,
        sort_by=None,
        log_search_params=False,
        **kwargs,
    ):  # pylint: disable=too-many-arguments
        """
        Perform a search.
        See docs: https://typesense.org/docs/29.0/api/search.html#search-parameters
        """
        compiled_params = get_search_params(
            field_dictionary=field_dictionary,
            filter_dictionary=filter_dictionary,
            exclude_dictionary=exclude_dictionary,
            aggregation_terms=aggregation_terms,
            sort_by=sort_by,
            **kwargs,
        )
        if log_search_params:
            logger.info("Search query: compiled_params=%s", compiled_params)

        # TypeSense requires us to specify which text fields the 'q' query will search,
        # for ALL searches, even if we're not specifying a query text (i.e. even if we're
        # just using filters).
        index_config = INDEX_CONFIGURATION[self.index_name]
        query_by = index_config["query_fields"]
        search_args = {"q": query_string, "query_by": query_by, **compiled_params}
        try:
            results = self.typesense_index.documents.search(search_args)
        except RequestMalformed as err:
            if "Query string exceeds max allowed length" in str(err):
                # To do large queries (with complex filters), we need to use the multi-search endpoint:
                client = get_typesense_client()
                multi_response = client.multi_search.perform({"searches": [{
                    "collection": self.typesense_index_name,
                    **search_args,
                }]})
                results = multi_response["results"][0]
            else:
                raise err
        # The search endpoints can sometimes return an error dict {"code": 404, "error": "..."}
        # Even though it's not defined in the typing information for these functions.
        # It's unclear why the Typesense client is returning this instead of raising an exception.
        if "code" in results and results["code"] == 404:
            raise ObjectNotFound(results.get("error", "not found"))
        if "error" in results:
            # Some other unexpected error:
            raise ServerError(results["error"])
        processed_results = process_results(results, self.index_name)
        return processed_results

    def remove(self, doc_ids, **kwargs):
        """
        Removing documents from the index is as simple as deleting the the documents
        with the corresponding primary key.
        """
        logger.info(
            "Remove request: index=%s, doc_ids=%s kwargs=%s",
            self.typesense_index_name,
            doc_ids,
            kwargs,
        )
        if doc_ids:
            self.typesense_index.documents.delete({'filter_by': f'filter_by=id: [{",".join(doc_ids)}]'})


def create_indexes():
    """
    This is an initialization function that creates indexes and makes sure that they
    support the right data types, filtering, and faceting.
    """
    client = get_typesense_client()
    for index_name, index_config in INDEX_CONFIGURATION.items():
        field_overrides = index_config.get("field_overrides", [])
        fields_config: list[RegularCollectionFieldSchema] = [
            # Auto-create fields by default
            {"name": ".*", "type": "auto"},
        ]
        for field in field_overrides:
            field_type = field["type"]  # Throw error if type is not set here.
            is_datetime = field["type"] == "datetime"
            is_optional = field.get("optional", False)
            if is_datetime:
                field_type = "int64"
            fields_config.append({**field, "type": field_type})
            if is_datetime:
                fields_config.append({
                    "name": field["name"] + UTC_OFFSET_SUFFIX,
                    "type": "int32",
                    "optional": field.get("optional", False),
                })
            if is_optional:
                fields_config.append({
                    "name": field["name"] + IS_NULL_SUFFIX,
                    "type": "bool",
                    "optional": True,
                })
        client.collections.create({
            "name": get_typesense_index_name(index_name),
            "enable_nested_fields": True,
            "fields": fields_config,
        })


def delete_indexes():
    """
    Delete the Typesense indexes ("collections"), including all data.
    """
    client = get_typesense_client()
    # Collection that stores the list of courses
    for index_name in [COURSE_INFO_INDEX, COURSE_CONTENT_INDEX]:
        full_index_name = get_typesense_index_name(index_name)
        if full_index_name in client.collections:
            client.collections[full_index_name].delete()


def get_typesense_client() -> typesense.Client:
    """
    Return a Typesense client with the right settings.
    """
    if not hasattr(get_typesense_client, "client_singleton"):
        get_typesense_client.client_singleton = typesense.Client({
            'nodes': settings.TYPESENSE_URLS,
            'api_key': settings.TYPESENSE_API_KEY,
            # Per note in the python example at
            # https://typesense.org/docs/29.0/api/documents.html#index-multiple-documents ,
            # this timeout needs to be at least five minutes.
            'connection_timeout_seconds': 5 * 60,
        })
    return get_typesense_client.client_singleton


def get_typesense_index_name(index_name: str) -> str:
    """
    Return the index name in Typesense associated to a hard-coded index name.

    This is useful for multi-tenant Typesense: just define a different prefix for
    every tenant.
    """
    return settings.TYPESENSE_COLLECTION_PREFIX + index_name


def _escape_str(value: str):
    """ Escape a string for use in a Typesense filter """
    # https://typesense.org/docs/guide/tips-for-filtering.html#escaping-special-characters
    return f'`{value.replace("`", "")}`'


def get_search_params(
    field_dictionary=None,
    filter_dictionary=None,
    exclude_dictionary=None,
    aggregation_terms=None,
    sort_by=None,
    **kwargs,
) -> dict[str, t.Any]:
    """
    Return a dictionary of parameters that should be passed to the Typesense
    client `.search()` method. Converts from Elasticsearch-compatible parameters
    to Typesense ones.
    """
    params: dict[str, t.Any] = {}

    # Aggregation
    if aggregation_terms:
        params["facet_by"] = list(aggregation_terms.keys())

    # Sorting
    if sort_by:
        raise NotImplementedError("Sorting not yet implemented for Typesense")

    # Exclusion and inclusion filters
    filters = []
    if field_dictionary:
        filters += get_filter_rules(field_dictionary)
    if filter_dictionary:
        filters += get_filter_rules(filter_dictionary, optional=True)
    for key, values in exclude_dictionary.items():
        # Ignore multiple values for this field, e.g. {"id": ["ignorethis1", "ignorethis2"]}
        filters += [f"{key}:!=[{', '.join(_escape_str(value) for value in values)}]"]
    if filters:
        params["filter_by"] = " && ".join(filters)

    # Offset/Size
    if "from_" in kwargs:
        params["offset"] = kwargs["from_"]
    if "size" in kwargs:
        params["limit"] = kwargs["size"]

    return params


def get_filter_rules(
    rule_dict: dict[str, t.Any], optional: bool = False
) -> list[str | list[str]]:
    """
    Convert inclusion/exclusion rules.
    """
    rules = []
    for key, value in rule_dict.items():
        if isinstance(value, list):
            key_rules = [
                get_filter_rule(key, v, optional=optional)
                for v in value
            ]
            rules.append(key_rules)
        else:
            rules.append(
                get_filter_rule(key, value, optional=optional)
            )
    return rules


def get_filter_rule(key: str, value: str, optional: bool = False) -> str:
    """
    Create a Typesense filter rule.

    See: https://typesense.org/docs/29.0/api/search.html#filter-parameters
    """
    if isinstance(value, str):
        rule = f'{key}:={_escape_str(value)}'
    elif isinstance(value, ValueRange):
        lower = value.lower
        if isinstance(lower, timezone.datetime):
            lower = round(lower.timestamp())
        upper = value.upper
        if isinstance(upper, timezone.datetime):
            upper = round(upper.timestamp())
        # I know that the following fails if value == 0, but we are being
        # consistent with the behaviour in the elasticsearch engine.
        if not isinstance(lower, (int, type(None))) or not isinstance(upper, (int, type(None))):
            raise ValueError("Upper/lower bounds of ValueRange must be integers if specified. Floats not implemented.")
        if upper and lower:
            # Note: this range is inclusive, i.e. equivalent to "lower <= value <= upper"
            rule = f"{key}:[{lower}..{upper}]"
        elif lower:
            rule = f"{key}:>={lower}"
        elif upper:
            rule = f"{key}:<={upper}"
        else:
            raise ValueError("Either upper or lower is required for range search")
    else:
        raise ValueError(f"Unknown value type: {value.__class__}")
    if optional:
        # https://typesense.org/docs/guide/tips-for-searching-common-types-of-data.html#searching-for-null-or-empty-values
        rule = f"({rule} || {key}{IS_NULL_SUFFIX}:true)"
    return rule


def process_results(results: SearchResponse, index_name: str) -> dict[str, t.Any]:
    """
    Convert results produced by Typesense into results that are compatible with the
    edx-search engine API.

    Example input:

        {
            'hits': [
                {
                    "document": {
                        "id": "course-v1:...",
                        "course_name": "Calculus 300",
                        ...
                    },
                    "highlights": [
                        {
                            "field": "course_name",
                            "snippet": "<mark>Calculus</mark> 300",
                            "matched_tokens": ["Calculus"]
                        }
                    ],
                    "text_match": 130916
                },
                ...
            ],
            'facet_counts': [],
            'found': 5,
            'out_of': 121,
            'page': 1,
            'request_params': {
                'collection_name': 'tutor_courseware_content',
                'per_page': 10,
                'q': 'calculus'
            },
            'search_cutoff': False,
            'search_time_ms': 0
        }

    Example output:

        {
                'took': 13,
                'total': 1,
                'max_score': 0.4001565,
                'results': [
                    {
                        '_index': 'course_info',
                        '_type': '_doc',
                        '_id': 'course-v1:OpenedX+DemoX+DemoCourse',
                        '_ignored': ['content.overview.keyword'], # removed
                        'data': {
                            'id': 'course-v1:OpenedX+DemoX+DemoCourse',
                            'course': 'course-v1:OpenedX+DemoX+DemoCourse',
                            'content': {
                                'display_name': 'Open edX Demo Course',
                                ...
                            },
                            'image_url': '/asset-v1:OpenedX+DemoX+DemoCourse+type@asset+block@thumbnail_demox.jpeg',
                            'start': '2020-01-01T00:00:00+00:00',
                            ...
                        },
                        'score': 0.4001565
                    }
                ],
                'aggs': {
                    'modes': {
                        'terms': {'audit': 1},
                        'total': 1.0,
                        'other': 0
                    },
                    'org': {
                        'terms': {'OpenedX': 1}, 'total': 1.0, 'other': 0
                    },
                    'language': {'terms': {'en': 1}, 'total': 1.0, 'other': 0}
                }
            }
    """
    # Base
    processed = {
        "took": results["search_time_ms"],
        "total": results["found"],
        "results": [],
        "aggs": {},
    }

    # Hits
    max_score = 100
    for hit in results["hits"]:
        result = process_hit(hit, index_name)
        # TODO: do we need the scores and max_score? For now just hard-coding max to 100.
        # The TypeSense scores are so large that they cannot be represented as regular
        # numbers in JavaScript, causing client-side errors.
        # Ref: https://github.com/typesense/typesense/issues/667
        # max_score = max(max_score, hit["text_match"])
        processed["results"].append(result)
    processed["max_score"] = max_score

    for facet_data in results.get("facet_counts", []):
        total = facet_data["stats"]["total_values"]
        field_name = facet_data["field_name"]
        facet_distribution = {}
        for entry in facet_data["counts"]:
            facet_distribution[entry["value"]] = entry["count"]
            # e.g. facet_distribution["red"] = 15 if we have a color facet distribution {red: 15, yellow: 10, blue: 5}
        processed["aggs"][field_name] = {
            "terms": facet_distribution,
            "total": total,
            "other": 0,
        }
    return processed


def process_hit(hit: Hit, index_name: str) -> dict[str, t.Any]:
    """
    Convert a search result back to the Elasticsearch format.
    """
    return {
        "_id": hit["document"]["id"],
        "_index": index_name,
        "_type": "_doc",
        "data": restore_doc_datatypes(hit["document"]),
    }

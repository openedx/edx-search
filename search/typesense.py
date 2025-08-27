"""
This is a search engine for Typesense. It implements the edx-search's SearchEngine
API, such that it can be setup as a drop-in replacement for the ElasticSearchEngine. To
switch to this engine, you should run a Typesense instance and define the following
setting:

    SEARCH_ENGINE = "search.typesense.TypesenseEngine"

You will then need to create the new indices by running:

    ./manage.py lms shell -c "import search.typesense; search.typesense.create_indexes()"

For more information about the Typesense API in Python, check
https://github.com/typesense/typesense-python

(TODO: potentially copy other docs that were here in meilisearch.py, if they are
 also relevant to Typesense)
"""
import logging
import typing as t

from django.conf import settings
from django.utils import timezone
import typesense
from typesense.collection import Collection
from typesense.exceptions import ObjectNotFound, RequestMalformed, ServerError
from typesense.types.document import Hit, SearchResponse

from search.search_engine_base import SearchEngine
from search.utils import convert_doc_datatypes, UTC_OFFSET_SUFFIX, ValueRange, restore_doc_datatypes

TYPESENSE_API_KEY = getattr(settings, "TYPESENSE_API_KEY", "")
TYPESENSE_URLS = getattr(settings, "TYPESENSE_URLS", [getattr(settings, "TYPESENSE_URL", "http://typesense")])
TYPESENSE_COLLECTION_PREFIX = getattr(settings, "TYPESENSE_COLLECTION_PREFIX", "")

# ====== Indices ======
# Due to the messy architecture of edx-search, we have to encode information about specific indexes in this engine.
# So some information about the indexes is here (in ES, Meilisearch, and Typesense engines separately) and other info
# is in edx-platform, e.g. edx-platform/cms/djangoapps/contentstore/courseware_index.py
#
# The two indexes we're most concerned about are:
#  - Course Info index: stores a list of courses that users can browse and then enroll into
#  - Course Content index: stores the course content (XBlocks) for enrolled users to search within one course
COURSE_INFO_INDEX = getattr(settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
# We need to know which fields store timestamps in order to force them to be stored as 64 bit integers instead of 32
COURSE_INFO_TIMESTAMP_FIELDS = ["start", "end", "enrollment_start", "enrollment_end"]
COURSE_CONTENT_INDEX = getattr(settings, "COURSEWARE_CONTENT_INDEX_NAME", "courseware_content")
# In Typesense, we explicitly list fields for which we expect to use faceting.
# This is different than Elasticsearch where we can aggregate results over any field.
# Reference: https://typesense.org/docs/29.0/api/collections.html#create-a-collection
# TODO: do we use faceting for any fields?

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
        processed_documents = [process_document(source) for source in sources]
        self.typesense_index.documents.import_(processed_documents, {'action': 'create'})
        # TODO ^ Should that be upsert instead of create?

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
            **kwargs,
        )
        if log_search_params:
            logger.info("Search query: compiled_params=%s", compiled_params)

        # TypeSense requires us to specify which text fields the 'q' query will search,
        # for ALL searches, even if we're not specifying a query text (i.e. even if we're
        # just using filters). Load them from the index:
        # TODO: specify a smaller, more specific, prioritized list? This shouldn't include e.g. image_url, org_image_url
        # TODO: cache this? It won't change during the lifetime of our process, and retrieving it probably slows us down
        query_by = [field["name"] for field in self.typesense_index.retrieve()["fields"] if field["type"] == "string"]

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
    # Collection that stores the list of courses
    client.collections.create({
        "name": get_typesense_index_name(COURSE_INFO_INDEX),
        "fields": [
            # Auto-create fields by default
            {"name": ".*", "type": "auto"},
            # Override specific fields as needed:
            # timestamps and their suffixes should be integers, not float
            *[
                {"name": field_name, "type": "int64"} for field_name in COURSE_INFO_TIMESTAMP_FIELDS
            ],
            *[
                {"name": field_name + UTC_OFFSET_SUFFIX, "type": "int32"} for field_name in COURSE_INFO_TIMESTAMP_FIELDS
            ],
        ],
    })
    # Collection that stores content within each course:
    client.collections.create({
        "name": get_typesense_index_name(COURSE_CONTENT_INDEX),
        "fields": [
            # Auto-create fields by default
            {"name": ".*", "type": "auto"},
            # Override specific fields as needed:
            {"name": "start_date", "type": "int64"},
            {"name": "start_date" + UTC_OFFSET_SUFFIX, "type": "int32"},
        ],
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
            'nodes': TYPESENSE_URLS,
            'api_key': TYPESENSE_API_KEY,
            'connection_timeout_seconds': 4,
        })
    return get_typesense_client.client_singleton


def get_typesense_index_name(index_name: str) -> str:
    """
    Return the index name in Typesense associated to a hard-coded index name.

    This is useful for multi-tenant Typesense: just define a different prefix for
    every tenant.
    """
    return TYPESENSE_COLLECTION_PREFIX + index_name


def process_document(doc: dict[str, t.Any]) -> dict[str, t.Any]:
    """
    Process document before indexing.

    We make a copy to avoid modifying the source document.
    """
    processed = convert_doc_datatypes(doc)

    return processed


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
        params["facets"] = list(aggregation_terms.keys())

    # Sorting
    if sort_by:
        raise NotImplementedError("Sorting not yet implemented for Typesense")

    # Exclusion and inclusion filters
    filters = []
    if field_dictionary:
        filters += get_filter_rules(field_dictionary)
    if filter_dictionary:
        filters += get_filter_rules(filter_dictionary, optional=True)
    if exclude_dictionary:
        filters += get_filter_rules(exclude_dictionary, exclude=True)
    if filters:
        params["filter_by"] = " && ".join(filters)

    # Offset/Size
    if "from_" in kwargs:
        params["offset"] = kwargs["from_"]
    if "size" in kwargs:
        params["limit"] = kwargs["size"]

    return params


def get_filter_rules(
    rule_dict: dict[str, t.Any], exclude: bool = False, optional: bool = False
) -> list[str | list[str]]:
    """
    Convert inclusion/exclusion rules.
    """
    rules = []
    for key, value in rule_dict.items():
        if isinstance(value, list):
            key_rules = [
                get_filter_rule(key, v, exclude=exclude, optional=optional)
                for v in value
            ]
            if exclude:
                rules.extend(key_rules)
            else:
                rules.append(key_rules)
        else:
            rules.append(
                get_filter_rule(key, value, exclude=exclude, optional=optional)
            )
    return rules


def get_filter_rule(
    key: str, value: str, exclude: bool = False, optional: bool = False
) -> str:
    """
    Create a Typesense filter rule.

    See: https://typesense.org/docs/29.0/api/search.html#filter-parameters
    """
    if isinstance(value, str):
        # https://typesense.org/docs/guide/tips-for-filtering.html#escaping-special-characters
        value_escaped = f'`{value.replace("`", "")}`'
        if exclude:
            rule = f'{key}:!={value_escaped}'
        else:
            rule = f'{key}:={value_escaped}'
    elif isinstance(value, ValueRange):
        lower = value.lower
        if isinstance(lower, timezone.datetime):
            lower = lower.timestamp()
        upper = value.upper
        if isinstance(upper, timezone.datetime):
            upper = upper.timestamp()
        # I know that the following fails if value == 0, but we are being
        # consistent with the behaviour in the elasticsearch engine.
        if upper and lower:
            # TODO: verify if this is inclusive or exclusive of outer bounds
            rule = f"{key}:[{lower}..{upper}]"
        elif lower:
            rule = f"{key}:>={lower}"
        elif upper:
            rule = f"{key}:<={upper}"
        else:
            raise ValueError("Either upper or lower is required for range search")
        if exclude:
            raise NotImplementedError("Excluding by value range not implemented yet for Typesense.")
    else:
        raise ValueError(f"Unknown value type: {value.__class__}")
    if optional:
        # rule += f" || {key} NOT EXISTS"
        raise NotImplementedError("Optional fields not supported for Typesense")
        # TODO: put a sentinel value instead of NULL ?
        # See https://typesense.org/docs/guide/tips-for-filtering.html#filtering-for-empty-fields
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
    max_score = 0
    for hit in results["hits"]:
        result = process_hit(hit, index_name)
        # TODO: we are discarding the actual score here and just keeping the max. Is that correct?
        max_score = max(max_score, hit["text_match"])
        processed["results"].append(result)
    processed["max_score"] = max_score

    # Aggregates/Facets - TODO: update this for Typesense
    for facet_name, facet_distribution in results.get("facetDistribution", {}).items():
        total = sum(facet_distribution.values())
        processed["aggs"][facet_name] = {
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

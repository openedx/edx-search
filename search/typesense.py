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

from copy import deepcopy
import logging
import typing as t

from django.conf import settings
from django.utils import timezone
import typesense
from typesense.collection import Collection

from search.meilisearch import convert_doc_datatypes, UTC_OFFSET_SUFFIX
from search.search_engine_base import SearchEngine
from search.utils import ValueRange


TYPESENSE_API_KEY = getattr(settings, "TYPESENSE_API_KEY", "")
TYPESENSE_URLS = getattr(settings, "TYPESENSE_URLS", [getattr(settings, "TYPESENSE_URL", "http://typesense")])
TYPESENSE_COLLECTION_PREFIX = getattr(settings, "TYPESENSE_COLLECTION_PREFIX", "")

# Indices:
COURSE_INFO_INDEX = getattr(settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
COURSE_INFO_TIMESTAMP_FIELDS = ["start", "end", "enrollment_start", "enrollment_end"]
COURSE_CONTENT_INDEX = getattr(settings, "COURSEWARE_CONTENT_INDEX_NAME", "courseware_content")


logger = logging.getLogger(__name__)


# In Typesense, we explicitly list fields for which we expect to use faceting.
# This is different than Elasticsearch where we can aggregate results over any field.
# Reference: https://typesense.org/docs/29.0/api/collections.html#create-a-collection
# TODO: do we use faceting for any fields?


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
        query_string=None,
        field_dictionary=None,
        filter_dictionary=None,
        exclude_dictionary=None,
        aggregation_terms=None,
        # exclude_ids=None, # deprecated
        # use_field_match=False, # deprecated
        log_search_params=False,
        **kwargs,
    ):  # pylint: disable=too-many-arguments
        """
        See meilisearch docs: https://typesense.org/docs/29.0/api/search.html#search-parameters
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
        results = self.typesense_index.documents.search({"q": query_string, **compiled_params})
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
    support the right facetting.
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
    This is an initialization function that creates indexes and makes sure that they
    support the right facetting.
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
    return typesense.Client({
        'nodes': TYPESENSE_URLS,
        'api_key': TYPESENSE_API_KEY,
        'connection_timeout_seconds': 4,
    })


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
    **kwargs,
) -> dict[str, t.Any]:
    """
    Return a dictionary of parameters that should be passed to the Typesense
    client `.search()` method.
    """
    params: dict[str, t.Any] = {}

    # Aggregation
    if aggregation_terms:
        params["facets"] = list(aggregation_terms.keys())

    # Exclusion and inclusion filters
    filters = []
    if field_dictionary:
        filters += get_filter_rules(field_dictionary)
    if filter_dictionary:
        filters += get_filter_rules(filter_dictionary, optional=True)
    if exclude_dictionary:
        filters += get_filter_rules(exclude_dictionary, exclude=True)
    if filters:
        params["filter_by"] = filters

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
    Meilisearch filter rule.

    See: https://www.meilisearch.com/docs/learn/filtering_and_sorting/filter_expression_reference
    """
    prefix = "NOT " if exclude else ""
    if isinstance(value, str):
        rule = f'{prefix}{key} = "{value}"'
    elif isinstance(value, ValueRange):
        constraints = []
        lower = value.lower
        if isinstance(lower, timezone.datetime):
            lower = lower.timestamp()
        upper = value.upper
        if isinstance(upper, timezone.datetime):
            upper = upper.timestamp()
        # I know that the following fails if value == 0, but we are being
        # consistent with the behaviour in the elasticsearch engine.
        if lower:
            constraints.append(f"{key} >= {lower}")
        if upper:
            constraints.append(f"{key} <= {upper}")
        rule = " AND ".join(constraints)
        if len(constraints) > 1:
            rule = f"({rule})"
    else:
        raise ValueError(f"Unknown value type: {value.__class__}")
    if optional:
        rule += f" OR {key} NOT EXISTS"
    return rule


def process_results(results: dict[str, t.Any], index_name: str) -> dict[str, t.Any]:
    """
    Convert results produced by Typesense into results that are compatible with the
    edx-search engine API.

    Example input:

        {
            'hits': [
                {
                    'pk': 'f381d4f1914235c9532576c0861d09b484ade634',
                    'id': 'course-v1:OpenedX+DemoX+DemoCourse',
                    ...
                    "_rankingScore": 0.865,
                },
                ...
            ],
            'query': 'demo',
            'processingTimeMs': 0,
            'limit': 20,
            'offset': 0,
            'estimatedTotalHits': 1
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
        "took": results["processingTimeMs"],
        "total": results["estimatedTotalHits"],
        "results": [],
        "aggs": {},
    }

    # Hits
    max_score = 0
    for result in results["hits"]:
        result = process_hit(result)
        score = result.pop("_rankingScore")
        max_score = max(max_score, score)
        processed_result = {
            "_id": result["id"],
            "_index": index_name,
            "_type": "_doc",
            "data": result,
        }
        processed["results"].append(processed_result)
    processed["max_score"] = max_score

    # Aggregates/Facets
    for facet_name, facet_distribution in results.get("facetDistribution", {}).items():
        total = sum(facet_distribution.values())
        processed["aggs"][facet_name] = {
            "terms": facet_distribution,
            "total": total,
            "other": 0,
        }
    return processed


def process_hit(hit: dict[str, t.Any]) -> dict[str, t.Any]:
    """
    Convert a search result back to the ES format.
    """
    processed = deepcopy(hit)

    # Convert datetime fields back to datetime
    for key in list(processed.keys()):
        if key.endswith(UTC_OFFSET_SUFFIX):
            utcoffset = processed.pop(key)
            key = key[: -len(UTC_OFFSET_SUFFIX)]
            timestamp = hit[key]
            tz = (
                timezone.get_fixed_timezone(timezone.timedelta(seconds=utcoffset))
                if utcoffset
                else None
            )
            processed[key] = timezone.datetime.fromtimestamp(timestamp, tz=tz)
    return processed

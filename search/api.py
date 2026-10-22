""" search business logic implementations """
import logging
from datetime import datetime
import dateutil.parser
import six
from django.conf import settings
from collections import defaultdict

from .filter_generator import SearchFilterGenerator as CourseSearchFilterGenerator
from .program_filter_generator import SearchFilterGenerator as ProgramSearchFilterGenerator
from .search_engine_base import SearchEngine
from .result_processor import SearchResultProcessor
from .utils import DateRange

log = logging.getLogger(__name__)  # pylint: disable=invalid-name

# Default filters that we support, override using COURSE_DISCOVERY_FILTERS setting if desired
DEFAULT_FILTER_FIELDS = ['org', 'modes', 'languages']

# Default filters that we support, override using PROGRAM_DISCOVERY_FILTERS setting if desired
DEFAULT_PROGRAM_FILTER_FIELDS = ['languages']

#from xmodule.course_module import CATALOG_VISIBILITY_CATALOG_AND_ABOUT
CATALOG_VISIBILITY_CATALOG_AND_ABOUT = "both"


def course_discovery_filter_fields():
    """ look up the desired list of course discovery filter fields """
    return getattr(settings, "COURSE_DISCOVERY_FILTERS", DEFAULT_FILTER_FIELDS)


def course_discovery_facets():
    """ Discovery facets to include, by default we specify each filter field with unspecified size attribute """
    return getattr(settings, "COURSE_DISCOVERY_FACETS", {field: {'size': 100} for field in course_discovery_filter_fields()})


def program_discovery_filter_fields():
    """ look up the desired list of program discovery filter fields """
    return getattr(
        settings,
        "PROGRAM_DISCOVERY_FILTERS",
        DEFAULT_PROGRAM_FILTER_FIELDS
    )


def program_discovery_facets():
    """ Discovery facets to include, by default we specify each filter field with unspecified size attribute """
    return getattr(
        settings,
        "PROGRAM_DISCOVERY_FACETS",
        {
            field: {'size': 100}
            for field in program_discovery_filter_fields()
        }
    )


def mixed_discovery_facets():
    """
    Facet config for cross-index course+program discovery.

    If ``settings.MIXED_DISCOVERY_FACETS`` is set, it is used as the full facet
    map. Otherwise the union of ``course_discovery_facets`` and
    ``program_discovery_facets`` (program keys override on name collision).
    """
    custom = getattr(settings, "MIXED_DISCOVERY_FACETS", None)
    if custom is not None:
        return custom
    merged = dict(course_discovery_facets())
    merged.update(program_discovery_facets())
    return merged


def _facet_terms_for_index_scope(scopes):
    """
    Facet map for a requested list of index kinds (``course``, ``program``).

    When only course+program are selected, ``mixed_discovery_facets()`` is used so
    ``MIXED_DISCOVERY_FACETS`` overrides still apply; otherwise facets are merged
    from the selected indices.
    """
    if set(scopes) == {'course', 'program'}:
        return mixed_discovery_facets()
    merged = {}
    if 'course' in scopes:
        merged.update(course_discovery_facets())
    if 'program' in scopes:
        merged.update(program_discovery_facets())
    return merged


def _normalize_index_scope(index_scope):
    """
    Normalize to an ordered list of index kinds. ``None`` means course+program
    (legacy mixed discovery default). Accepts a comma-separated string or a list.
    """
    default = ['course', 'program']
    if index_scope is None:
        return list(default)
    if isinstance(index_scope, (list, tuple)):
        parts = [
            six.text_type(x).strip().lower()
            for x in index_scope
            if six.text_type(x).strip()
        ]
    else:
        parts = [
            p.strip().lower()
            for p in six.text_type(index_scope).split(',')
            if p.strip()
        ]
    valid = {'course', 'program'}
    out = []
    for p in parts:
        if p not in valid:
            raise ValueError(
                'Invalid index_scope token %r; expected one of: course, program.' % (p,)
            )
        if p not in out:
            out.append(p)
    if not out:
        return list(default)
    return out


class NoSearchEngineError(Exception):
    """ NoSearchEngineError exception to be thrown if no search engine is specified """
    pass


class QueryParseError(Exception):
    """QueryParseError will be thrown if the query is malformed.

    If a query has mismatched quotes (e.g. '"some phrase', return a
    more specific exception so the view can provide a more helpful
    error message to the user.

    """
    pass


def perform_search(
        search_term,
        user=None,
        size=10,
        from_=0,
        course_id=None,
        only_released_courses=True):
    """ Call the search engine with the appropriate parameters """
    # field_, filter_ and exclude_dictionary(s) can be overridden by calling application
    # field_dictionary includes course if course_id provided
    (field_dictionary, filter_dictionary, exclude_dictionary) = CourseSearchFilterGenerator.generate_field_filters(
        user=user,
        course_id=course_id
    )

    searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    search_terms = [] if search_term in (None, '') else [search_term]
    filter_dictionary = {key: _format_filter(value) for key, value in filter_dictionary.items()}

    if only_released_courses:
        filter_dictionary["course_status"] = _format_filter("released")

    results = searcher.search_string(
        search_terms,
        field_dictionary=field_dictionary,
        filter_dictionary=filter_dictionary,
        exclude_dictionary=exclude_dictionary,
        size=size,
        from_=from_,
        doc_type="courseware_content"
    )

    # post-process the result
    for result in results["results"]:
        result["data"] = SearchResultProcessor.process_result(result["data"], search_term, user)

    results["access_denied_count"] = len([r for r in results["results"] if r["data"] is None])
    results["results"] = [r for r in results["results"] if r["data"] is not None]

    return results


def _format_filter(filter, missing_included=True):
    """This is used to apply filters missing or existing search according to specific value.
    """
    return {'value': filter, 'missing_included': missing_included}


def process_range_data(results):
    """Mainly used for processing range datetime data, including `start` property and combined `status` property(`start` and `end`).
    """
    # For LMS usage
    if "start" in course_discovery_filter_fields():
        now = datetime.utcnow()
        start_terms = results.get('facets', {}).get('start', {}).get('terms', {})
        if start_terms:
            new_start_terms = defaultdict(int)
            # Initial new_start_terms = {'current': 0, 'future': 0}
            new_start_terms['current']
            new_start_terms['future']

            for key, value in start_terms.items():
                if not isinstance(key, (str, unicode, bytes, bytearray)):
                    continue
                key = dateutil.parser.parse(key, ignoretz=True)
                
                new_key = 'current'
                if key > now:
                    new_key = 'future'

                new_start_terms[new_key] += value

            results['facets']['start']['terms'] = new_start_terms
            results['facets']['start']['total'] = sum(new_start_terms.values())

    # For Studio usage
    elif "status" in course_discovery_filter_fields():
        status_terms = defaultdict(int)
        for course in results.get('results', []):
            start_term = course.get('data', {}).get('start', None)
            end_term = course.get('data', {}).get('end', None)
            now = datetime.utcnow()
            # start property always has value(not None)
            if not isinstance(start_term, (str, unicode, bytes, bytearray)):
                continue
            if start_term and dateutil.parser.parse(start_term, ignoretz=True) <= now:
                if not isinstance(end_term, (str, unicode, bytes, bytearray)):
                    continue
                if end_term and dateutil.parser.parse(end_term, ignoretz=True) <= now:
                    status_terms['past'] += 1
                else:
                    status_terms['current'] += 1
            else:
                status_terms['future'] += 1
                
        results['facets']['status']['terms'] = status_terms
        results['facets']['status']['total'] = sum(status_terms.values())

    return results


def course_discovery_search(search_terms=None, size=20, from_=0, field_dictionary=None, only_released_courses=True, **kwargs):
    """
    Course Discovery activities against the search engine index of course details
    """
    # We'll ignore the course-enrollemnt informaiton in field and filter
    # dictionary, and use our own logic upon enrollment dates for these
    sort_args = kwargs.get('sort_type') or 'default'
    sort_args = sort_args.lower()

    use_search_fields = ["org"]
    # Apply course filter by specified `user role`:
    # Developer / Platform Super Admin / Platform Admin / Course Admin / Course Staff...
    if kwargs.get('include_course_filter', False) and 'user' in kwargs:
        use_search_fields.append("course")
    (search_fields, _, exclude_dictionary) = CourseSearchFilterGenerator.generate_field_filters(**kwargs)
    use_field_dictionary = {}
    use_field_dictionary.update({field: search_fields[field] for field in search_fields if field in use_search_fields})
    if field_dictionary:
        use_field_dictionary.update(field_dictionary)
    if not getattr(settings, "SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING", False):
        use_field_dictionary["enrollment_start"] = DateRange(None, datetime.utcnow())

    searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    filter_dictionary = {}
    if kwargs.get('allow_enrollment_end_filter', False):
        filter_dictionary.update({
            "enrollment_end": _format_filter(DateRange(datetime.utcnow(), None))
        })
    start = use_field_dictionary.pop('start', None)
    if start == 'current':
        if sort_args == '+display_name':
            sort_args = [{'raw_display_name': {'order': 'asc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '-display_name':
            sort_args = [{'raw_display_name': {'order': 'desc'}}, {'start': {'order': 'desc'}}]
        else:
            sort_args = [
                {'new_course_flag': {'order': 'desc'}},
                {'new_flag_expired_date': {'order': 'desc', 'ignore_unmapped': True}},
                {'start': {'order': 'desc'}},
                {'raw_display_name': {'order': 'asc'}}
            ]
        filter_dictionary.update({
            'start':
            _format_filter(
                DateRange(None, datetime.utcnow()))
        })
    elif start == 'future':
        if sort_args == '+display_name':
            sort_args = [{'raw_display_name': {'order': 'asc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '-display_name':
            sort_args = [{'raw_display_name': {'order': 'desc'}}, {'start': {'order': 'desc'}}]
        else:
            sort_args = [
                {'new_course_flag': {'order': 'desc'}},
                {'new_flag_expired_date': {'order': 'desc', 'ignore_unmapped': True}},
                {'start': {'order': 'asc'}},
                {'raw_display_name': {'order': 'asc'}}
            ]
        filter_dictionary.update({
            'start':
            _format_filter(
                DateRange(datetime.utcnow(), None))
        })
    else:
        if sort_args == '+display_name':
            sort_args = [{'raw_display_name': {'order': 'asc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '-display_name':
            sort_args = [{'raw_display_name': {'order': 'desc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '+course':
            sort_args = [{'course': {'order': 'asc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '-course':
            sort_args = [{'course': {'order': 'desc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '+created':
            sort_args = [{'created': {'order': 'asc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '-created':
            sort_args = [{'created': {'order': 'desc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '+modified':
            sort_args = [{'modified': {'order': 'asc'}}, {'start': {'order': 'desc'}}]
        elif sort_args == '-modified':
            sort_args = [{'modified': {'order': 'desc'}}, {'start': {'order': 'desc'}}]
        else:
            sort_args = [
                {'new_course_flag': {'order': 'desc'}},
                {'new_flag_expired_date': {'order': 'desc', 'ignore_unmapped': True}},
                {'start': {'order': 'desc'}},
                {'raw_display_name': {'order': 'asc'}}
            ]

    status = use_field_dictionary.pop('status', None)
    if status == 'past':
        filter_dictionary.update({
            'end':
            _format_filter(DateRange(None, datetime.utcnow()),
                           missing_included=False)
        })
    elif status == 'current':
        filter_dictionary.update({
            'start':
            _format_filter(
                DateRange(None, datetime.utcnow())),
            'end':
            _format_filter(DateRange(datetime.utcnow(), None))
        })
    elif status == 'future':
        filter_dictionary.update({
            'start':
            _format_filter(
                DateRange(datetime.utcnow(), None))
        })

    if getattr(settings, 'ALLOW_CATALOG_VISIBILITY_FILTER', False):
        use_field_dictionary['catalog_visibility'] = CATALOG_VISIBILITY_CATALOG_AND_ABOUT

    exclude = search_fields.get('exclude', None)
    if 'archived' == exclude:
        filter_dictionary.update(
            {
                'end': _format_filter(DateRange(datetime.utcnow(), None))
            }
        )
    if only_released_courses:
        filter_dictionary["course_status"] = _format_filter("released")

    results = searcher.search(
        query_strings=search_terms,
        doc_type="course_info",
        size=size,
        from_=from_,
        # only show when enrollment start IS provided and is before now
        field_dictionary=use_field_dictionary,
        # show if no enrollment end is provided and has not yet been reached
        filter_dictionary=filter_dictionary,
        exclude_dictionary=exclude_dictionary,
        facet_terms=course_discovery_facets(),
        sort=sort_args,
        ga_total=kwargs.pop('ga_total', False),
        targeting_gate=CourseSearchFilterGenerator.generate_targeting_gate(**kwargs),
    )

    return process_range_data(results)


def _mixed_sort_for_cross_index(sort_type):
    """
    Unified sort list for cross-index search. Uses ``missing`` so documents
    without a field (course vs program) sort last on that key.

    ``ignore_unmapped`` and ``unmapped_type`` (ES 1.x string) avoid failures when
    a field is absent on one index type.
    """
    def _raw_name_sort(order):
        """Sort clause for ``raw_display_name`` (course); unmapped program docs are skipped."""
        return {
            'order': order, 'missing': '_last',
            'ignore_unmapped': True, 'unmapped_type': 'string'
        }

    sort_type = (sort_type or 'default').lower()
    if sort_type == '+display_name':
        return [
            {'start': {'order': 'desc'}},
            {'raw_display_name': _raw_name_sort('asc')},
        ]
    if sort_type == '-display_name':
        return [
            {'start': {'order': 'desc'}},
            {'raw_display_name': _raw_name_sort('desc')},
        ]
    # default: same intent as course discovery default + display name tie-breaker
    return [
        {'start': _raw_name_sort('desc')},
        {'new_course_flag': _raw_name_sort('desc')},
        {'new_flag_expired_date': _raw_name_sort('desc')},
        {'raw_display_name': _raw_name_sort('asc')},
    ]


def mixed_content_discovery_search(
        search_terms_course=None,
        search_terms_program=None,
        size=20,
        from_=0,
        course_field_dictionary=None,
        program_field_dictionary=None,
        sort_type=None,
        index_scope=None,
        **kwargs):
    """
    Single Elasticsearch request over one or more catalog indices with unified sort.

    Query/filter construction is duplicated (not refactored) from
    ``course_discovery_search`` and ``programs_discovery_search`` so those
    functions stay unchanged. Sort is only ``_mixed_sort_for_cross_index`` (not
    the per-index sort lists from those helpers).

    ``index_scope``: ``None`` for the default ``course`` + ``program`` indices.
    Otherwise a comma-separated string or list of kinds: ``course``, ``program``.
    Each kind maps to ``COURSEWARE_INDEX_NAME`` or ``PROGRAM_INDEX_NAME`` (hits
    expose the matching ``_index`` field).

    Raw ES facet counts are returned; ``process_range_data`` is not applied.
    """
    from .elastic import ElasticSearchEngine, search_mixed_discovery, build_elasticsearch_query_dict

    course_idx = getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index")
    program_idx = getattr(settings, 'PROGRAM_INDEX_NAME', 'program_index')

    searcher = SearchEngine.get_search_engine(course_idx)
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")
    if not isinstance(searcher, ElasticSearchEngine):
        raise NoSearchEngineError("Mixed discovery requires Elasticsearch engine implementation")

    # --- course branch (query/filter only; sort is always _mixed_sort_for_cross_index) ---
    course_kwargs = dict(kwargs)

    use_search_fields = ["org"]
    (search_fields, _, exclude_dictionary) = CourseSearchFilterGenerator.generate_field_filters(**course_kwargs)
    use_field_dictionary = {}
    use_field_dictionary.update({field: search_fields[field] for field in search_fields if field in use_search_fields})
    if course_field_dictionary:
        use_field_dictionary.update(course_field_dictionary)

    filter_dictionary = {}
    start = use_field_dictionary.pop('start', None)
    if start == 'current':
        filter_dictionary.update({
            'start':
            _format_filter(
                DateRange(None, datetime.utcnow()))
        })
    elif start == 'future':
        filter_dictionary.update({
            'start':
            _format_filter(
                DateRange(datetime.utcnow(), None))
        })

    if getattr(settings, 'ALLOW_CATALOG_VISIBILITY_FILTER', False):
        use_field_dictionary['catalog_visibility'] = CATALOG_VISIBILITY_CATALOG_AND_ABOUT

    exclude = search_fields.get('exclude', None)
    if 'archived' == exclude:
        filter_dictionary.update(
            {
                'end': _format_filter(DateRange(datetime.utcnow(), None))
            }
        )

    filter_dictionary["course_status"] = _format_filter("released")

    q_course = build_elasticsearch_query_dict(
        search_terms_course,
        use_field_dictionary,
        filter_dictionary,
        exclude_dictionary,
        targeting_gate=CourseSearchFilterGenerator.generate_targeting_gate(**course_kwargs),
    )

    # --- program branch (query/filter only; sort is always _mixed_sort_for_cross_index) ---
    program_kwargs = dict(kwargs)

    use_field_dictionary, _, exclude_dictionary = ProgramSearchFilterGenerator.generate_field_filters(**program_kwargs)
    if program_field_dictionary:
        use_field_dictionary.update(program_field_dictionary)

    filter_dictionary = {}
    start = use_field_dictionary.pop('start', None)
    if start == 'current':
        filter_dictionary.update(
            {
                'start': _format_filter(
                    DateRange(None, datetime.utcnow())
                )
            }
        )
    elif start == 'future':
        filter_dictionary.update(
            {
                'start': _format_filter(
                    DateRange(datetime.utcnow(), None)
                )
            }
        )

    exclude = use_field_dictionary.pop('exclude', None)
    if 'archived' == exclude:
        filter_dictionary.update(
            {
                'end': _format_filter(DateRange(datetime.utcnow(), None))
            }
        )

    q_program = build_elasticsearch_query_dict(
        search_terms_program,
        use_field_dictionary,
        filter_dictionary,
        exclude_dictionary,
        targeting_gate=ProgramSearchFilterGenerator.generate_targeting_gate(**program_kwargs),
    )

    scopes = _normalize_index_scope(index_scope)
    scope_queries = {
        'course': (course_idx, q_course),
        'program': (program_idx, q_program),
    }
    scoped_queries = [scope_queries[k] for k in scopes]

    results = search_mixed_discovery(
        searcher,
        scoped_queries,
        _mixed_sort_for_cross_index(sort_type),
        size,
        from_,
        facet_terms=_facet_terms_for_index_scope(scopes),
    )
    return process_range_data(results)


def programs_discovery_search(search_terms=None, size=20, from_=0, field_dictionary=None, only_released_courses=True, **kwargs):
    """Fetch programs data from ElasticSearch."""
    sort_args = kwargs.get('sort_type') or 'default'
    sort_args = sort_args.lower()
    if sort_args == '+display_name':
        sort_args = [{'raw_title': {'order': 'asc'}}, {'start': {'order': 'desc'}}]
    elif sort_args == '-display_name':
        sort_args = [{'raw_title': {'order': 'desc'}}, {'start': {'order': 'desc'}}]
    else:
        sort_args = [{'raw_title': {'order': 'asc'}}, {'start': {'order': 'desc'}}]

    searcher = SearchEngine.get_search_engine(getattr(settings, 'PROGRAM_INDEX_NAME', 'program_index'))
    if not searcher:
        raise NoSearchEngineError('No search engine specified in settings.SEARCH_ENGINE')

    use_field_dictionary, _, exclude_dictionary = ProgramSearchFilterGenerator.generate_field_filters(**kwargs)
    if field_dictionary:
        use_field_dictionary.update(field_dictionary)

    filter_dictionary = {}
    start = use_field_dictionary.pop('start', None)
    if start == 'current':
        filter_dictionary.update(
            {
                'start': _format_filter(
                    DateRange(None, datetime.utcnow())
                )
            }
        )
    elif start == 'future':
        filter_dictionary.update(
            {
                'start': _format_filter(
                    DateRange(datetime.utcnow(), None)
                )
            }
        )

    exclude = use_field_dictionary.pop('exclude', None)
    if 'archived' == exclude:
        filter_dictionary.update(
            {
                'end': _format_filter(DateRange(datetime.utcnow(), None))
            }
        )

    if only_released_courses:
        filter_dictionary["course_status"] = _format_filter("released")

    results = searcher.search(
        query_strings=search_terms,
        size=size,
        from_=from_,
        field_dictionary=use_field_dictionary,
        # show if no enrollment end is provided and has not yet been reached
        filter_dictionary=filter_dictionary,
        exclude_dictionary=exclude_dictionary,
        facet_terms=program_discovery_facets(),
        sort=sort_args,
        ga_total=kwargs.pop('ga_total', False),
        targeting_gate=ProgramSearchFilterGenerator.generate_targeting_gate(**kwargs),
    )

    return process_range_data(results)

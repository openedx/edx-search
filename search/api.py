""" search business logic implementations """

from datetime import datetime

from django.conf import settings

from .filter_generator import SearchFilterGenerator
from .search_engine_base import SearchEngine
from .result_processor import SearchResultProcessor
from .utils import DateRange

# Default filters that we support, override using COURSE_DISCOVERY_FILTERS setting if desired
DEFAULT_FILTER_FIELDS = ["org", "modes", "language"]


def course_discovery_filter_fields():
    """
    Look up the desired list of course discovery filter fields.
    """
    return getattr(settings, "COURSE_DISCOVERY_FILTERS", DEFAULT_FILTER_FIELDS)


def course_discovery_aggregations():
    """
    Discovery aggregations to include bucket names.

    By default we specify each filter field with unspecified size attribute.
    """
    return getattr(
        settings,
        "COURSE_DISCOVERY_AGGREGATIONS",
        {field: {} for field in course_discovery_filter_fields()}
    )


class NoSearchEngineError(Exception):
    """
    NoSearchEngineError exception.

    It is thrown if no search engine is specified.
    """


def perform_search(
        search_term,
        user=None,
        size=10,
        from_=0,
        course_id=None):
    """
    Call the search engine with the appropriate parameters
    """
    # field_, filter_ and exclude_dictionary(s) can be overridden by calling application
    # field_dictionary includes course if course_id provided
    (field_dictionary, filter_dictionary, exclude_dictionary) = SearchFilterGenerator.generate_field_filters(
        user=user,
        course_id=course_id
    )

    searcher = SearchEngine.get_search_engine(
        getattr(settings, "COURSEWARE_CONTENT_INDEX_NAME", "courseware_content")
    )
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    results = searcher.search_string(
        search_term,
        field_dictionary=field_dictionary,
        filter_dictionary=filter_dictionary,
        exclude_dictionary=exclude_dictionary,
        size=size,
        from_=from_,
    )

    # post-process the result
    for result in results["results"]:
        result["data"] = SearchResultProcessor.process_result(result["data"], search_term, user)

    results["access_denied_count"] = len([r for r in results["results"] if r["data"] is None])
    results["results"] = [r for r in results["results"] if r["data"] is not None]

    return results


def course_discovery_search(search_term=None, size=20, from_=0, field_dictionary=None):
    """
    Course Discovery activities against the search engine index of course details
    """
    # We'll ignore the course-enrollemnt informaiton in field and filter
    # dictionary, and use our own logic upon enrollment dates for these
    use_search_fields = ["org"]
    (search_fields, _, exclude_dictionary) = SearchFilterGenerator.generate_field_filters()
    use_field_dictionary = {
        field: search_fields[field]
        for field in search_fields if field in use_search_fields
    }
    if field_dictionary:
        use_field_dictionary.update(field_dictionary)
    if not getattr(settings, "SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING", False):
        use_field_dictionary["enrollment_start"] = DateRange(None, datetime.utcnow())

    searcher = SearchEngine.get_search_engine(
        getattr(settings, "COURSEWARE_INFO_INDEX_NAME", "course_info")
    )
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    results = searcher.search(
        query_string=search_term,
        size=size,
        from_=from_,
        # only show when enrollment start IS provided and is before now
        field_dictionary=use_field_dictionary,
        # show if no enrollment end is provided and has not yet been reached
        filter_dictionary={"enrollment_end": DateRange(datetime.utcnow(), None)},
        exclude_dictionary=exclude_dictionary,
        aggregation_terms=course_discovery_aggregations(),
    )

    return results

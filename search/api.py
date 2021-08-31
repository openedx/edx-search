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
    """ look up the desired list of course discovery filter fields """
    return getattr(settings, "COURSE_DISCOVERY_FILTERS", DEFAULT_FILTER_FIELDS)


def course_discovery_facets():
    """ Discovery facets to include, by default we specify each filter field with unspecified size attribute """
    return getattr(settings, "COURSE_DISCOVERY_FACETS", {field: {} for field in course_discovery_filter_fields()})


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
        course_id=None):
    """ Call the search engine with the appropriate parameters """
    # field_, filter_ and exclude_dictionary(s) can be overridden by calling application
    # field_dictionary includes course if course_id provided
    (field_dictionary, filter_dictionary, exclude_dictionary) = SearchFilterGenerator.generate_field_filters(
        user=user,
        course_id=course_id
    )

    searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    results = searcher.search_string(
        search_term,
        field_dictionary=field_dictionary,
        filter_dictionary=filter_dictionary,
        exclude_dictionary=exclude_dictionary,
        size=size,
        from_=from_,
        doc_type="courseware_content",
    )

    # post-process the result
    for result in results["results"]:
        result["data"] = SearchResultProcessor.process_result(result["data"], search_term, user)

    results["access_denied_count"] = len([r for r in results["results"] if r["data"] is None])
    results["results"] = [r for r in results["results"] if r["data"] is not None]

    return results


def hack_course_is_accessible_to_current_user(course_id, access_action):
    """
    Check if the currently logged-in user has access to a course.

    This is a hack function that should be refactored into the LMS.
    See RED-637.
    """
    from lms.djangoapps.courseware.access import has_access
    from crum import get_current_request
    from opaque_keys.edx.keys import CourseKey
    from xmodule.modulestore.django import modulestore

    ms = modulestore()
    user = get_current_request().user

    return has_access(
        user,
        access_action,
        ms.get_course(CourseKey.from_string(course_id), depth=0),
    )


def hack_filter_discovery_results(results):
    """
    Filter CourseDiscovery search results.

    This is a hack function that should be refactored into the LMS.
    See RED-637.
    """
    import six

    for result in results["results"]:
        if not hack_course_is_accessible_to_current_user(result['data']['id'], 'see_in_catalog'):
            result["data"] = None

    # Count and remove the results that has no access
    access_denied_count = len([r for r in results["results"] if r["data"] is None])
    results["access_denied_count"] = access_denied_count
    results["results"] = [r for r in results["results"] if r["data"] is not None]

    # Hack: Naively reduce the facet numbers by the access denied results
    # This is not the smartest hack, and customers could report issues
    # The solution is most likely to just remove the facet numbers
    results["total"] = max(0, results["total"] - access_denied_count)
    for name, facet in six.iteritems(results["facets"]):
        facet["other"] = max(0, facet.get("other", 0) - access_denied_count)
        facet["terms"] = {
            term: max(0, count - access_denied_count)
            for term, count in six.iteritems(facet["terms"])
            # Remove the facet terms that has no results
            if max(0, count - access_denied_count)
        }
    return results


def course_discovery_search(search_term=None, size=20, from_=0, field_dictionary=None):
    """
    Course Discovery activities against the search engine index of course details
    """
    # We'll ignore the course-enrollemnt informaiton in field and filter
    # dictionary, and use our own logic upon enrollment dates for these
    use_search_fields = ["org"]
    (search_fields, _, exclude_dictionary) = SearchFilterGenerator.generate_field_filters()
    use_field_dictionary = {}
    use_field_dictionary.update({field: search_fields[field] for field in search_fields if field in use_search_fields})
    if field_dictionary:
        use_field_dictionary.update(field_dictionary)
    if not getattr(settings, "SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING", False):
        use_field_dictionary["enrollment_start"] = DateRange(None, datetime.utcnow())

    searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    results = searcher.search(
        query_string=search_term,
        doc_type="course_info",
        size=size,
        from_=from_,
        # only show when enrollment start IS provided and is before now
        field_dictionary=use_field_dictionary,
        # show if no enrollment end is provided and has not yet been reached
        filter_dictionary={"enrollment_end": DateRange(datetime.utcnow(), None)},
        exclude_dictionary=exclude_dictionary,
        facet_terms=course_discovery_facets(),
    )

    return hack_filter_discovery_results(results)

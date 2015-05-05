""" search business logic implementations """
from datetime import datetime

from django.conf import settings

from .filter_generator import SearchFilterGenerator
from .search_engine_base import SearchEngine
from .result_processor import SearchResultProcessor
from .utils import DateRange


class NoSearchEngineError(Exception):
    """ NoSearchEngineError exception to be thrown if no search engine is specified """
    pass


def perform_search(
        search_term,
        user=None,
        size=10,
        from_=0,
        course_id=None,
        request=None):
    """ Call the search engine with the appropriate parameters """
    # field_ and filter_dictionary(s) which can be overridden by calling application
    # field_dictionary includes course if course_id provided
    field_dictionary, filter_dictionary = SearchFilterGenerator.generate_field_filters(
        user=user,
        course_id=course_id,
        request=request,
    )

    searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    results = searcher.search_string(
        search_term,
        field_dictionary=field_dictionary,
        filter_dictionary=filter_dictionary,
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


def course_discovery_search(search_term=None, size=20, from_=0, field_dictionary=None):
    """
    Course Discovery activities against the search engine index of course details
    """
    searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
    if not searcher:
        raise NoSearchEngineError("No search engine specified in settings.SEARCH_ENGINE")

    use_field_dictionary = {}
    if field_dictionary:
        use_field_dictionary.update(field_dictionary)
    use_field_dictionary.update({"enrollment_start": DateRange(None, datetime.utcnow())})

    results = searcher.search(
        query_string=search_term,
        doc_type="course_info",
        size=size,
        from_=from_,
        # only show when enrollment start IS provided and is before now
        field_dictionary=use_field_dictionary,
        # show if no enrollment end is provided and has not yet been reached
        filter_dictionary={"enrollment_end": DateRange(datetime.utcnow(), None)},
    )

    return results

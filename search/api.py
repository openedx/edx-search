""" search business logic implementations """
from django.conf import settings

from .filter_generator import SearchFilterGenerator
from .search_engine_base import SearchEngine
from .result_processor import SearchResultProcessor


class NoSearchEngine(Exception):
    """ NoSearchEngine exception to be thrown if no search engine is specified """
    pass


def perform_search(
        search_terms,
        user=None,
        size=10,
        from_=0,
        course_id=None,
        cohort_id=None):
    """ Call the search engine with the appropriate parameters """
    # field_ and filter_dictionary(s) which can be overridden by calling application
    # field_dictionary includes course if course_id provided
    field_dictionary, filter_dictionary = SearchFilterGenerator.generate_field_filters(
        user=user,
        course_id=course_id,
        cohort_id=cohort_id,
    )

    searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
    if not searcher:
        raise NoSearchEngine("No search engine specified in settings.SEARCH_ENGINE")

    results = searcher.search_string(
        search_terms,
        field_dictionary=field_dictionary,
        filter_dictionary=filter_dictionary,
        size=size,
        from_=from_,
    )

    # post-process the result
    for result in results["results"]:
        result["data"] = SearchResultProcessor.process_result(result["data"], search_terms, user)

    results["access_denied_count"] = len([r for r in results["results"] if r["data"] is None])
    results["results"] = [r for r in results["results"] if r["data"] is not None]

    return results

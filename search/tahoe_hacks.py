"""
Module for Tahoe hacks for the edx-search repository.
"""

from django.conf import settings


def has_access_for_results(results):
    """
    Filter CourseDiscovery search results via the edX Platform LMS `has_access` function.

    This is a hack function that should be refactored into the LMS.
    See RED-637.
    """
    if not getattr(settings, 'TAHOE_ENABLE_HAS_ACCESS_FILTER', True):
        return results

    from lms.djangoapps.courseware.access import has_access
    from crum import get_current_request
    from opaque_keys.edx.keys import CourseKey

    user = get_current_request().user

    for result in results["results"]:
        course_key = CourseKey.from_string(result['data']['id'])
        if not has_access(user, 'see_in_catalog', course_key):
            result["data"] = None

    # Count and remove the results that has no access
    access_denied_count = len([r for r in results["results"] if r["data"] is None])
    results["access_denied_count"] = access_denied_count
    results["results"] = [r for r in results["results"] if r["data"] is not None]

    # Hack: Naively reduce the facet numbers by the access denied results
    # This is not the smartest hack, and customers could report issues
    # The solution is most likely to just remove the facet numbers
    results["total"] = max(0, results["total"] - access_denied_count)
    for _name, facet in list(results["facets"].items()):
        results["total"] = max(0, results["total"] - access_denied_count)
        facet["other"] = max(0, facet.get("other", 0) - access_denied_count)
        facet["terms"] = {
            term: max(0, count - access_denied_count)
            for term, count in list(facet["terms"].items())
            # Remove the facet terms that has no results
            if max(0, count - access_denied_count)
        }
    return results

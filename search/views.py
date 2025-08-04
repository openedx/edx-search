""" handle requests for courseware search http requests """
# This contains just the url entry points to use if desired, which currently has only one

import json
import logging

from django.conf import settings
from django.http import JsonResponse, QueryDict
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from eventtracking import tracker as track
from .api import perform_search, course_discovery_search, course_discovery_filter_fields
from .initializer import SearchInitializer
from django.views.decorators.csrf import csrf_exempt
# log appears to be standard name used for logger
log = logging.getLogger(__name__)

def parse_post_data(request):
    """Support both JSON and form-encoded input."""
    if request.content_type == 'application/json':
        try:
            body = json.loads(request.body.decode('utf-8'))
        except json.JSONDecodeError:
            log.warning("⚠️ Malformed JSON received")
            return QueryDict('', mutable=True)

        qdict = QueryDict('', mutable=True)
        for key, value in body.items():
            if isinstance(value, list):
                # ensure qdict.getlist("org") returns ['astu', 'openedx'], by appending lists 
                for item in value:
                    qdict.appendlist(key, item)
            else:
                qdict.update({key: value})
        return qdict
    # else return request.post 
    return request.POST
# def _process_pagination_values(request):
#     """ process pagination requests from request parameter """
#     size = 20
#     page = 0
#     from_ = 0
#     if "page_size" in request.POST:
#         size = int(request.POST["page_size"])
#         max_page_size = getattr(settings, "SEARCH_MAX_PAGE_SIZE", 100)
#         # The parens below are superfluous, but make it much clearer to the reader what is going on
#         if not (0 < size <= max_page_size):  # pylint: disable=superfluous-parens
#             raise ValueError(_('Invalid page size of {page_size}').format(page_size=size))

#         if "page_index" in request.POST:
#             page = int(request.POST["page_index"])
#             from_ = page * size
#     return size, from_, page

def _process_pagination_values(data):
    """Extract pagination info from data."""
    size = int(data.get("page_size", 20))
    page = int(data.get("page_index", 0))
    max_page_size = getattr(settings, "SEARCH_MAX_PAGE_SIZE", 100)

    if not (0 < size <= max_page_size):
        raise ValueError(_('Invalid page size of {page_size}').format(page_size=size))

    from_ = page * size
    return size, from_, page

# def _process_field_values(request):
#     """ Create separate dictionary of supported filter values provided """
#     return {
#         field_key: request.POST[field_key]
#         for field_key in request.POST
#         if field_key in course_discovery_filter_fields()
#     }

# ----to support multiple values per key as QueryDict and regular dict like "org=astu&org=openedx" ----
def _process_field_values(data):
    filters = {}
    for key in course_discovery_filter_fields():
        if isinstance(data, QueryDict):
            values = data.getlist(key)
        else:
            values = data.get(key, [])
            if not isinstance(values, list):
                values = [values]
        if values:
            filters[key] = values
    return filters

@require_POST
def do_search(request, course_id=None):
    """
    Search view for http requests

    Args:
        request (required) - django request object
        course_id (optional) - course_id within which to restrict search

    Returns:
        http json response with the following fields
            "took" - how many seconds the operation took
            "total" - how many results were found
            "max_score" - maximum score from these results
            "results" - json array of result documents

            or

            "error" - displayable information about an error that occured on the server

    POST Params:
        "search_string" (required) - text upon which to search
        "page_size" (optional)- how many results to return per page (defaults to 20, with maximum cutoff at 100)
        "page_index" (optional) - for which page (zero-indexed) to include results (defaults to 0)
    """

    # Setup search environment
    SearchInitializer.set_search_enviroment(request=request, course_id=course_id)

    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    search_term = request.POST.get("search_string", None)

    try:
        if not search_term:
            raise ValueError(_('No search term provided for search'))

        size, from_, page = _process_pagination_values(request)

        # Analytics - log search request
        track.emit(
            'edx.course.search.initiated',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
            }
        )

        results = perform_search(
            search_term,
            user=request.user,
            size=size,
            from_=from_,
            course_id=course_id
        )

        status_code = 200

        # Analytics - log search results before sending to browser
        track.emit(
            'edx.course.search.results_displayed',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
                "results_count": results["total"],
            }
        )

    except ValueError as invalid_err:
        results = {
            "error": str(invalid_err)
        }
        log.debug(str(invalid_err))

    except Exception as err:  # pylint: disable=broad-exception-caught
        results = {
            "error": _('An error occurred when searching for "{search_string}"').format(search_string=search_term)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r',
            search_term,
            request.user.id,
            err
        )

    return JsonResponse(results, status=status_code)


@require_POST
@csrf_exempt
def course_discovery(request):
    """
    Search for courses

    Args:
        request (required) - django request object

    Returns:
        http json response with the following fields
            "took" - how many seconds the operation took
            "total" - how many results were found
            "max_score" - maximum score from these resutls
            "results" - json array of result documents

            or

            "error" - displayable information about an error that occured on the server

    POST Params:
        "search_string" (optional) - text with which to search for courses
        "page_size" (optional)- how many results to return per page (defaults to 20, with maximum cutoff at 100)
        "page_index" (optional) - for which page (zero-indexed) to include results (defaults to 0)
    """
    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    # search_term = request.POST.get("search_string", None)
    post_data = parse_post_data(request)
    search_term = post_data.get("search_string", "").strip()
    try:
        size, from_, page = _process_pagination_values(post_data)
        field_dictionary = _process_field_values(post_data)
        # ✅ Allow searches even if search_string is empty, as long as filters are applied
        if not search_term and not field_dictionary:
            search_term = None
        # Analytics - log search request
        track.emit(
            'edx.course_discovery.search.initiated',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
                "filters": field_dictionary, #track filters information
            }
        )

        results = course_discovery_search(
            search_term=search_term,
            size=size,
            from_=from_,
            field_dictionary=field_dictionary,
        )

        # Analytics - log search results before sending to browser
        track.emit(
            'edx.course_discovery.search.results_displayed',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
                "results_count": results["total"],
            }
        )

        status_code = 200

    except ValueError as invalid_err:
        results = {
            "error": str(invalid_err)
        }
        log.debug(str(invalid_err))

    except Exception as err:  # pylint: disable=broad-exception-caught
        results = {
            "error": _('An error occurred when searching for "{search_string}"').format(search_string=search_term)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r',
            search_term,
            request.user.id,
            err
        )

    return JsonResponse(results, status=status_code)

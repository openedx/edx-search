""" handle requests for courseware search http requests """
# This contains just the url entry points to use if desired, which currently has only one
# pylint: disable=too-few-public-methods
import logging
import json

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.utils.translation import ugettext as _
from django.views.decorators.http import require_POST

from .api import perform_search

# log appears to be standard name used for logger
log = logging.getLogger(__name__)  # pylint: disable=invalid-name


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
            "max_score" - maximum score from these resutls
            "results" - json array of result documents

            or

            "error" - displayable information about an error that occured on the server

    POST Params:
        "search_string" (required) - text upon which to search
        "page_size" (optional)- how many results to return per page (defaults to 20, with maximum cutoff at 100)
        "page_index" (optional) - for which page (zero-indexed) to include results (defaults to 0)
    """
    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    search_terms = request.POST.get("search_string", None)
    try:
        if not search_terms:
            raise ValueError(_('No search term provided for search'))

        # process pagination requests
        size = 20
        from_ = 0
        if "page_size" in request.POST:
            size = int(request.POST["page_size"])
            max_page_size = getattr(settings, "SEARCH_MAX_PAGE_SIZE", 100)
            # The parens below are superfluous, but make it much clearer to the reader what is going on
            if not (0 < size <= max_page_size):  # pylint: disable=superfluous-parens
                raise ValueError(_('Invalid page size of {page_size}').format(page_size=size))

            if "page_index" in request.POST:
                from_ = int(request.POST["page_index"]) * size

        results = perform_search(
            search_terms,
            user=request.user,
            size=size,
            from_=from_,
            course_id=course_id,
        )

        status_code = 200

    except ValueError as invalid_err:
        results = {
            "error": unicode(invalid_err)
        }
        log.debug(unicode(invalid_err))

    # Allow for broad exceptions here - this is an entry point from external reference
    except Exception as err:  # pylint: disable=broad-except
        results = {
            "error": _('An error occurred when searching for "{search_string}"').format(search_string=search_terms)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r',
            search_terms,
            request.user.id,
            err
        )

    return HttpResponse(
        json.dumps(results, cls=DjangoJSONEncoder),
        content_type='application/json',
        status=status_code
    )

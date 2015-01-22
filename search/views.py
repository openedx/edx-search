""" handle requests for courseware search http requests """
# This contains just the url entry points to use if desired, which currently has only one
# pylint: disable=too-few-public-methods
import logging
import json
import datetime

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import ugettext as _
from django.views.decorators.http import require_POST

from .api import perform_search

# log appears to be standard name used for logger
log = logging.getLogger(__name__)  # pylint: disable=invalid-name


class DateTimeEncoder(json.JSONEncoder):
    """ encode datetimes into json appropriately """

    def default(self, obj):  # pylint: disable=method-hidden
        """ override default encoding """
        if isinstance(obj, datetime.datetime):
            encoded_object = obj.isoformat()
        else:
            encoded_object = super(DateTimeEncoder, self).default(self, obj)
        return encoded_object


class InvalidPageSize(ValueError):
    """ Exception for invalid page size value passed in """
    pass


@require_POST
def do_search(request, course_id=None):
    """
    Search view for http requests
    """
    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    search_terms = request.POST["search_string"]
    try:
        # process pagination requests
        size = 20
        from_ = 0
        if "page_size" in request.POST:
            size = int(request.POST["page_size"])
            max_page_size = getattr(settings, "SEARCH_MAX_PAGE_SIZE", 100)
            if size < 0 or size > max_page_size:
                raise InvalidPageSize(_('Invalid page size of {page_size}').format(page_size=size))

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

    except InvalidPageSize as invalid_err:
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
            'Search view exception when searching for %s for user %s: %s',
            search_terms,
            request.user.id,
            unicode(err)
        )

    return HttpResponse(
        json.dumps(results, cls=DateTimeEncoder),
        content_type='application/json',
        status=status_code
    )

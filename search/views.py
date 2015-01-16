""" handle requests for courseware search http requests """
import logging
import json
import datetime

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


@require_POST
def do_search(request, course_id=None):
    """
    Search view for http requests
    """
    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    try:
        search_terms = request.POST["search_string"]

        # process pagination requests
        size = 20
        from_ = 0
        if "page_size" in request.POST:
            size = int(request.POST["page_size"])
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
    # Allow for broad exceptions here - this is an entry point from external reference
    except Exception as err:  # pylint: disable=broad-except
        results = {
            "error": str(err)
        }
        log.exception("Search view exception - %s", str(err))

    return HttpResponse(
        json.dumps(results, cls=DateTimeEncoder),
        content_type='application/json',
        status=status_code
    )

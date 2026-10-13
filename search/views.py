# -*- coding: utf-8 -*-
""" handle requests for courseware search http requests """
# This contains just the url entry points to use if desired, which currently has only one
# pylint: disable=too-few-public-methods
import logging
from functools import reduce
import json

from datetime import datetime
from django.conf import settings
from django.db.models import Avg, Count
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from pytz import UTC
import re
import six

from eventtracking import tracker as track
from .api import (
    QueryParseError,
    perform_search,
    course_discovery_search,
    course_discovery_filter_fields,
    programs_discovery_search,
    program_discovery_filter_fields,
    mixed_content_discovery_search,
)
from .initializer import SearchInitializer
from lms.djangoapps.metrics.metrics import catalog_search_log
from lms.djangoapps.program_enrollments.models import ProgramRating
from openedx.core.djangoapps.content.course_overviews.models import CourseRating
from util.string_utils import is_vulnerable_text


# log appears to be standard name used for logger
log = logging.getLogger(__name__)  # pylint: disable=invalid-name

def _process_pagination_values(request):
    """ process pagination requests from request parameter """
    size = 20
    page = 0
    from_ = 0
    if "page_size" in request.POST:
        size = int(request.POST["page_size"])
        max_page_size = getattr(settings, "SEARCH_MAX_PAGE_SIZE", 100)
        # The parens below are superfluous, but make it much clearer to the reader what is going on
        if not (0 < size <= max_page_size):  # pylint: disable=superfluous-parens
            raise ValueError(_('Invalid page size of {page_size}').format(page_size=size))

        if "page_index" in request.POST:
            page = int(request.POST["page_index"])
            from_ = page * size
    return size, from_, page


def _total_pages(count, page_size):
    """Pages needed to show ``count`` items when each page holds up to ``page_size`` items."""
    if not page_size:
        return 0
    return (count + page_size - 1) // page_size


def _process_field_values(request, allowed_fields):
    """ Create separate dictionary of supported filter values provided """
    field_values = {}
    for field_key in request.POST:
        # Check if the key's value is array so using request.POST.getlist to get array value.
        if field_key.endswith('[]'):
            if field_key[:-2] in allowed_fields and request.POST.getlist(field_key):
                field_values[field_key[:-2]] = request.POST.getlist(
                    field_key)[0] if len(request.POST.getlist(
                        field_key)) == 1 else request.POST.getlist(field_key)
        elif field_key in allowed_fields:
            filter_values = request.POST[field_key]

            if field_key == 'vendor' and '|' in filter_values:
                field_values[field_key] = filter_values.split('|')
                continue
            elif field_key == 'course_category' and ',' in filter_values:
                field_values[field_key] = filter_values.split(',')
                continue

            field_values[field_key] = filter_values

    return field_values


def _course_process_field_values(request):
    return _process_field_values(request, course_discovery_filter_fields())


def _programs_process_field_values(request):
    return _process_field_values(request, program_discovery_filter_fields())


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
        log.info('%s courses found.', results['total'])

        results["page_index"] = page # starts from 0
        results["total_pages"] = (results["total"] + size - 1) // size # represents how many pages for this result

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
            "error": six.text_type(invalid_err)
        }
        log.debug(six.text_type(invalid_err))

    except QueryParseError:
        results = {
            "error": _('Your query seems malformed. Check for unmatched quotes.')
        }

    # Allow for broad exceptions here - this is an entry point from external reference
    except Exception as err:  # pylint: disable=broad-except
        results = {
            "error": _('An error occurred when searching for "{search_string}"').format(search_string=search_term)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r',
            search_term,
            request.user.id,
            err
        )

    return HttpResponse(
        json.dumps(results, cls=DjangoJSONEncoder),
        content_type='application/json',
        status=status_code
    )


@require_POST
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

    search_term = request.POST.get("search_string", None)

    try:
        size, from_, page = _process_pagination_values(request)
        field_dictionary = _course_process_field_values(request)

        # Analytics - log search request
        track.emit(
            'edx.course_discovery.search.initiated',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
            }
        )

        if search_term and is_vulnerable_text(search_term):
            raise SyntaxError(
                r'{field} {field_name}: {message}'.format(
                    field=_('Field'), field_name=_('Search'),
                    message=_('This value is invalid.')
                )
            )

        search_terms = set(search_term.split(' ')) if search_term else None

        results = course_discovery_search(
            search_terms=search_terms,
            size=size,
            from_=from_,
            field_dictionary=field_dictionary,
            user=request.user,
            allow_enrollment_end_filter=True,
            sort_type=request.POST.get('sort_type')
        )
        for c in results['results']:
            start = c['data']['start'].replace("+00:00", "Z")
            start = datetime.strptime(start, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
            c['data']['non_started'] = not has_started(start)
        log.info('%s courses found.', results['total'])

        results["page_index"] = page # starts from 0
        results["total_pages"] = (results["total"] + size - 1) // size # represents how many pages for this result

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

    except SyntaxError as syntax_err:
        results = {
            "illegal_search_string": six.text_type(syntax_err)
        }

    except ValueError as invalid_err:
        results = {
            "error": six.text_type(invalid_err)
        }
        log.debug(six.text_type(invalid_err))

    except QueryParseError:
        results = {
            "error": _('Your query seems malformed. Check for unmatched quotes.')
        }

    # Allow for broad exceptions here - this is an entry point from external reference
    except Exception as err:  # pylint: disable=broad-except
        results = {
            "error": _('An error occurred when searching for "{search_string}"').format(search_string=search_term)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r',
            search_term,
            request.user.id,
            err
        )

    catalog_search_log(request, "courses", results)

    return HttpResponse(
        json.dumps(results, cls=DjangoJSONEncoder),
        content_type='application/json',
        status=status_code
    )


@require_POST
def program_discovery(request):
    """
    Search for programs from ElasticSearch

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

    # Test code
    # from django.views.decorators.csrf import csrf_exempt
    # from django.http import QueryDict
    # request.POST = QueryDict('', mutable=True)
    # request.POST.update(
    #     {
    #         "language": [
    #                 "fr-fr"
    #             ],
    #         "page_no": 1,
    #         "page_size": 60,
    #         "search_content": "",
    #         "sort_type": "+display_name"
    #     }
    # )
    search_term = request.POST.get('search_string', None)
    search_term = search_term if search_term else None

    try:
        size, from_, page = _process_pagination_values(request)
        field_dictionary = _programs_process_field_values(request)

        if search_term and is_vulnerable_text(search_term):
            raise SyntaxError(
                r'{field} {field_name}: {message}'.format(
                    field=_('Field'), field_name=_('Search'),
                    message=_('This value is invalid.')
                )
            )

        # Analytics - log search request
        track.emit(
            'edx.course_discovery.search.initiated',
            {
                "search_term": search_term,
                "page_size": size,
                "page_number": page,
            }
        )

        results = programs_discovery_search(
            search_terms=search_term,
            size=size,
            from_=from_,
            field_dictionary=field_dictionary,
            # user=request.user,
            include_course_filter=True,
            sort_type=request.POST.get('sort_type')
        )
        for p in results['results']:
            start = datetime.strptime(p['data']['start'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
            p['data']['non_started'] = not has_started(start)

        log.info('%s programs found.', results['total'])

        results["page_index"] = page # starts from 0
        results["total_pages"] = (results["total"] + size - 1) // size # represents how many pages for this result

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

    except SyntaxError as syntax_err:
        results = {
            "illegal_search_string": six.text_type(syntax_err)
        }

    except ValueError as invalid_err:
        results = {
            "error": six.text_type(invalid_err)
        }
        log.debug(six.text_type(invalid_err))

    except QueryParseError:
        results = {
            "error": _('Your query seems malformed. Check for unmatched quotes.')
        }

    # Allow for broad exceptions here - this is an entry point from external reference
    except Exception as err:
        results = {
            'error': _('An error occurred when searching for "{search_string}"').format(search_string=search_term),
            'error_description': six.text_type(err)
        }
        log.exception(
            'Search view exception when searching for %s for user %s: %r : %s',
            search_term,
            request.user.id,
            err
        )

    catalog_search_log(request, "programs", results)

    return HttpResponse(
        json.dumps(results, cls=DjangoJSONEncoder),
        content_type='application/json',
        status=status_code
    )


def _add_addtional_course_data(hit, rating_by_course):
    start = hit['data']['start'].replace('+00:00', 'Z')
    start = datetime.strptime(start, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    hit['data']['non_started'] = not has_started(start)
    stats = rating_by_course.get(
        six.text_type(hit['data']['id']), {'rating_count': 0, 'avg_rating': 0}
    )
    hit['data']['rating_count'] = stats['rating_count']
    hit['data']['avg_rating'] = stats['avg_rating']


def _add_addtional_program_data(hit, rating_by_program):
    start = datetime.strptime(hit['data']['start'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)
    hit['data']['non_started'] = not has_started(start)
    program_uuid = hit['data']['uuid']
    stats = rating_by_program.get(
        six.text_type(program_uuid), {'rating_count': 0, 'avg_rating': 0}
    )
    hit['data']['rating_count'] = stats['rating_count']
    hit['data']['avg_rating'] = stats['avg_rating']


def _mixed_discovery_index_names():
    """Index name prefixes used in ES ``_index`` for mixed discovery (see settings)."""
    return (
        getattr(settings, 'COURSEWARE_INDEX_NAME', 'courseware_index'),
        getattr(settings, 'PROGRAM_INDEX_NAME', 'program_index'),
    )


def _parse_index_scope_parameter(raw):
    """
    Parse POST ``index_scope`` / ``content_scope`` (comma-separated).

    Returns ``None`` to use API default (course + program), or an ordered list of
    ``course`` and/or ``program`` with duplicates removed.
    """
    if not raw or not six.text_type(raw).strip():
        return None
    valid = {'course', 'program'}
    out = []
    for chunk in six.text_type(raw).split(','):
        p = chunk.strip().lower()
        if not p:
            continue
        if p not in valid:
            raise ValueError(
                _('Invalid index_scope token "%(token)s"; use course or program, comma-separated.')
                % {'token': p}
            )
        if p not in out:
            out.append(p)
    return out if out else ['course', 'program']


def _content_type_for_mixed_hit(hit):
    """
    Derive hit kind from Elasticsearch ``_index`` on each hit, using the same
    configured index names as ``mixed_content_discovery_search`` / ``index_scope``.
    """
    index_name = hit.get('_index') or ''
    if not isinstance(index_name, six.string_types):
        index_name = six.text_type(index_name)
    course_idx, program_idx = _mixed_discovery_index_names()
    if index_name.startswith(course_idx):
        return 'course'
    if index_name.startswith(program_idx):
        return 'program'
    return 'unknown'


@require_POST
def mixed_content_discovery(request):
    """
    Single Elasticsearch request across catalog indices (course and program) with
    unified sort. Filtering mirrors ``course_discovery`` / ``program_discovery``; see
    ``mixed_content_discovery_search``.

    Optional POST ``index_scope`` (alias ``content_scope``): omit for the default
    (course + program). Comma-separated kinds select which indices to search, e.g.
    ``program,course`` (same as default), ``course``, or ``program``. Each kind
    matches Elasticsearch ``_index`` prefixes from ``COURSEWARE_INDEX_NAME`` and
    ``PROGRAM_INDEX_NAME``.
    """
    results = {'error': _('Nothing to search')}
    status_code = 500
    search_term = request.POST.get('search_string', None)

    try:
        size, from_, page = _process_pagination_values(request)
        course_field_dictionary = _course_process_field_values(request)
        program_field_dictionary = _programs_process_field_values(request)

        track.emit(
            'edx.course_discovery.search.initiated',
            {'search_term': search_term, 'page_size': size, 'page_number': page}
        )
        if search_term and is_vulnerable_text(search_term):
            raise SyntaxError(
                r'{field} {field_name}: {message}'.format(
                    field=_('Field'), field_name=_('Search'),
                    message=_('This value is invalid.')
                )
            )

        search_terms_words = re.split(r'[, ]+', search_term) if search_term else None

        index_scope = _parse_index_scope_parameter(
            request.POST.get('index_scope') or request.POST.get('content_scope')
        )

        course_filters = set(course_field_dictionary.keys())
        program_filters = set(program_field_dictionary.keys())
        # Learning-path filters are a subset of course filters. When the request asks for
        # both kinds, drop the kind that cannot apply the active filters. Never remove the
        # last remaining kind (empty scope would be re-expanded to course+program).
        if (isinstance(index_scope, list) and len(index_scope) > 1 and u'program' in index_scope
                and (course_filters - program_filters) and not (program_filters - course_filters)):
            index_scope.remove(u'program')
        if (isinstance(index_scope, list) and len(index_scope) > 1 and u'course' in index_scope
                and (program_filters - course_filters) and not (course_filters - program_filters)):
            index_scope.remove(u'course')

        raw_results = mixed_content_discovery_search(
            search_terms_course=search_terms_words,
            search_terms_program=search_terms_words,
            size=size,
            from_=from_,
            course_field_dictionary=course_field_dictionary,
            program_field_dictionary=program_field_dictionary,
            sort_type=request.POST.get('sort_type'),
            index_scope=index_scope,
        )

        merged_results = []
        course_ids = []
        program_uuids = []
        for hit in raw_results.get('results', []):
            ctype = _content_type_for_mixed_hit(hit)
            hit['content_type'] = ctype
            if ctype == 'program':
                program_uuids.append(hit['data']['uuid'])
            elif ctype == 'course':
                course_ids.append(hit['data']['id'])
            merged_results.append(hit)

        rating_by_course = {}
        if course_ids:
            rating_rows = CourseRating.objects.filter(
                course_id__in=course_ids
            ).values('course_id').annotate(rating_count=Count('pk'), avg_rating=Avg('rating'))
            rating_by_course = {
                six.text_type(row['course_id']): {
                    'rating_count': row['rating_count'],
                    'avg_rating': row['avg_rating'] if row['avg_rating'] is not None else 0,
                } for row in rating_rows
            }
        rating_by_program = {}
        if program_uuids:
            rating_rows = ProgramRating.objects.filter(
                program_uuid__in=program_uuids
            ).values('program_uuid').annotate(rating_count=Count('pk'), avg_rating=Avg('rating'))
            rating_by_program = {
                six.text_type(row['program_uuid']): {
                    'rating_count': row['rating_count'],
                    'avg_rating': row['avg_rating'] if row['avg_rating'] is not None else 0,
                } for row in rating_rows
            }

        for hit in merged_results:
            if hit['content_type'] == 'course':
                _add_addtional_course_data(hit, rating_by_course)
            elif hit['content_type'] == 'program':
                _add_addtional_program_data(hit, rating_by_program)

        total = raw_results.get('total', 0)
        results = {
            'took': raw_results.get('took', 0),
            'total': total,
            'max_score': raw_results.get('max_score'),
            'results': merged_results
        }
        if 'facets' in raw_results:
            results['facets'] = raw_results['facets']
            ratings = {}
            for rating_by in (rating_by_course, rating_by_program):
                for i in rating_by.values():
                    key = six.text_type(float(i['avg_rating']))
                    ratings[key] = ratings.get(key, 0) + 1
                results['facets']['rating'] = {
                    'total': len(ratings.keys()), 'terms': ratings, 'other': 0, 'missing': 0
                }
        results['page_index'] = page
        results['total_pages'] = _total_pages(total, size)

        track.emit(
            'edx.course_discovery.search.results_displayed',
            {'search_term': search_term, 'page_size': size, 'page_number': page, 'results_count': total}
        )
        log.info('mixed_content_discovery: %s total hits (page %s, page_size %s).',total, page, size)
        status_code = 200

    except SyntaxError as syntax_err:
        results = {'illegal_search_string': six.text_type(syntax_err)}
    except ValueError as invalid_err:
        results = {'error': six.text_type(invalid_err)}
        log.debug(six.text_type(invalid_err))
    except QueryParseError:
        results = {'error': _('Your query seems malformed. Check for unmatched quotes.')}
    except Exception as err:  # pylint: disable=broad-except
        results = {
            'error': _('An error occurred when searching for "{search_string}"').format(search_string=search_term)
        }
        log.exception(
            'mixed_content_discovery exception for %s user %s: %r', search_term, request.user.id, err
        )

    if isinstance(results, dict):
        results.setdefault('total', 0)
    catalog_search_log(request, 'mixed_content', results)

    return HttpResponse(
        json.dumps(results, cls=DjangoJSONEncoder), content_type='application/json', status=status_code
    )


def has_started(start_date):
    """
    Given a course or program's start datetime, returns whether the current time's past it.

    Arguments:
        start_date (datetime): The start datetime of the course in question.
    """
    return datetime.now(UTC) > start_date if start_date is not None else False

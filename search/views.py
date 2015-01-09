""" handle requests for courseware search http requests """
import importlib
import inspect
import json
import re

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_exempt

from .manager import SearchEngine

DESIRED_EXCERPT_LENGTH = 100
ELLIPSIS = "&hellip;"


def _load_class(class_path, default):
    """ Loads the class from the class_path string """
    if class_path is None:
        return default

    component = class_path.rsplit('.', 1)
    result_processor = getattr(
        importlib.import_module(component[0]),
        component[1],
        default
    ) if len(component) > 1 else default

    return result_processor


class SearchResultProcessor(object):

    """
    Class to post-process a search result from the search.
    Each @property defined herein will be exposed as a member in the json-results given to the end user

    Users of this search app will override this class and update setting for SEARCH_RESULT_PROCESSOR
    In particular, an application using this search app will want to:
        * override `should_remove`:
            - This is where an application can decide whether to deny access to the result provided
        * provide additional properties to be included
            - Mark a method as a property and it's returned value will be added into the resultset given
    """

    _results_fields = {}
    _match_phrase = None

    def __init__(self, dictionary, match_phrase):
        self._results_fields = dictionary
        self._match_phrase = match_phrase

    @staticmethod
    def strings_in_dictionary(dictionary):
        """ Used by default implementation for finding excerpt """
        strings = [value for value in dictionary.itervalues() if not isinstance(value, dict)]
        for child_dict in [dv for dv in dictionary.itervalues() if isinstance(dv, dict)]:
            strings.extend(SearchResultProcessor.strings_in_dictionary(child_dict))
        return strings

    @staticmethod
    def find_matches(strings, words, length_hoped):
        """ Used by default property excerpt """
        matches = []
        length_found = 0
        for word in words:
            length_found += sum([len(s) for s in strings if word.lower() in s.lower() and s not in matches])
            matches.extend([s for s in strings if word.lower() in s.lower() and s not in matches])
            if length_found >= length_hoped:
                return [SearchResultProcessor.shorten_string(m, words, length_hoped) for m in matches]
        return [SearchResultProcessor.shorten_string(m, words, length_hoped) for m in matches]

    @staticmethod
    def boldface_matches(match_in, match_word):
        """ boldface the matches within the excerpt """
        matches = re.finditer(match_word, match_in, re.IGNORECASE)
        for matched_string in set([match.group() for match in matches]):
            match_in = match_in.replace(matched_string, u"<b>{}</b>".format(matched_string))
        return match_in

    @staticmethod
    def shorten_string(string_in, words, length_hoped):
        """ Used by default property excerpt - Make sure the excerpt is not too long"""
        if len(string_in) <= length_hoped:
            return string_in

        word_at = -1
        word_index = 0
        while word_at < 0 and word_index < len(words):
            word = words[word_index]
            word_at = string_in.lower().find(word.lower())
            word_index += 1

        start_index = (word_at - length_hoped / 2)
        if start_index < 0:
            start_index = 0
        end_index = (word_at + length_hoped / 2) + len(word) + 1
        if end_index >= len(string_in):
            end_index = None

        return u"{}{}{}".format(
            "" if start_index < 1 else ELLIPSIS,
            string_in[start_index:end_index].strip(),
            "" if end_index is None else ELLIPSIS,
        )

    # disabling pylint violations because overriders will want to use these
    def should_remove(self, user):  # pylint: disable=unused-argument, no-self-use
        """
        Override this in a class in order to add in last-chance access checks to the search process
        Your application will want to make this decision
        """
        return False

    def add_properties(self):
        """
        Called during post processing of result
        Any properties defined in your subclass will get exposed as members of the result json from the search
        """
        for property_name in [p[0] for p in inspect.getmembers(self.__class__) if isinstance(p[1], property)]:
            self._results_fields[property_name] = getattr(self, property_name, None)

    @classmethod
    def process_result(cls, dictionary, match_phrase, user):
        """
        Called from within search handler. Finds desired subclass and decides if the
        result should be removed and adds properties derived from the result information
        """
        result_processor = _load_class(getattr(settings, "SEARCH_RESULT_PROCESSOR", None), cls)
        srp = result_processor(dictionary, match_phrase)
        if srp.should_remove(user):
            return None
        srp.add_properties()
        return dictionary

    @property
    def excerpt(self):
        """
        Property to display a useful excerpt representing the matches within the results
        """
        if "content" not in self._results_fields:
            return None

        match_words = [self._match_phrase]
        separate_words = self._match_phrase.split(' ')
        if len(separate_words) > 1:
            match_words.extend(self._match_phrase.split(' '))

        matches = SearchResultProcessor.find_matches(
            SearchResultProcessor.strings_in_dictionary(self._results_fields["content"]),
            match_words,
            DESIRED_EXCERPT_LENGTH
        )
        excerpt_text = '...'.join(matches)

        for match_word in match_words:
            excerpt_text = SearchResultProcessor.boldface_matches(excerpt_text, match_word)

        return excerpt_text

    @property
    def url(self):
        """
        Property to display the url for the given location, useful for allowing navigation
        """
        if "course" not in self._results_fields or "id" not in self._results_fields:
            return None

        return u"/courses/{course_id}/jump_to/{location}".format(
            course_id=self._results_fields["course"],
            location=self._results_fields["id"],
        )


class SearchFilterGenerator(object):

    """
    Class to provide a set of filters for the search.
    Users of this search app will override this class and update setting for SEARCH_FILTER_GENERATOR
    """

    # disabling pylint violations because overriders will want to use these
    # pylint: disable=unused-argument, no-self-use
    def filter_dictionary(self, **kwargs):
        """ base implementation which filters via start_date """
        return {"start_date": [None, "now"]}

    def field_dictionary(self, **kwargs):
        """ base implementation which add course if provided """
        field_dictionary = {}
        if "course_id" in kwargs and kwargs["course_id"]:
            field_dictionary["course"] = kwargs["course_id"]

        return field_dictionary

    @classmethod
    def generate_field_filters(cls, **kwargs):
        """
        Called from within search handler
        Finds desired subclass and adds filter information based upon user information
        """
        generator = _load_class(getattr(settings, "SEARCH_FILTER_GENERATOR", None), cls)()
        return generator.field_dictionary(**kwargs), generator.filter_dictionary(**kwargs)


@csrf_exempt
def do_search(request, course_id=None):
    """
    Search view for http requests
    """
    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    try:
        if request.method == 'POST':
            search_terms = request.POST["search_string"]

            # field_ and filter_dictionary(s) which can be overridden by calling application
            # field_dictionary includes course if course_id provided
            field_dictionary, filter_dictionary = SearchFilterGenerator.generate_field_filters(
                user=request.user,
                course_id=course_id,
            )

            searcher = SearchEngine.get_search_engine(getattr(settings, "COURSEWARE_INDEX_NAME", "courseware_index"))
            results = searcher.search_string(
                search_terms,
                field_dictionary=field_dictionary,
                filter_dictionary=filter_dictionary,
            )

            # post-process the result
            for result in results["results"]:
                result["data"] = SearchResultProcessor.process_result(result["data"], search_terms, request.user)

            results["access_denied_count"] = len([r for r in results["results"] if r["data"] is None])
            results["results"] = [r for r in results["results"] if r["data"] is not None]

            status_code = 200
    except StandardError as err:
        results = {
            "error": str(err)
        }

    return HttpResponse(
        json.dumps(results),
        content_type='application/json',
        status=status_code
    )

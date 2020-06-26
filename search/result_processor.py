""" overridable result processor object to allow additional properties to be exposed """

import inspect
from itertools import chain
import json
import logging
import re
import shlex
import textwrap

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from .utils import _load_class

DESIRED_EXCERPT_LENGTH = 100
ELLIPSIS = '<span class="search-results-ellipsis"></span>'

# log appears to be standard name used for logger
log = logging.getLogger(__name__)  # pylint: disable=invalid-name


class SearchResultProcessor:

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
        strings = [value for value in dictionary.values() if not isinstance(value, dict)]
        for child_dict in [dv for dv in dictionary.values() if isinstance(dv, dict)]:
            strings.extend(SearchResultProcessor.strings_in_dictionary(child_dict))
        return strings

    @staticmethod
    def find_matches(strings, words, length_hoped):
        """ Used by default property excerpt """
        lower_words = [w.lower() for w in words]

        def has_match(string):
            """ Do any of the words match within the string """
            lower_string = string.lower()
            for test_word in lower_words:
                if test_word in lower_string:
                    return True
            return False

        shortened_strings = [textwrap.wrap(s) for s in strings]
        short_string_list = list(chain.from_iterable(shortened_strings))
        matches = [ms for ms in short_string_list if has_match(ms)]

        cumulative_len = 0
        break_at = None
        for idx, match in enumerate(matches):
            cumulative_len += len(match)
            if cumulative_len >= length_hoped:
                break_at = idx
                break

        return matches[0:break_at]

    @staticmethod
    def decorate_matches(match_in, match_word):
        """ decorate the matches within the excerpt """
        matches = re.finditer(match_word, match_in, re.IGNORECASE)
        for matched_string in {match.group() for match in matches}:
            match_in = match_in.replace(
                matched_string,
                getattr(settings, "SEARCH_MATCH_DECORATION", u"<b>{}</b>").format(matched_string)
            )
        return match_in

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
        try:
            srp.add_properties()
        # protect around any problems introduced by subclasses within their properties
        except Exception as ex:  # pylint: disable=broad-except
            log.exception("error processing properties for %s - %s: will remove from results",  # lint-amnesty, pylint: disable=unicode-format-string
                          json.dumps(dictionary, cls=DjangoJSONEncoder), str(ex))
            return None
        return dictionary

    @property
    def excerpt(self):
        """
        Property to display a useful excerpt representing the matches within the results
        """
        if "content" not in self._results_fields:
            return None

        match_phrases = [self._match_phrase]
        separate_phrases = list(shlex.split(self._match_phrase))
        if len(separate_phrases) > 1:
            match_phrases.extend(separate_phrases)
        else:
            match_phrases = separate_phrases

        matches = SearchResultProcessor.find_matches(
            SearchResultProcessor.strings_in_dictionary(self._results_fields["content"]),
            match_phrases,
            DESIRED_EXCERPT_LENGTH
        )
        excerpt_text = ELLIPSIS.join(matches)

        for match_word in match_phrases:
            excerpt_text = SearchResultProcessor.decorate_matches(excerpt_text, match_word)

        return excerpt_text

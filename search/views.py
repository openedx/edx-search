""" handle requests for courseware search http requests """
import importlib
import inspect
import json
import re

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_exempt

from manager import SearchEngine

DESIRED_EXCERPT_LENGTH = 100
ELLIPSIS = "&hellip;"


class SearchResultProcessor(object):

    _results_fields = {}
    _match_phrase = None

    def __init__(self, dictionary, match_phrase):
        self._results_fields = dictionary
        self._match_phrase = match_phrase

    @staticmethod
    def strings_in_dictionary(dictionary):
        strings = [value for value in dictionary.itervalues() if not isinstance(value, dict)]
        for child_dict in [dv for dv in dictionary.itervalues() if isinstance(dv, dict)]:
            strings.extend(SearchResultProcessor.strings_in_dictionary(child_dict))
        return strings

    @staticmethod
    def find_matches(strings, words, length_hoped):
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
        matches = re.finditer(match_word, match_in, re.IGNORECASE)
        for matched_string in set([match.group() for match in matches]):
            match_in = match_in.replace(matched_string, "<b>{}</b>".format(matched_string))
        return match_in

    @staticmethod
    def shorten_string(string_in, words, length_hoped):
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

        return "{}{}{}".format(
            "" if start_index < 1 else ELLIPSIS,
            string_in[start_index:end_index].strip(),
            "" if end_index is None else ELLIPSIS,
        )

    def should_remove(self, user):
        return False

    def add_properties(self):
        for property_name in [p[0] for p in inspect.getmembers(self.__class__) if isinstance(p[1], property)]:
            self._results_fields[property_name] = getattr(self, property_name, None)

    @classmethod
    def process_result(cls, dictionary, match_phrase, user):
        use_processor = getattr(settings, "SEARCH_RESULT_PROCESSOR", None)
        if use_processor:
            component = use_processor.rsplit('.', 1)
            result_processor = getattr(
                importlib.import_module(component[0]),
                component[1],
                cls
            ) if len(component) > 1 else cls
        else:
            result_processor = cls

        srp = result_processor(dictionary, match_phrase)
        if srp.should_remove(user):
            return None
        srp.add_properties()
        return dictionary

    @property
    def excerpt(self):
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
        if "course" not in self._results_fields or "id" not in self._results_fields:
            return None

        return u"/courses/{course_id}/jump_to/{location}".format(
            course_id=self._results_fields["course"],
            location=self._results_fields["id"],
        )

@csrf_exempt
def do_search(request, course_id=None):
    results = {
        "error": _("Nothing to search")
    }
    status_code = 500

    try:
        if request.method == 'POST':
            search_terms = request.POST["search_string"]

            field_dictionary = None
            if course_id:
                field_dictionary = {"course": course_id}
            searcher = SearchEngine.get_search_engine("courseware_index")
            results = searcher.search_string(
                search_terms, field_dictionary=field_dictionary)

            # post-process the result
            for result in results["results"]:
                result["data"] = SearchResultProcessor.process_result(result["data"], search_terms, request.user)

            results["access_denied_count"] = len([r for r in results["results"] if r["data"] is None])
            results["results"] = [r for r in results["results"] if r["data"] is not None]

            status_code = 200
    except Exception as err:
        results = {
            "error": str(err)
        }

    return HttpResponse(
        json.dumps(results),
        content_type='application/json',
        status=status_code
    )

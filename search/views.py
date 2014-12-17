""" handle requests for courseware search http requests """
import json
import re

from django.http import HttpResponse
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_exempt

from manager import SearchEngine


DESIRED_EXCERPT_LENGTH = 100
ELLIPSIS = "&hellip;"


class SearchResultProcessor(object):

    _results_fields = {}

    def __init__(self, dictionary):
        self._results_fields = dictionary

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
                return matches
        return matches

    @staticmethod
    def boldface_matches(match_in, match_word):
        matches = re.finditer(match_word, match_in, re.IGNORECASE)
        for matched_string in set([match.group() for match in matches]):
            match_in = match_in.replace(matched_string, "<b>{}</b>".format(matched_string))
        return match_in

    def excerpt(self, match_phrase):

        match_words = [match_phrase]
        separate_words = match_phrase.split(' ')
        if len(separate_words) > 1:
            match_words.extend(match_phrase.split(' '))

        matches = SearchResultProcessor.find_matches(
            SearchResultProcessor.strings_in_dictionary(self._results_fields["content"]),
            match_words,
            DESIRED_EXCERPT_LENGTH
        )
        excerpt_text = '...'.join(matches)
        if len(matches) == 1 and len(matches[0]) > DESIRED_EXCERPT_LENGTH:
            # find first match and position
            excerpt_text = matches[0]

            word_at = -1
            word_index = 0
            while word_at < 0 and word_index < len(match_words):
                word_at = excerpt_text.lower().find(match_words[word_index].lower())
                word_index += 1

            start_index = (word_at - DESIRED_EXCERPT_LENGTH / 2)
            if start_index < 0:
                start_index = 0
            end_index = (word_at + DESIRED_EXCERPT_LENGTH / 2)
            if end_index >= len(excerpt_text):
                end_index = -1

            excerpt_text = "{}{}{}".format(
                "" if start_index < 1 else ELLIPSIS,
                excerpt_text[start_index:end_index],
                "" if end_index < 0 else ELLIPSIS,
            )

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

            # update the data with the right url
            for result_data in [result["data"] for result in results["results"]]:
                result_info = SearchResultProcessor(result_data)
                result_data["excerpt"] = result_info.excerpt(search_terms)
                result_data["url"] = result_info.url

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

# -*- coding: utf-8 -*-
""" Tests for result processors """

import ddt

from django.test import TestCase
from django.test.utils import override_settings
from search.result_processor import SearchResultProcessor


# Any class that inherits from TestCase will cause too-many-public-methods pylint error
# pylint: disable=too-many-public-methods
@ddt.ddt
class SearchResultProcessorTests(TestCase):
    """ Tests to check SearchResultProcessor is working as desired """

    def test_strings_in_dictionary(self):
        """ Test finding strings within dictionary item """
        test_dict = {
            "a": "This is a string that should show up"
        }

        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 1)
        self.assertEqual(get_strings[0], test_dict["a"])

        test_dict.update({
            "b": "This is another string that should show up"
        })
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 2)
        self.assertIn(test_dict["a"], get_strings)
        self.assertIn(test_dict["b"], get_strings)

        test_dict.update({
            "CASCADE": {
                "z": "This one should be found too"
            }
        })
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 3)
        self.assertIn(test_dict["a"], get_strings)
        self.assertIn(test_dict["b"], get_strings)
        self.assertIn(test_dict["CASCADE"]["z"], get_strings)

        test_dict.update({
            "DEEP": {
                "DEEPER": {
                    "STILL_GOING": {
                        "MORE": {
                            "here": "And here, again and again"
                        }
                    }
                }
            }
        })
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 4)
        self.assertIn(test_dict["a"], get_strings)
        self.assertIn(test_dict["b"], get_strings)
        self.assertIn(test_dict["CASCADE"]["z"], get_strings)
        self.assertIn(test_dict["DEEP"]["DEEPER"]["STILL_GOING"]["MORE"]["here"], get_strings)

    def test_find_matches(self):
        """ test finding matches """
        words = ["hello"]
        strings = [
            "hello there",
            "goodbye",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(matches, [strings[0], strings[2]])

        words = ["hello", "there"]
        strings = [
            "hello there",
            "goodbye",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(matches, [strings[0], strings[2]])

        words = ["hello", "there"]
        strings = [
            "hello there",
            "goodbye there",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(matches, strings)

        words = ["goodbye there", "goodbye", "there"]
        strings = [
            "goodbye",
            "goodbye there",
            "Sail away to say GOODBYE",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(matches, strings)

        words = ["none of these are present"]
        strings = [
            "goodbye",
            "goodbye there",
            "Sail away to say GOODBYE",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 0)

    def test_too_long_find_matches(self):
        """ make sure that we keep the expert snippets short enough """
        words = ["edx", "afterward"]
        strings = [
            ("Here is a note about edx and it is very long - more than the desirable length of 100 characters"
             " - indeed this should show up"),
            "This matches too but comes afterward",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 1)

    def test_excerpt(self):
        """ test that we return an excerpt """
        test_result = {
            "content": {
                "notes": u"Here is a الاستحسان about edx",
                "name": "edX search a lot",
            }
        }
        srp = SearchResultProcessor(test_result, u"الاستحسان")
        self.assertEqual(srp.excerpt, u"Here is a <b>الاستحسان</b> about edx")

        srp = SearchResultProcessor(test_result, u"edx")
        self.assertIn(
            u"Here is a الاستحسان about <b>edx</b>",
            srp.excerpt
        )

    def test_too_long_excerpt(self):
        """ test that we shorten an excerpt that is too long appropriately """
        test_string = (
            u"Here is a note about الاستحسان and it is very long - more than the desirable length of 100"
            u" characters - indeed this should show up but it should trim the characters around in"
            u" order to show the selected text in bold"
        )
        test_result = {
            "content": {
                "notes": test_string,
            }
        }
        srp = SearchResultProcessor(test_result, u"الاستحسان")
        test_string_compare = SearchResultProcessor.decorate_matches(test_string, u"الاستحسان")
        excerpt = srp.excerpt
        self.assertNotEqual(excerpt, test_string_compare)
        self.assertIn(u"note about <b>الاستحسان</b> and it is", excerpt)

        test_string = (
            u"Here is a note about stuff and it is very long - more than the desirable length of 100"
            u" characters - indeed this should show up but it should trim the الاستحسان characters around in"
            u" order to show the selected text in bold"
        )
        test_result = {
            "content": {
                "notes": test_string,
            }
        }
        srp = SearchResultProcessor(test_result, u"الاستحسان")
        test_string_compare = SearchResultProcessor.decorate_matches(test_string, u"الاستحسان")
        excerpt = srp.excerpt
        self.assertNotEqual(excerpt, test_string_compare)
        self.assertIn(u"should trim the <b>الاستحسان</b> characters around", excerpt)

    def test_excerpt_front(self):
        """ test that we process correctly when match is at the front of the excerpt """
        test_result = {
            "content": {
                "notes": "Dog - match upon first word",
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt, "<b>Dog</b> - match upon first word")

        test_result = {
            "content": {
                "notes": (
                    "Dog - match upon first word "
                    "The long and winding road "
                    "That leads to your door "
                    "Will never disappear ..."
                ),
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt[0:34], "<b>Dog</b> - match upon first word")

    def test_excerpt_back(self):
        """ test that we process correctly when match is at the end of the excerpt """
        test_result = {
            "content": {
                "notes": "Match upon last word - Dog",
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt, "Match upon last word - <b>Dog</b>")

        test_result = {
            "content": {
                "notes": (
                    "The long and winding road "
                    "That leads to your door ..."
                    "... Yeah, yeah, yeah, yeah "
                    "Match upon last word - Dog"
                ),
            }
        }
        srp = SearchResultProcessor(test_result, "dog")
        self.assertEqual(srp.excerpt[-33:], "Match upon last word - <b>Dog</b>")

    @ddt.data(
        (u'"never disappear"', u"leads to your door Will <b>never disappear</b>"),
        (u'"I\'ve seen"', u"<b>I've seen</b> that road before It always"),
        (
            u'"long and winding" leads',
            u'The <b>long and winding</b> road That <b>leads</b> to your door'
        ),
        (u'"search"', u"इसको <b>search</b> करें| Lead"),
        (u'"हिंदी में"', u"It always leads me here यह एक <b>हिंदी में</b>"),
        (u'"इसको search"', u"वाक्य है| <b>इसको search</b> करें| Lead me"),
        # Match at the beginning
        (u'"The long"', u'<b>The long</b> and winding road That'),
        # Match at the end
        (u'"rain washed away"', u'windy night That the <b>rain washed away</b>'),
    )
    @ddt.unpack
    def test_excerpt_quoted(self, search_phrase, expected_excerpt):
        test_result = {
            "content": {
                "notes": (
                    u"The long and winding road "
                    u"That leads to your door "
                    u"Will never disappear "
                    u"I've seen that road before "
                    u"It always leads me here "
                    u"यह एक हिंदी में लिखा हुआ वाक्य है| इसको search करें| "
                    u"Lead me to you door "
                    u"The wild and windy night "
                    u"That the rain washed away "
                ),
            }
        }
        srp = SearchResultProcessor(test_result, search_phrase)
        self.assertIn(expected_excerpt, srp.excerpt)


class TestSearchResultProcessor(SearchResultProcessor):
    """
    Override the SearchResultProcessor so that we get the additional (inferred) properties
    and can identify results that should be removed due to access restriction
    """
    # pylint: disable=no-self-use
    @property
    def additional_property(self):
        """ additional property that should appear within processed results """
        return "Should have an extra value"

    @property
    def url(self):
        """
        Property to display the url for the given location, useful for allowing navigation
        """
        if "course" not in self._results_fields or "id" not in self._results_fields:
            raise ValueError("expect this error when not providing a course and/or id")

        return u"/courses/{course_id}/jump_to/{location}".format(
            course_id=self._results_fields["course"],
            location=self._results_fields["id"],
        )

    def should_remove(self, user):
        """ remove items when url is None """
        return "remove_me" in self._results_fields


@override_settings(SEARCH_RESULT_PROCESSOR="search.tests.test_search_result_processor.TestSearchResultProcessor")
class TestOverrideSearchResultProcessor(TestCase):
    """ test the correct processing of results using the SEARCH_RESULT_PROCESSOR specified class """

    def test_additional_property(self):
        """ make sure the addition properties are returned """
        test_result = {
            "course": "testmetestme",
            "id": "herestheid"
        }
        new_result = SearchResultProcessor.process_result(test_result, "fake search pattern", None)
        self.assertEqual(new_result, test_result)
        self.assertEqual(test_result["url"], "/courses/testmetestme/jump_to/herestheid")
        self.assertIsNone(test_result["excerpt"])
        self.assertEqual(test_result["additional_property"], "Should have an extra value")

    def test_removal(self):
        """ make sure that the override of should remove let's the application prevent access to a result """
        test_result = {
            "course": "remove_course",
            "id": "remove_id",
            "remove_me": True
        }
        new_result = SearchResultProcessor.process_result(test_result, "fake search pattern", None)
        self.assertIsNone(new_result)

    def test_property_error(self):
        """ result should be removed from list if there is an error in the handler properties """
        test_result = {
            "not_course": "asdasda",
            "not_id": "rthrthretht"
        }
        new_result = SearchResultProcessor.process_result(test_result, "fake search pattern", None)
        self.assertIsNone(new_result)

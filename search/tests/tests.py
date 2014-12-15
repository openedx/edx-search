from django.test import TestCase
from django.test.utils import override_settings
from elasticsearch import Elasticsearch
from nose.tools import set_trace

from search.manager import SearchEngine
from search.elastic import ElasticSearchEngine

from .mock_search_engine import MockSearchEngine
from search.views import SearchResultProcessor

TEST_INDEX_NAME = "test_index"

# We override ElasticSearchEngine class in order to force an index refresh upon index
# otherwise we often get results from the prior state, rendering the tests less useful


class ForceRefreshElasticSearchEngine(ElasticSearchEngine):

    def index(self, doc_type, body, **kwargs):
        kwargs.update({
            "refresh": True
        })
        super(ForceRefreshElasticSearchEngine, self).index(doc_type, body, **kwargs)

TEST_ENGINE = MockSearchEngine
# Uncomment below in order to test against Elastic Search installation
# TEST_ENGINE = ForceRefreshElasticSearchEngine


@override_settings(SEARCH_ENGINE=TEST_ENGINE)
class ElasticSearchTests(TestCase):

    _searcher = None

    @property
    def searcher(self):
        if self._searcher is None:
            self._searcher = SearchEngine.get_search_engine(TEST_INDEX_NAME)
        return self._searcher

    @property
    def _is_elastic(self):
        return isinstance(self.searcher, ElasticSearchEngine)

    def setUp(self):
        if self._is_elastic:
            es = Elasticsearch()
            # Make sure that we are fresh
            es.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])

            config_body = {}
            es.indices.create(index=TEST_INDEX_NAME, ignore=400, body=config_body)
        self._searcher = None

    def tearDown(self):
        if self._is_elastic:
            es = Elasticsearch()
            es.indices.delete(index=TEST_INDEX_NAME, ignore=[400, 404])
        self._searcher = None

    def test_factory_creator(self):
        self.assertTrue(isinstance(self.searcher, SearchEngine))

    def test_abstract_impl(self):
        abstract = SearchEngine("test_index_name")
        test_string = "A test string"
        abstract.index("test_doc", {"name": test_string})
        results = abstract.search(test_string)
        self.assertFalse(results)

    def test_find_all(self):
        test_string = "A test string"
        self.searcher.index("test_doc", {"name": test_string})

        # search everything
        response = self.searcher.search(None)
        self.assertEqual(response["total"], 1)
        results = response["results"]
        self.assertEqual(results[0]["data"]["name"], test_string)

        self.searcher.index("not_test_doc", {"value": test_string})

        response = self.searcher.search(None)
        self.assertEqual(response["total"], 2)
        results = response["results"]
        test_0 = results[0]["data"] if "name" in results[0]["data"] else results[1]["data"]
        test_1 = results[1]["data"] if "name" in results[0]["data"] else results[0]["data"]
        self.assertEqual(test_0["name"], test_string)
        self.assertEqual(test_1["value"], test_string)

    def test_find_doctype(self):
        test_string = "A test string"
        self.searcher.index("test_doc", {"name": test_string})

        # search by doc_type
        response = self.searcher.search(None, doc_type="test_doc")
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(None, doc_type="not_test_doc")
        self.assertEqual(response["total"], 0)

        self.searcher.index("not_test_doc", {"value": test_string})

        response = self.searcher.search(None, doc_type="not_test_doc")
        self.assertEqual(response["total"], 1)

    def test_find_string(self):
        test_string = "A test string"
        self.searcher.index("test_doc", {"name": test_string})

        # search string
        response = self.searcher.search(test_string)
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 1)

        self.searcher.index("not_test_doc", {"value": test_string})

        response = self.searcher.search_string(test_string)
        self.assertEqual(response["total"], 2)

    def test_field(self):
        test_string = "A test string"
        test_object = {
            "name": test_string,
            "tags": {
                "tag_one": "one",
                "tag_two": "two"
            },
            "fieldX": "valueY",
            "id": "12345"
        }
        self.searcher.index("test_doc", test_object)

        # search tags
        response = self.searcher.search_fields({"tags.tag_one": "one"})
        self.assertEqual(response["total"], 1)

        # search id
        response = self.searcher.search_fields({"id": "12345"})
        self.assertEqual(response["total"], 1)

        # search id
        response = self.searcher.search_fields({"id": "54321"})
        self.assertEqual(response["total"], 0)

        # search tags
        response = self.searcher.search_fields({"tags.tag_one": "one", "tags.tag_two": "two"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"fieldX": "valueY"})
        self.assertEqual(response["total"], 1)

        # search tags
        response = self.searcher.search_fields({"tags.tag_one": "one", "tags.tag_two": "not_two"})
        self.assertEqual(response["total"], 0)

    def test_search_string_and_field(self):
        test_object = {
            "name": "You may find me in a coffee shop",
            "course_id": "A/B/C",
            "abc": "xyz",
        }
        self.searcher.index("test_doc", test_object)

        response = self.searcher.search(query_string="find me")
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"course_id": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search(query_string="find me", field_dictionary={"course_id": "X/Y/Z"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(query_string="find me", field_dictionary={"course_id": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_string("find me", field_dictionary={"course_id": "A/B/C"})
        self.assertEqual(response["total"], 1)

        response = self.searcher.search_fields({"course_id": "A/B/C"}, query_string="find me")
        self.assertEqual(response["total"], 1)

    def test_search_tags(self):
        test_object = {
            "name": "John Lester",
            "course_id": "A/B/C",
            "abc": "xyz"
        }
        tags = {
            "color": "red",
            "shape": "square",
            "taste": "sour",
        }
        test_object["tags"] = tags
        self.searcher.index("test_doc", test_object)

        response = self.searcher.search_fields({"tags.color": "red"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self.searcher.search(field_dictionary={"tags.color": "red"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self.searcher.search(field_dictionary={"tags.color": "blue"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(field_dictionary={"tags.shape": "square"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self.searcher.search(field_dictionary={"tags.shape": "round"})
        self.assertEqual(response["total"], 0)

        response = self.searcher.search(field_dictionary={"tags.shape": "square", "tags.color": "red"})
        self.assertEqual(response["total"], 1)
        result = response["results"][0]["data"]
        self.assertEqual(result["tags"]["color"], "red")
        self.assertEqual(result["tags"]["shape"], "square")
        self.assertEqual(result["tags"]["taste"], "sour")

        response = self._searcher.search(field_dictionary={"tags.shape": "square", "tags.color": "blue"})
        self.assertEqual(response["total"], 0)

class SearchResultProcessorTests(TestCase):

    def test_strings_in_dictionary(self):
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
        self.assertEqual(get_strings[0], test_dict["a"])
        self.assertEqual(get_strings[1], test_dict["b"])

        test_dict.update({
            "CASCADE": {
                "z": "This one should be found too"
            }
        })
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict)
        self.assertEqual(len(get_strings), 3)
        self.assertEqual(get_strings[0], test_dict["a"])
        self.assertEqual(get_strings[1], test_dict["b"])
        self.assertEqual(get_strings[2], test_dict["CASCADE"]["z"])

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
        self.assertEqual(get_strings[0], test_dict["a"])
        self.assertEqual(get_strings[1], test_dict["b"])
        self.assertEqual(get_strings[2], test_dict["CASCADE"]["z"])
        self.assertEqual(get_strings[3], test_dict["DEEP"]["DEEPER"]["STILL_GOING"]["MORE"]["here"])

        test_dict = {
            "id": "excluded",
            "content_type": "excluded",
            "xblock_keywords": "excluded",
            "name": "not excluded",
            "url": "excluded",
        }

        excluded_fields = ["id", "content_type", "xblock_keywords", "url"]
        get_strings = SearchResultProcessor.strings_in_dictionary(test_dict, excluded_fields)
        self.assertEqual(len(get_strings), 1)


    def test_find_matches(self):
        words = ["hello"]
        strings = [
            "hello there",
            "goodbye",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0], strings[0])
        self.assertEqual(matches[1], strings[2])

        words = ["hello", "there"]
        strings = [
            "hello there",
            "goodbye",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0], strings[0])
        self.assertEqual(matches[1], strings[2])

        words = ["hello", "there"]
        strings = [
            "hello there",
            "goodbye there",
            "Sail away to say HELLO",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0], strings[0])
        self.assertEqual(matches[1], strings[2])
        self.assertEqual(matches[2], strings[1])

        words = ["goodbye there", "goodbye", "there"]
        strings = [
            "goodbye",
            "goodbye there",
            "Sail away to say GOODBYE",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0], strings[1])
        self.assertEqual(matches[1], strings[0])
        self.assertEqual(matches[2], strings[2])

    def test_too_long_find_matches(self):
        words = ["edx", "afterward"]
        strings = [
            "Here is a note about edx and it is very long - more than the desirable length of 100 characters - indeed this should show up",
            "This matches too but comes afterward",
        ]
        matches = SearchResultProcessor.find_matches(strings, words, 100)
        self.assertEqual(len(matches), 1)

    def test_url(self):
        test_result = {
            "course": "testmetestme",
            "id": "herestheid"
        }
        srp = SearchResultProcessor(test_result)
        self.assertEqual(srp.url, "/courses/testmetestme/jump_to/herestheid")

        srp = SearchResultProcessor({"course": "testmetestme"})
        self.assertEqual(srp.url, None)

        srp = SearchResultProcessor({"id": "herestheid"})
        self.assertEqual(srp.url, None)

        srp = SearchResultProcessor({"something_else": "altogether"})
        self.assertEqual(srp.url, None)

    def test_excerpt(self):
        test_result = {
            "notes": "Here is a note about edx",
            "name": "edX search a lot",
        }
        srp = SearchResultProcessor(test_result)
        edx_excerpt = srp.excerpt("note")
        self.assertEqual(edx_excerpt, "Here is a <b>note</b> about edx")

        edx_excerpt = srp.excerpt("edx")
        self.assertEqual(edx_excerpt, "Here is a note about <b>edx</b>...<b>edX</b> search a lot")

    def test_too_long_excerpt(self):
        test_result = {
            "notes": "Here is a note about edx and it is very long - more than the desirable length of 100 characters - indeed this should show up but it should trim the characters around in order to show the selected text in bold",
        }
        srp = SearchResultProcessor(test_result)
        edx_excerpt = srp.excerpt("edx")
        self.assertNotEqual(edx_excerpt, "Here is a note about <b>edx</b> and it is very long - more than the desirable length of 100 characters - indeed this should show up but it should trim the characters around in order to show the selected text in bold")
        self.assertTrue("note about <b>edx</b> and it is" in edx_excerpt)

        test_result = {
            "notes": "Here is a note about stuff and it is very long - more than the desirable length of 100 characters - indeed this should show up but it should trim the edx characters around in order to show the selected text in bold",
        }
        srp = SearchResultProcessor(test_result)
        edx_excerpt = srp.excerpt("edx")
        self.assertNotEqual(edx_excerpt, "Here is a note about stuff and it is very long - more than the desirable length of 100 characters - indeed this should show up but it should trim the edx characters around in order to show the selected text in bold")
        self.assertTrue("should trim the <b>edx</b> characters around" in edx_excerpt)
